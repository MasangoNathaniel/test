"""A stand-in for SAM 2 so the app can be tested without model weights."""

from pathlib import Path

import cv2
import numpy as np

W, H, N = 320, 180, 12


def ball_center(i):
    """Ball position in 320x180 space; it bounces between x=40 and x=280."""
    t = (i * 20) % 480
    return 40 + (t if t <= 240 else 480 - t), 90


def make_clip(path, n=None, size=(W, H), pretty=False):
    """A synthetic shot: a bright ball moving left to right over a dark background.

    ``pretty`` paints a moodier scene (for UI screenshots); the subject stays the only
    bright thing in frame so the fake tracker can find it.
    """
    n = n or N
    w, h = size
    sx, sy = w / W, h / H
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (w, h))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    bg = np.full((h, w, 3), 30, np.uint8)
    if pretty:
        t = yy / h
        img = np.dstack([40 + 50 * (1 - t), 22 + 30 * (1 - t) + 10 * np.sin(xx / w * 3), 30 + 60 * t])
        img[yy > h * 0.72] = (18, 22, 26)
        for k in range(14):  # soft bokeh
            cx, cy = (k * 97 * sx % w), (k * 53 * sy % (h * 0.6))
            r = (6 + k % 5 * 4) * sx
            img += 30 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))[..., None] * np.array([0.6, 0.8, 1.0])
        bg = np.clip(img, 0, 110).astype(np.uint8)
    for i in range(n):
        img = bg.copy()
        x, y = ball_center(i)
        cv2.circle(img, (int(x * sx), int(y * sy)), int(25 * sx), (225, 232, 240), -1, cv2.LINE_AA)
        vw.write(img)
    vw.release()
    return path


class FakeEngine:
    """Segments the bright blob under the clicked point and follows it frame to frame."""

    device = "fake"

    def __init__(self, frames_dir, *a, **k):
        self.dir = Path(frames_dir)
        self.n = len(list(self.dir.glob("*.jpg")))
        self.cond = {}  # frame -> {obj: mask}

    def _blob(self, frame, x, y):
        g = cv2.imread(str(self.dir / f"{frame:05d}.jpg"), cv2.IMREAD_GRAYSCALE)
        _, labels = cv2.connectedComponents((g > 128).astype(np.uint8))
        h, w = g.shape
        lab = labels[int(np.clip(y, 0, h - 1)), int(np.clip(x, 0, w - 1))]
        return labels == lab if lab else np.zeros_like(g, bool)

    def set_prompt(self, frame, obj, prompt):
        if prompt.empty():
            self.cond.get(frame, {}).pop(obj, None)
            return {}
        pos = [pt for pt, lab in zip(prompt.points, prompt.labels) if lab] or [
            [(prompt.box[0] + prompt.box[2]) / 2, (prompt.box[1] + prompt.box[3]) / 2]]
        m = self._blob(frame, *pos[0])
        self.cond.setdefault(frame, {})[obj] = m
        return {obj: m}

    def remove_object(self, obj):
        for d in self.cond.values():
            d.pop(obj, None)

    def propagate(self, start, reverse):
        prev = dict(self.cond.get(start, {}))
        rng = range(start, -1, -1) if reverse else range(start, self.n)
        for f in rng:
            out = {}
            for obj, pm in prev.items():
                if f in self.cond and obj in self.cond[f]:
                    out[obj] = self.cond[f][obj]
                    continue
                ys, xs = np.nonzero(pm)
                out[obj] = self._blob(f, xs.mean(), ys.mean()) if len(xs) else pm
                if not out[obj].any():  # moved too far; search the frame
                    h, w = pm.shape
                    x, y = ball_center(f)
                    out[obj] = self._blob(f, x * w / W, y * h / H)
            prev = out
            yield f, out
