"""One masking session: the clip, the tracker, the user's marks, the masks and the jobs.

Both the web UI and headless mode drive this object.
"""

from __future__ import annotations

import itertools
import json
import os
import tempfile
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

from .refine import RefineSettings, auto_refine_radius, finish_matte, temporal_soft_mask
from .tracker import MODELS, MaskStore, PromptSet, apply_prompts, track
from .video_io import default_name, extract_frames, imread, probe, safe_remove

# Object colours (RGB) — shared with the UI.
OBJECT_COLORS = [
    (0, 0, 0),
    (124, 92, 255),
    (0, 214, 201),
    (255, 107, 107),
    (255, 196, 61),
    (77, 171, 247),
    (240, 101, 200),
    (132, 230, 112),
    (255, 146, 43),
    (180, 180, 200),
]


class Busy(RuntimeError):
    """Raised when an action needs the tracker while a job is using it."""


class Job:
    _ids = itertools.count(1)

    def __init__(self, kind: str):
        self.id = next(Job._ids)
        self.kind = kind
        self.progress = 0.0
        self.frame = None
        self.message = ""
        self.error = None
        self.done = False
        self.result = None
        self.cancel = False
        self.started = time.time()
        self.finished = None

    def to_dict(self):
        return {
            "kind": self.kind,
            "progress": round(self.progress, 4),
            "frame": self.frame,
            "message": self.message,
            "error": self.error,
            "done": self.done,
            "result": self.result,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "id": self.id,
        }


def default_engine_factory(frames_dir: str, model: str, device: str):
    custom = os.environ.get("FREEMASK_ENGINE")  # "module:Class" — used by the test-suite
    if custom:
        import importlib

        mod, _, attr = custom.partition(":")
        return getattr(importlib.import_module(mod), attr)(frames_dir, model, device)
    from .tracker import Sam2Engine

    return Sam2Engine(frames_dir, model, device)


class Session:
    def __init__(self, model: str = "small", device: str = "auto", quality: int = 1024,
                 engine_factory=default_engine_factory, formats=("png", "cutout", "video")):
        if model not in MODELS:
            raise ValueError(f"Unknown model {model!r}")
        self.model = model
        self.device = device
        self.quality = quality
        self.engine_factory = engine_factory
        self.formats = set(formats)

        self.status = "empty"  # empty | loading | ready | error
        self.error = None
        self.info = None
        self.engine = None
        self.prompts = PromptSet()
        self.store = None
        self.settings = RefineSettings()
        self.source = None
        self.name = None
        self.out_dir = None
        self.start_frame = 0  # UI frame to show first (relative)
        self.mask_rev = 0  # bumps whenever masks change
        self.stale = False
        self.has_tracked = False  # a track has run since the clip was opened
        self.last_export = None
        self.job: Job | None = None

        self._work = None
        self._lock = threading.RLock()  # guards the engine

    # ------------------------------------------------------------------ clip

    def open_clip(self, source: str, start: int = 0, end: int | None = None, fps: float | None = None,
                  out_dir: str | None = None, name: str | None = None, frame: int | None = None,
                  session_file: str | None = None, wait: bool = False):
        src = Path(source).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"Clip not found: {src}")
        if self.job and not self.job.done:
            raise Busy("Still working — wait for the current job to finish.")
        self._release()
        self.source = str(src)
        self.name = name or default_name(self.source)
        self.out_dir = Path(out_dir) if out_dir else src.parent / f"{self.name}_freemask"
        self.status = "loading"
        self.error = None
        self.prompts = PromptSet()
        self.last_export = None
        self.stale = False
        self.has_tracked = False

        def run(job: Job):
            self._work = tempfile.mkdtemp(prefix="freemask_")
            frames_dir = str(Path(self._work) / "frames")
            job.message = "Reading frames"
            try:
                n_src = probe(self.source, fps)[3]
            except Exception:
                n_src = 0
            last = end if end is not None else n_src - 1
            total = max(0, last - start + 1) or None

            def prog(n):
                job.frame = n - 1
                if total:
                    job.progress = min(0.6, 0.6 * n / total)
                job.message = f"Reading frames · {n}"
                if job.cancel:
                    raise InterruptedError

            info = extract_frames(self.source, frames_dir, max_side=self.quality, start=start, end=end,
                                  fps_override=fps, progress=prog)
            job.progress = 0.65
            job.message = f"Loading SAM 2.1 {self.model} model"
            engine = self.engine_factory(frames_dir, self.model, self.device)
            with self._lock:
                self.info = info
                self.frames_dir = frames_dir
                self.engine = engine
                self.store = MaskStore(info.frame_count, info.proc_height, info.proc_width)
                if session_file:
                    self._load_session_file(session_file)
                if frame is not None:
                    self.start_frame = int(np.clip(frame - info.first_frame, 0, info.frame_count - 1))
                elif self.prompts.frames():
                    self.start_frame = self.prompts.frames()[0]
                else:
                    self.start_frame = 0
                self.status = "ready"
            job.progress = 1.0
            job.message = "Ready"
            return {"frames": info.frame_count}

        def on_error(exc):
            self.status = "error"
            self.error = str(exc)

        return self._start_job("load", run, on_error=on_error, wait=wait)

    def _load_session_file(self, path: str):
        data = json.loads(Path(path).read_text())
        to_proc = self.info.proc_width / self.info.width
        self.prompts = PromptSet.from_json(data, to_proc, self.info.first_frame)
        if "refine" in data:
            self.settings = RefineSettings.from_dict(data["refine"])
        self.store.hidden = set(data.get("hidden", []))
        if self.prompts.nonempty():
            apply_prompts(self.engine, self.prompts, self.store)
            self.stale = True
            self.mask_rev += 1

    def _release(self):
        with self._lock:
            self.engine = None
            self.info = None
            self.store = None
        if self._work:
            safe_remove(self._work)
            self._work = None

    def close(self):
        if self.job and not self.job.done:
            self.job.cancel = True
            deadline = time.time() + 10
            while not self.job.done and time.time() < deadline:
                time.sleep(0.05)
        self._release()

    # ------------------------------------------------------------------ jobs

    def _start_job(self, kind, fn, on_error=None, wait=False):
        if self.job and not self.job.done:
            raise Busy("Still working — wait for the current job to finish.")
        job = Job(kind)
        self.job = job

        def runner():
            try:
                job.result = fn(job)
            except InterruptedError:
                job.message = "Cancelled"
                job.error = None
                if on_error and kind == "load":
                    on_error(RuntimeError("Cancelled"))
            except Exception as exc:  # surfaced to the UI
                traceback.print_exc()
                job.error = _friendly(exc)
                if on_error:
                    on_error(job.error)
            finally:
                job.finished = time.time()
                job.done = True

        if wait:
            runner()
        else:
            threading.Thread(target=runner, daemon=True, name=f"freemask-{kind}").start()
        return job

    def cancel(self):
        if self.job and not self.job.done:
            self.job.cancel = True

    def _require_ready(self):
        if self.status != "ready":
            raise Busy("Open a clip first.")
        if self.job and not self.job.done and self.job.kind in ("track", "load"):
            raise Busy("Tracking is running — stop it or wait for it to finish.")

    # ------------------------------------------------------------------ marks

    def _apply(self, frame: int, obj: int):
        p = self.prompts.get(frame, obj)
        with self._lock:
            masks = self.engine.set_prompt(frame, obj, p)
            if p.empty():
                self.store.drop(frame, obj)
                if obj not in self.prompts.objects():
                    self.engine.remove_object(obj)
                    self.store.drop_object(obj)
            for o, m in masks.items():
                self.store.set(frame, o, m)
        self.stale = True
        self.mask_rev += 1

    def _check(self, frame: int, obj: int):
        if not 0 <= frame < self.info.frame_count:
            raise ValueError(f"Frame {frame} out of range")
        if not 1 <= obj <= 9:
            raise ValueError("Object must be 1-9")

    def add_point(self, frame: int, obj: int, x: float, y: float, label: int):
        self._require_ready()
        self._check(frame, obj)
        x = float(np.clip(x, 0, self.info.proc_width - 1))
        y = float(np.clip(y, 0, self.info.proc_height - 1))
        p = self.prompts.get(frame, obj)
        p.points.append([x, y])
        p.labels.append(1 if label else 0)
        self._apply(frame, obj)

    def set_box(self, frame: int, obj: int, x1, y1, x2, y2):
        self._require_ready()
        self._check(frame, obj)
        w, h = self.info.proc_width - 1, self.info.proc_height - 1
        xa, xb = sorted([float(np.clip(x1, 0, w)), float(np.clip(x2, 0, w))])
        ya, yb = sorted([float(np.clip(y1, 0, h)), float(np.clip(y2, 0, h))])
        if xb - xa < 2 or yb - ya < 2:
            raise ValueError("Box is too small")
        self.prompts.get(frame, obj).box = [xa, ya, xb, yb]
        self._apply(frame, obj)

    def undo(self, frame: int, obj: int):
        self._require_ready()
        self._check(frame, obj)
        p = self.prompts.get(frame, obj)
        if p.points:
            p.points.pop()
            p.labels.pop()
        elif p.box is not None:
            p.box = None
        else:
            return
        self._apply(frame, obj)

    def clear(self, frame: int, obj: int):
        self._require_ready()
        self._check(frame, obj)
        p = self.prompts.get(frame, obj)
        if p.empty():
            return
        p.points, p.labels, p.box = [], [], None
        self._apply(frame, obj)

    def set_hidden(self, obj: int, hidden: bool):
        self._require_ready()
        (self.store.hidden.add if hidden else self.store.hidden.discard)(obj)
        self.mask_rev += 1

    def update_settings(self, values: dict):
        merged = self.settings.to_dict()
        for k, v in values.items():
            if k not in merged:
                raise ValueError(f"Unknown setting {k}")
            merged[k] = type(merged[k])(v) if not isinstance(merged[k], bool) else bool(v)
        self.settings = RefineSettings.from_dict(merged)

    # ------------------------------------------------------------------ tracking / export

    def track(self, direction: str = "both", wait: bool = False):
        self._require_ready()
        if direction not in ("both", "forward", "backward"):
            raise ValueError("direction must be both, forward or backward")
        if not self.prompts.frames():
            raise ValueError("Click on what you want to mask first.")

        def run(job: Job):
            n = self.info.frame_count
            frames = self.prompts.frames()
            back = frames[0] + 1 if frames[0] > 0 else 0  # SAM 2 skips a backward pass from frame 0
            total = {"both": (n - frames[0]) + back,
                     "forward": n - frames[0], "backward": frames[-1] + 1}[direction]
            job.message = "Tracking"

            def prog(frame, done, _masks):
                job.frame = frame
                job.progress = min(1.0, done / max(total, 1))
                self.mask_rev += 1

            with self._lock:
                finished = track(self.engine, self.prompts, self.store, direction, prog,
                                 should_stop=lambda: job.cancel)
            self.mask_rev += 1
            self.has_tracked = True
            if finished:
                self.stale = False
                job.message = "Tracked"
            else:
                job.message = "Stopped"
            return {"finished": finished}

        return self._start_job("track", run, wait=wait)

    def export(self, formats=None, wait: bool = False):
        self._require_ready()
        if not self.store.tracked_count():
            raise ValueError("Nothing to export yet — mark the subject and track it first.")
        formats = set(formats or self.formats)
        bad = formats - {"png", "cutout", "video"}
        if bad or not formats:
            raise ValueError(f"Unknown export format(s): {sorted(bad)}")
        settings = RefineSettings.from_dict(self.settings.to_dict())

        def run(job: Job):
            from .export import export_mattes

            job.message = "Rendering mattes"

            def prog(i, n):
                job.frame = i - 1
                job.progress = i / n
                if job.cancel:
                    raise InterruptedError

            paths = export_mattes(self.info, self.store, settings, str(self.out_dir), self.name,
                                  formats, progress=prog)
            self.last_export = {k: str(v) for k, v in paths.items()}
            self.save_session()
            job.message = "Exported"
            return self.last_export

        return self._start_job("export", run, wait=wait)

    def save_session(self) -> str:
        if self.status != "ready":
            raise Busy("Open a clip first.")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{self.name}_session.json"
        data = self.prompts.to_json(self.info.width / self.info.proc_width, self.info.first_frame)
        data["refine"] = self.settings.to_dict()
        data["hidden"] = sorted(self.store.hidden)
        data["source"] = self.source
        path.write_text(json.dumps(data, indent=2))
        return str(path)

    # ------------------------------------------------------------------ images for the UI

    def frame_path(self, i: int) -> Path:
        return Path(self.frames_dir) / f"{int(i):05d}.jpg"

    def overlay_png(self, i: int, selected: int = 0) -> bytes:
        """Coloured RGBA overlay of every object's mask on frame ``i``."""
        self._require_image(i)
        h, w = self.info.proc_height, self.info.proc_width
        rgba = np.zeros((h, w, 4), np.uint8)
        for obj in self.store.objects_at(i):
            m = self.store.get_obj(i, obj)
            if m is None or not m.any():
                continue
            r, g, b = OBJECT_COLORS[obj % len(OBJECT_COLORS)]
            hidden = obj in self.store.hidden
            alpha = 35 if hidden else (105 if obj == selected or not selected else 75)
            rgba[m] = (r, g, b, alpha)
            edge = m & ~cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            edge = cv2.dilate(edge.astype(np.uint8), np.ones((2, 2), np.uint8)).astype(bool)
            rgba[edge] = (r, g, b, 120 if hidden else 255)
        return _png(cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))

    def matte_png(self, i: int) -> bytes:
        """Refined matte preview at tracking resolution, as white with the matte in alpha."""
        self._require_image(i)
        s = RefineSettings.from_dict(self.settings.to_dict())
        ratio = self.info.proc_width / self.info.width
        s.grow = int(round(s.grow * ratio))
        s.feather = s.feather * ratio
        if s.smart_refine < 0:
            s.smart_refine = auto_refine_radius(self.info.width, self.info.height)
        s.smart_refine = max(1, int(round(s.smart_refine * ratio))) if s.smart_refine else 0
        soft = temporal_soft_mask(self.store.combined, i, self.info.frame_count, s)
        if soft is None:
            soft = np.zeros((self.info.proc_height, self.info.proc_width), np.float32)
        guide = imread(self.frame_path(i))
        matte = finish_matte(soft, s, (self.info.proc_width, self.info.proc_height), guide)
        a = np.round(matte * 255).astype(np.uint8)
        white = np.full_like(a, 255)
        return _png(np.dstack([white, white, white, a]))

    def _require_image(self, i):
        if self.status != "ready":
            raise Busy("No clip loaded")
        if not 0 <= i < self.info.frame_count:
            raise ValueError("Frame out of range")

    # ------------------------------------------------------------------ state for the UI

    def snapshot(self) -> dict:
        info = self.info
        out = {
            "status": self.status,
            "error": self.error,
            "model": self.model,
            "device": getattr(self.engine, "device", None),
            "source": self.source,
            "name": self.name,
            "out_dir": str(self.out_dir) if self.out_dir else None,
            "job": self.job.to_dict() if self.job else None,
            "settings": self.settings.to_dict(),
            "auto_refine": auto_refine_radius(info.width, info.height) if info else None,
            "formats": sorted(self.formats),
            "colors": OBJECT_COLORS,
            "last_export": self.last_export,
        }
        if info and self.store is not None and self.status == "ready":
            out.update(
                clip={
                    "frames": info.frame_count,
                    "first_frame": info.first_frame,
                    "fps": info.fps,
                    "width": info.width,
                    "height": info.height,
                    "proc_width": info.proc_width,
                    "proc_height": info.proc_height,
                },
                start_frame=self.start_frame,
                mask_rev=self.mask_rev,
                stale=self.stale,
                has_tracked=self.has_tracked,
                tracked=_ranges([f for f in range(info.frame_count) if self.store.has(f)]),
                tracked_count=self.store.tracked_count(),
                hidden=sorted(self.store.hidden),
                objects=sorted(set(self.prompts.objects()) | set(self.store.hidden)),
                prompts=[
                    {"frame": f, "object": o, "points": p.points, "labels": p.labels, "box": p.box}
                    for f, o, p in self.prompts.nonempty()
                ],
            )
        return out


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()


def _ranges(frames: list[int]) -> list[list[int]]:
    """[0,1,2,5,6] -> [[0,2],[5,6]] — compact for the timeline."""
    out = []
    for f in frames:
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def _friendly(exc: Exception) -> str:
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if "out of memory" in low or isinstance(exc, MemoryError):
        return ("Ran out of memory. Try a shorter range, a lower Quality setting, "
                "or the 'tiny' model.")
    if "could not open video" in low or "no frames decoded" in low:
        return (f"{msg}. If this is Blackmagic RAW or another camera-raw format, render the "
                "shot to ProRes/DNxHR first.")
    if _is_network_error(exc):
        return ("Couldn't download the SAM 2 model — check your internet connection and try again "
                f"(it's only downloaded once). Details: {msg}")
    return msg


def _is_network_error(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        mod = type(exc).__module__ or ""
        text = f"{type(exc).__name__} {exc}".lower()
        if any(m in mod for m in ("httpx", "httpcore", "requests", "urllib3", "huggingface_hub", "socket")):
            return True
        if isinstance(exc, (ConnectionError, TimeoutError)) or any(
                k in text for k in ("proxyerror", "connecterror", "max retries", "name or service not known",
                                    "getaddrinfo", "network is unreachable", "403 forbidden", "ssl")):
            return True
        exc = exc.__cause__ or exc.__context__
    return False
