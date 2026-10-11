"""Reading source footage and writing mattes in formats DaVinci Resolve imports."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr", ".dpx"}


@dataclass
class ClipInfo:
    """Everything we need to know about the source clip."""

    source: str
    fps: float
    width: int  # full (source) resolution
    height: int
    frame_count: int  # frames inside the selected range
    first_frame: int  # index of the first frame in the source
    proc_width: int  # resolution the tracker works at
    proc_height: int


def _sequence_files(folder: Path) -> list[Path]:
    files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in IMAGE_EXTS]
    if not files:
        raise FileNotFoundError(f"No image files found in {folder}")
    return files


def _proc_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    scale = min(1.0, max_side / max(width, height))
    # Even dimensions keep video encoders and the model happy.
    return max(2, int(round(width * scale / 2)) * 2), max(2, int(round(height * scale / 2)) * 2)


def imread(path, flags=cv2.IMREAD_COLOR):
    """cv2.imread that also works with non-ASCII paths on Windows, and falls back to
    ffmpeg for formats this OpenCV build can't decode (EXR, some DPX/TIFF)."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(data, flags) if data.size else None
    if img is None and data.size:
        img = _ffmpeg_image(path)
    return img


def _ffmpeg_image(path):
    meta, gen = _ffmpeg_reader(Path(path))
    if gen is None:
        return None
    try:
        raw = next(gen, None)
    finally:
        gen.close()
    if raw is None:
        return None
    w, h = meta["size"]
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).copy()


def imwrite(path, img, params=()):
    """cv2.imwrite that also works with non-ASCII paths on Windows."""
    ok, buf = cv2.imencode(Path(path).suffix, img, list(params))
    if not ok:
        raise IOError(f"Could not encode {path}")
    buf.tofile(str(path))


def _open_capture(path: Path):
    cap = cv2.VideoCapture(str(path))
    if cap.isOpened():
        return cap
    cap.release()
    return None


def _ffmpeg_reader(path: Path):
    """Fallback decoder (bundled ffmpeg): handles paths/codecs OpenCV can't. Returns (meta, frames)."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None, None
    try:
        gen = imageio_ffmpeg.read_frames(str(path), pix_fmt="bgr24")
        meta = next(gen)
    except Exception:
        return None, None
    return meta, gen


def iter_source_frames(source: str, start: int = 0, end: int | None = None):
    """Yield full-resolution BGR uint8 frames from a video file or image-sequence folder."""
    path = Path(source)
    if path.is_dir():
        files = _sequence_files(path)[start : None if end is None else end + 1]
        for f in files:
            img = imread(f, cv2.IMREAD_UNCHANGED)
            if img is None:
                raise IOError(f"Could not read {f}")
            yield _to_bgr8(img)
        return

    cap = _open_capture(path)
    if cap is None:
        meta, gen = _ffmpeg_reader(path)
        if gen is None:
            raise IOError(f"Could not open video {source}")
        w, h = meta["size"]
        try:
            for idx, raw in enumerate(gen):
                if idx < start:
                    continue
                if end is not None and idx > end:
                    break
                yield np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        finally:
            gen.close()
        return
    try:
        if start:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        idx = start
        while end is None or idx <= end:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
            idx += 1
    finally:
        cap.release()


def _to_bgr8(img: np.ndarray) -> np.ndarray:
    if img.dtype != np.uint8:
        if np.issubdtype(img.dtype, np.floating):
            img = np.clip(img, 0.0, 1.0) * 255.0
        else:
            img = img.astype(np.float64) / np.iinfo(img.dtype).max * 255.0
        img = img.astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def probe(source: str, fps_override: float | None = None) -> tuple[float, int, int, int]:
    """Return (fps, width, height, total_frames) of the source."""
    path = Path(source)
    if path.is_dir():
        files = _sequence_files(path)
        first = imread(files[0], cv2.IMREAD_UNCHANGED)
        if first is None:
            raise IOError(f"Could not read {files[0]}")
        h, w = _to_bgr8(first).shape[:2]
        return fps_override or 24.0, w, h, len(files)
    cap = _open_capture(path)
    if cap is None:
        meta, gen = _ffmpeg_reader(path)
        if gen is None:
            raise IOError(f"Could not open video {source}")
        gen.close()
        fps = float(meta.get("fps") or 24.0)
        w, h = meta["size"]
        return fps_override or fps, w, h, int(round(float(meta.get("duration") or 0) * fps))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps_override or fps, w, h, n


def extract_frames(
    source: str,
    work_dir: str,
    max_side: int = 1024,
    start: int = 0,
    end: int | None = None,
    fps_override: float | None = None,
    progress=None,
) -> ClipInfo:
    """Write the frame range as numbered JPEGs (the layout SAM 2 expects) at tracking resolution."""
    fps, w, h, _ = probe(source, fps_override)
    pw, ph = _proc_size(w, h, max_side)
    out = Path(work_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    count = 0
    for frame in iter_source_frames(source, start, end):
        if (frame.shape[1], frame.shape[0]) != (pw, ph):
            frame = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        imwrite(out / f"{count:05d}.jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        count += 1
        if progress:
            progress(count)
    if count == 0:
        raise IOError(f"No frames decoded from {source} (range {start}-{end})")
    return ClipInfo(source, fps, w, h, count, start, pw, ph)


class MatteWriter:
    """Writes refined mattes to disk in one or more Resolve-friendly formats.

    * ``png``    – 16-bit grayscale PNG sequence (white = selected). Default.
    * ``cutout`` – RGBA PNG sequence of the source with the matte as alpha, plus the same
      as a ProRes 4444 movie with alpha (one file: the most reliable thing to import).
    * ``video``  – a grayscale matte movie (ProRes 422 HQ .mov via ffmpeg,
      falling back to an .mp4 written by OpenCV when ffmpeg is unavailable).
    """

    def __init__(self, out_dir: str, name: str, info: ClipInfo, formats: set[str]):
        self.out_dir = Path(out_dir)
        self.name = name
        self.info = info
        self.formats = formats
        self.index = 0
        self.paths: dict[str, Path] = {}
        self._ffmpeg = None
        self._cv_writer = None
        self._cutout_ffmpeg = None

        if "png" in formats:
            self.paths["png"] = self._mkdir(f"{name}_matte")
        if "cutout" in formats:
            self.paths["cutout"] = self._mkdir(f"{name}_cutout")
            self._open_cutout_video()
        if "video" in formats:
            self._open_video()

    def _mkdir(self, sub: str) -> Path:
        d = self.out_dir / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _open_video(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        w, h, fps = self.info.width, self.info.height, self.info.fps
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            path = self.out_dir / f"{self.name}_matte.mov"
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", f"{fps}",
                "-i", "-",
                "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
                str(path),
            ]
            self._ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        else:
            path = self.out_dir / f"{self.name}_matte.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._cv_writer = cv2.VideoWriter(str(path), fourcc, fps, (w, h), True)
        self.paths["video"] = path

    def _open_cutout_video(self):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return
        w, h, fps = self.info.width, self.info.height, self.info.fps
        path = self.out_dir / f"{self.name}_cutout.mov"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", f"{fps}",
            "-i", "-",
            "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16",
            "-vendor", "apl0",
            str(path),
        ]
        self._cutout_ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.paths["cutout_video"] = path

    def write(self, matte: np.ndarray, frame_bgr: np.ndarray | None = None):
        """``matte`` is float32 in [0, 1] at full source resolution."""
        frame_no = self.info.first_frame + self.index
        if "png" in self.paths:
            m16 = np.round(np.clip(matte, 0, 1) * 65535).astype(np.uint16)
            imwrite(self.paths["png"] / f"{self.name}_matte_{frame_no:06d}.png", m16)
        if "cutout" in self.paths:
            if frame_bgr is None:
                raise ValueError("cutout output needs the source frame")
            alpha = np.round(np.clip(matte, 0, 1) * 255).astype(np.uint8)
            rgba = np.dstack([frame_bgr, alpha])
            imwrite(self.paths["cutout"] / f"{self.name}_cutout_{frame_no:06d}.png", rgba)
            if self._cutout_ffmpeg:
                self._cutout_ffmpeg.stdin.write(np.ascontiguousarray(rgba).tobytes())
        m8 = None
        if self._ffmpeg or self._cv_writer:
            m8 = np.round(np.clip(matte, 0, 1) * 255).astype(np.uint8)
        if self._ffmpeg:
            self._ffmpeg.stdin.write(np.ascontiguousarray(m8).tobytes())
        if self._cv_writer:
            self._cv_writer.write(cv2.cvtColor(m8, cv2.COLOR_GRAY2BGR))
        self.index += 1

    def close(self):
        if self._cutout_ffmpeg:
            self._cutout_ffmpeg.stdin.close()
            if self._cutout_ffmpeg.wait() != 0:
                raise RuntimeError("ffmpeg failed while writing the cutout movie")
            self._cutout_ffmpeg = None
        if self._ffmpeg:
            self._ffmpeg.stdin.close()
            if self._ffmpeg.wait() != 0:
                raise RuntimeError("ffmpeg failed while writing the matte movie")
            self._ffmpeg = None
        if self._cv_writer:
            self._cv_writer.release()
            self._cv_writer = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def find_ffmpeg() -> str | None:
    """System ffmpeg, else the copy bundled with the imageio-ffmpeg package."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def default_name(source: str) -> str:
    p = Path(source)
    return p.name if p.is_dir() else p.stem


def safe_remove(path: str):
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
