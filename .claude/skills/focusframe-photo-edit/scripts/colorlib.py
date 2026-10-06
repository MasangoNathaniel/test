"""Shared colour math and image statistics for the FocusFrame style scripts.

Everything works in CIE Lab (D65): L = lightness 0..100, a = green(-)/magenta(+),
b = blue(-)/yellow(+). Measuring and editing in Lab keeps tone and colour
separate, which is how a photographer thinks about an edit.
"""

import io

import numpy as np
from PIL import Image, ImageCms, ImageOps

PERCENTILES = [1, 5, 10, 25, 50, 75, 90, 95, 99]

# Approximate Lab hue angles (degrees) for the familiar HSL-panel colour names.
HUES = {
    "red": 30, "orange": 60, "yellow": 90, "green": 135,
    "aqua": 195, "blue": 260, "purple": 300, "magenta": 340,
}
HUE_NAMES = list(HUES)
HUE_ANGLES = np.array([HUES[h] for h in HUE_NAMES], dtype=np.float32)

_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_M_INV = np.linalg.inv(_M).astype(np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
_EPS = (6 / 29) ** 3


def load_image(path, max_side=None):
    """Return (float sRGB array 0..1, exif bytes or None), upright per EXIF.

    Embedded colour profiles (Adobe RGB, Display P3, ...) are converted to sRGB
    so the Lab maths below is valid. The returned EXIF has orientation reset,
    since the pixels are already rotated upright.
    """
    im = Image.open(path)
    exif = im.getexif()
    if 0x0112 in exif:
        exif[0x0112] = 1
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im).convert("RGB")
    if icc:
        try:
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                           ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass  # unreadable profile: treat the pixels as sRGB
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0, (exif.tobytes() if len(exif) else None)


def save_image(rgb, path, exif=None, quality=95):
    im = Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8))
    kwargs = {"quality": quality, "subsampling": 0} if str(path).lower().endswith((".jpg", ".jpeg")) else {}
    if exif:
        kwargs["exif"] = exif
    im.save(path, **kwargs)
    return im


def rgb_to_lab(rgb):
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _M.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1).astype(np.float32)


def lab_to_rgb(lab):
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = np.where(f > 6 / 29, f ** 3, 3 * (6 / 29) ** 2 * (f - 4 / 29)) * _WHITE
    lin = np.clip(xyz @ _M_INV.T, 0, 1)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055).astype(np.float32)


def band_weights(L):
    """Soft shadow / midtone / highlight masks that always sum to 1."""
    ws = np.clip(1 - L / 50, 0, 1)
    wh = np.clip((L - 50) / 50, 0, 1)
    return ws, 1 - ws - wh, wh


def hue_angle(a, b):
    return np.degrees(np.arctan2(b, a)) % 360


def nearest_hue(h):
    d = np.abs((h[..., None] - HUE_ANGLES + 180) % 360 - 180)
    return np.argmin(d, axis=-1)


def interp_hue(h, values):
    """Smoothly interpolate one value per named hue around the colour wheel."""
    order = np.argsort(HUE_ANGLES)
    xs = HUE_ANGLES[order]
    ys = np.asarray(values, dtype=np.float32)[order]
    xs = np.concatenate([xs[-1:] - 360, xs, xs[:1] + 360])
    ys = np.concatenate([ys[-1:], ys, ys[:1]])
    return np.interp(h, xs, ys).astype(np.float32)


def radial_distance(h, w):
    """0 at the centre, 1 at the corners."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.hypot((x - w / 2) / (w / 2), (y - h / 2) / (h / 2)) / np.sqrt(2)


def _box_blur3(x):
    p = np.pad(x, 1, mode="edge")
    return sum(p[i:i + x.shape[0], j:j + x.shape[1]] for i in range(3) for j in range(3)) / 9


def image_stats(lab):
    """Measure the tonal and colour signature of one image (Lab array)."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    ws, wm, wh = band_weights(L)

    def wmean(v, w):
        return float((v * w).sum() / max(w.sum(), 1e-6))

    h, w = L.shape
    r = radial_distance(h, w)
    centre, edge = L[r < 0.35], L[r > 0.85]

    hue_idx = nearest_hue(hue_angle(a, b))
    coloured = C > 5
    hues = {}
    for i, name in enumerate(HUE_NAMES):
        m = coloured & (hue_idx == i)
        frac = float(m.mean())
        hues[name] = {"fraction": round(frac, 4),
                      "chroma": round(float(C[m].mean()), 2) if frac > 0.005 else None}

    resid = np.abs(L - _box_blur3(L))
    flat = resid < np.percentile(resid, 60)

    return {
        "L_percentiles": {str(p): round(float(v), 2) for p, v in zip(PERCENTILES, np.percentile(L, PERCENTILES))},
        "contrast_std": round(float(L.std()), 2),
        "chroma_mean": round(float(C.mean()), 2),
        "chroma_p90": round(float(np.percentile(C, 90)), 2),
        "is_monochrome": bool(np.percentile(C, 95) < 4),
        "tint": {
            "shadows": [round(wmean(a, ws), 2), round(wmean(b, ws), 2)],
            "midtones": [round(wmean(a, wm), 2), round(wmean(b, wm), 2)],
            "highlights": [round(wmean(a, wh), 2), round(wmean(b, wh), 2)],
        },
        "hues": hues,
        "vignette_ratio": round(float(edge.mean() / max(centre.mean(), 1e-3)), 3) if centre.size and edge.size else 1.0,
        "noise": round(float(np.median(resid[flat])), 3),
    }
