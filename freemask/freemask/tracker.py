"""Bidirectional object tracking with Meta's SAM 2 video model.

Magic Mask works by letting you mark what you want on one (or several) frames and then
tracking that selection forwards and backwards through the clip. SAM 2 does exactly that:
it segments from clicks/boxes and propagates the object through the video with a memory
of every frame you have corrected.
"""

from __future__ import annotations

import contextlib
import os
import threading
import warnings
from dataclasses import dataclass, field

import numpy as np

MODELS = {
    "tiny": "facebook/sam2.1-hiera-tiny",
    "small": "facebook/sam2.1-hiera-small",
    "base": "facebook/sam2.1-hiera-base-plus",
    "large": "facebook/sam2.1-hiera-large",
}


# --------------------------------------------------------------------------- prompts


@dataclass
class Prompt:
    """What the user marked for one object on one frame."""

    points: list[list[float]] = field(default_factory=list)  # [[x, y], ...] tracking-res px
    labels: list[int] = field(default_factory=list)  # 1 = include, 0 = exclude
    box: list[float] | None = None  # [x1, y1, x2, y2]

    def empty(self) -> bool:
        return not self.points and self.box is None


class PromptSet:
    """All prompts in a session, keyed by (frame, object id). Saveable to JSON."""

    def __init__(self):
        self.items: dict[tuple[int, int], Prompt] = {}

    def get(self, frame: int, obj: int) -> Prompt:
        return self.items.setdefault((frame, obj), Prompt())

    def frames(self) -> list[int]:
        return sorted({f for (f, _), p in self.items.items() if not p.empty()})

    def objects(self) -> list[int]:
        return sorted({o for (_, o), p in self.items.items() if not p.empty()})

    def nonempty(self):
        return [(f, o, p) for (f, o), p in sorted(self.items.items()) if not p.empty()]

    def to_json(self, scale_to_source: float = 1.0, frame_offset: int = 0) -> dict:
        out = []
        for f, o, p in self.nonempty():
            out.append(
                {
                    "frame": f + frame_offset,
                    "object": o,
                    "points": [[x * scale_to_source, y * scale_to_source] for x, y in p.points],
                    "labels": list(p.labels),
                    "box": None if p.box is None else [v * scale_to_source for v in p.box],
                }
            )
        return {"version": 1, "coords": "source_pixels", "frames": "source_frame_numbers", "prompts": out}

    @classmethod
    def from_json(cls, data: dict, scale_from_source: float = 1.0, frame_offset: int = 0) -> "PromptSet":
        """``frame_offset`` is subtracted from stored frame numbers; prompts outside are dropped."""
        ps = cls()
        for item in data.get("prompts", []):
            frame = int(item["frame"]) - frame_offset
            if frame < 0:
                continue
            p = ps.get(frame, int(item.get("object", 1)))
            p.points = [[x * scale_from_source, y * scale_from_source] for x, y in item.get("points", [])]
            p.labels = [int(v) for v in item.get("labels", [1] * len(p.points))]
            box = item.get("box")
            p.box = None if box is None else [v * scale_from_source for v in box]
        return ps



# --------------------------------------------------------------------------- masks


class MaskStore:
    """Per-frame, per-object boolean masks, bit-packed to keep long clips in RAM."""

    def __init__(self, n_frames: int, height: int, width: int):
        self.n_frames = n_frames
        self.shape = (height, width)
        self._data: dict[int, dict[int, np.ndarray]] = {}
        self.hidden: set[int] = set()  # object ids excluded from the final matte
        self._lock = threading.Lock()  # the UI reads while tracking writes

    def set(self, frame: int, obj: int, mask: np.ndarray):
        packed = np.packbits(mask.astype(bool), axis=None)
        with self._lock:
            self._data.setdefault(frame, {})[obj] = packed

    def get_obj(self, frame: int, obj: int) -> np.ndarray | None:
        with self._lock:
            packed = self._data.get(frame, {}).get(obj)
        if packed is None:
            return None
        h, w = self.shape
        return np.unpackbits(packed, count=h * w).reshape(h, w).astype(bool)

    def objects_at(self, frame: int) -> list[int]:
        with self._lock:
            return sorted(self._data.get(frame, {}))

    def combined(self, frame: int) -> np.ndarray | None:
        """Union of all visible objects on a frame (the final matte)."""
        out = None
        for obj in self.objects_at(frame):
            if obj in self.hidden:
                continue
            m = self.get_obj(frame, obj)
            out = m if out is None else (out | m)
        return out

    def drop(self, frame: int, obj: int):
        with self._lock:
            self._drop(frame, obj)

    def _drop(self, frame: int, obj: int):
        self._data.get(frame, {}).pop(obj, None)
        if frame in self._data and not self._data[frame]:
            del self._data[frame]

    def drop_object(self, obj: int):
        with self._lock:
            for frame in list(self._data):
                self._drop(frame, obj)

    def has(self, frame: int) -> bool:
        return bool(self._data.get(frame))

    def tracked_count(self) -> int:
        return len(self._data)

    def clear(self):
        with self._lock:
            self._data.clear()


# --------------------------------------------------------------------------- engine


def pick_device(requested: str | None = None) -> str:
    import torch

    if requested and requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Sam2Engine:
    """Thin wrapper around ``SAM2VideoPredictor`` so the rest of the app never touches torch."""

    def __init__(self, frames_dir: str, model: str = "small", device: str | None = None):
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        # SAM 2's optional compiled extension isn't installed; its fallback is fine.
        warnings.filterwarnings("ignore", message=".*cannot import name '_C'.*")
        warnings.filterwarnings("ignore", message=".*Skipping the post-processing step.*")
        import torch
        from sam2.sam2_video_predictor import SAM2VideoPredictor

        self.torch = torch
        self.device = pick_device(device)
        model_id = MODELS.get(model, model)
        self.predictor = SAM2VideoPredictor.from_pretrained(model_id, device=self.device)
        self._autocast_dtype = None
        if self.device == "cuda":
            self._autocast_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            if torch.cuda.get_device_properties(0).major >= 8:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
        with self._ctx():
            self.state = self.predictor.init_state(
                video_path=frames_dir,
                offload_video_to_cpu=self.device != "cpu",
                offload_state_to_cpu=False,
            )

    @contextlib.contextmanager
    def _ctx(self):
        with self.torch.inference_mode():
            if self._autocast_dtype is not None:
                with self.torch.autocast("cuda", dtype=self._autocast_dtype):
                    yield
            else:
                yield

    @staticmethod
    def _to_masks(obj_ids, logits) -> dict[int, np.ndarray]:
        return {int(o): (logits[i] > 0.0).squeeze(0).cpu().numpy() for i, o in enumerate(obj_ids)}

    def set_prompt(self, frame: int, obj: int, prompt: Prompt) -> dict[int, np.ndarray]:
        """Replace the prompt for (frame, obj) and return masks for that frame."""
        with self._ctx():
            if prompt.empty():
                if obj in self.state["obj_id_to_idx"]:
                    self.predictor.clear_all_prompts_in_frame(self.state, frame, obj)
                return {}
            kwargs = {}
            if prompt.points:
                kwargs["points"] = np.asarray(prompt.points, dtype=np.float32)
                kwargs["labels"] = np.asarray(prompt.labels, dtype=np.int32)
            if prompt.box is not None:
                kwargs["box"] = np.asarray(prompt.box, dtype=np.float32)
            _, obj_ids, logits = self.predictor.add_new_points_or_box(
                inference_state=self.state,
                frame_idx=frame,
                obj_id=obj,
                clear_old_points=True,
                normalize_coords=True,
                **kwargs,
            )
        return self._to_masks(obj_ids, logits)

    def propagate(self, start: int, reverse: bool):
        """Yield (frame, {obj: mask}) walking from ``start`` forwards or backwards."""
        with self._ctx():
            for frame, obj_ids, logits in self.predictor.propagate_in_video(
                self.state, start_frame_idx=start, reverse=reverse
            ):
                yield int(frame), self._to_masks(obj_ids, logits)

    def remove_object(self, obj: int):
        """Forget an object entirely (SAM 2 refuses to track an object with no marks)."""
        with self._ctx():
            self.predictor.remove_object(self.state, obj, need_output=False)

    def reset(self):
        self.predictor.reset_state(self.state)


def track(engine, prompts: PromptSet, store: MaskStore, direction: str = "both", progress=None,
          should_stop=None):
    """Track every prompted object through the clip, like Magic Mask's track buttons.

    ``direction`` is ``"both"``, ``"forward"`` or ``"backward"``. Forward runs from the
    earliest marked frame to the end; backward runs from the latest marked frame to the
    start. Every marked frame is used as memory in both passes, so corrections made on any
    frame improve the whole track.
    """
    frames = prompts.frames()
    if not frames:
        raise ValueError("Mark the object on at least one frame before tracking.")

    passes = []
    if direction in ("both", "forward"):
        passes.append((frames[0], False))
    if direction in ("both", "backward"):
        passes.append((frames[-1] if direction == "backward" else frames[0], True))

    done = 0
    for start, reverse in passes:
        for frame, masks in engine.propagate(start, reverse):
            for obj, m in masks.items():
                store.set(frame, obj, m)
            done += 1
            if progress:
                progress(frame, done, masks)
            if should_stop and should_stop():
                return False
    return True


def apply_prompts(engine, prompts: PromptSet, store: MaskStore):
    """Push every saved prompt into the engine (used when loading a session)."""
    for frame, obj, p in prompts.nonempty():
        for o, m in engine.set_prompt(frame, obj, p).items():
            store.set(frame, o, m)
