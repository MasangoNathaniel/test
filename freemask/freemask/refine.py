"""Matte refinement — the equivalent of Magic Mask's "Refine" and "Matte Finesse" controls."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass
class RefineSettings:
    grow: int = 0  # Expand (+) / contract (-) in source pixels
    remove_specks: int = 64  # Drop islands smaller than this many tracking-res pixels
    fill_holes: int = 0  # Fill holes smaller than this many tracking-res pixels
    consolidate: int = 0  # Temporal smoothing radius in frames (reduces chatter)
    smart_refine: int = -1  # Edge-aware snap radius in source px; -1 = auto, 0 = off
    feather: float = 0.0  # Gaussian blur sigma in source px
    clean_black: float = 0.0  # Matte levels: crush values below this to 0
    clean_white: float = 0.0  # Matte levels: lift values above 1-this to 1
    invert: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def clean_binary(mask: np.ndarray, remove_specks: int, fill_holes: int) -> np.ndarray:
    """Remove small islands and fill small holes in a boolean mask."""
    m = mask.astype(np.uint8)
    if remove_specks > 0 and m.any():
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        keep = np.zeros(n, dtype=bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= remove_specks
        if n > 1 and not keep.any():
            keep[1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))] = True  # never erase everything
        m = keep[labels].astype(np.uint8)
    if fill_holes > 0:
        inv = 1 - m
        n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
        h, w = m.shape
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            touches_border = x == 0 or y == 0 or x + bw == w or y + bh == h
            if area < fill_holes and not touches_border:
                m[labels == i] = 1
    return m.astype(bool)


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float = 1e-4) -> np.ndarray:
    """Fast grey-guide guided filter (He et al.) — snaps a soft matte onto image edges."""
    ksize = (2 * radius + 1, 2 * radius + 1)

    def box(x):
        return cv2.boxFilter(x, cv2.CV_32F, ksize, borderType=cv2.BORDER_REFLECT)

    mean_i = box(guide)
    mean_p = box(src)
    cov_ip = box(guide * src) - mean_i * mean_p
    var_i = box(guide * guide) - mean_i * mean_i
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    return box(a) * guide + box(b)


def auto_refine_radius(width: int, height: int) -> int:
    return max(2, int(round(min(width, height) / 270)))


def finish_matte(
    soft: np.ndarray,
    settings: RefineSettings,
    size: tuple[int, int],
    guide_bgr: np.ndarray | None = None,
) -> np.ndarray:
    """Take a tracking-resolution soft matte (float 0..1) to a finished full-res matte.

    ``size`` is (width, height) of the source.
    """
    w, h = size
    m = soft.astype(np.float32)
    if m.shape[:2] != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)

    if settings.grow:
        r = abs(int(settings.grow))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        m = cv2.dilate(m, k) if settings.grow > 0 else cv2.erode(m, k)

    radius = settings.smart_refine
    if radius < 0:
        radius = auto_refine_radius(w, h)
    if radius > 0 and guide_bgr is not None:
        g = cv2.cvtColor(guide_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        if g.shape != m.shape:
            g = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA)
        m = guided_filter(g, m, radius)
        # The filter puts the transition on the image edge but leaves a faint ramp in flat
        # areas; a smoothstep re-crisps it while keeping genuinely soft detail (hair, blur).
        t = np.clip((m - 0.2) / 0.6, 0.0, 1.0)
        m = t * t * (3.0 - 2.0 * t)

    if settings.feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), float(settings.feather))

    lo, hi = float(settings.clean_black), 1.0 - float(settings.clean_white)
    if lo > 0 or hi < 1:
        m = (m - lo) / max(hi - lo, 1e-6)

    m = np.clip(m, 0.0, 1.0)
    if settings.invert:
        m = 1.0 - m
    return m


def temporal_soft_mask(get_mask, idx: int, n_frames: int, settings: RefineSettings) -> np.ndarray | None:
    """Clean the mask(s) around ``idx`` and average them over the consolidate window.

    ``get_mask(i)`` returns a boolean tracking-res mask or ``None`` for an empty frame.
    Returns float32 in 0..1, or ``None`` if every frame in the window is empty.
    """
    r = max(0, int(settings.consolidate))
    acc = None
    total = 0.0
    for j in range(max(0, idx - r), min(n_frames, idx + r + 1)):
        # Centre frame counts double so consolidation never drifts off the current pose.
        weight = 2.0 if j == idx else 1.0
        total += weight
        mk = get_mask(j)
        if mk is None:
            continue
        mk = clean_binary(mk, settings.remove_specks, settings.fill_holes).astype(np.float32)
        acc = mk * weight if acc is None else acc + mk * weight
    if acc is None:
        return None
    soft = acc / total
    if r > 0:
        # Re-sharpen: consolidation should stabilise the edge, not make it mushy.
        soft = np.clip((soft - 0.5) * 2.5 + 0.5, 0.0, 1.0)
    return soft
