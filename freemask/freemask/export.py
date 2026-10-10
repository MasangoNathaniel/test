"""Render finished full-resolution mattes for the whole clip."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .refine import RefineSettings, finish_matte, temporal_soft_mask
from .video_io import ClipInfo, MatteWriter, iter_source_frames


def render_matte(store, idx: int, info: ClipInfo, settings: RefineSettings, frame_bgr):
    soft = temporal_soft_mask(store.combined, idx, info.frame_count, settings)
    if soft is None:
        soft = np.zeros((info.proc_height, info.proc_width), np.float32)
    return finish_matte(soft, settings, (info.width, info.height), frame_bgr)


def export_mattes(
    info: ClipInfo,
    store,
    settings: RefineSettings,
    out_dir: str,
    name: str,
    formats: set[str],
    progress=None,
) -> dict[str, Path]:
    end = info.first_frame + info.frame_count - 1
    with MatteWriter(out_dir, name, info, formats) as writer:
        for i, frame in enumerate(iter_source_frames(info.source, info.first_frame, end)):
            if i >= info.frame_count:
                break
            writer.write(render_matte(store, i, info, settings, frame), frame)
            if progress:
                progress(i + 1, info.frame_count)
        paths = dict(writer.paths)

    meta = {
        "source": str(info.source),
        "first_frame": info.first_frame,
        "frame_count": info.frame_count,
        "fps": info.fps,
        "resolution": [info.width, info.height],
        "refine": settings.to_dict(),
        "outputs": {k: str(v) for k, v in paths.items()},
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / f"{name}_freemask.json").write_text(json.dumps(meta, indent=2))
    return paths
