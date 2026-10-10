"""Post-install smoke test, run with the installed .venv Python on each OS by CI.

    .venv/bin/python tests/ci_smoke.py [--device auto]

Generates a clip, runs FreeMask headless with the REAL model, checks the matte follows
the subject, and checks the Resolve script was installed with this folder's path.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fakes import ball_center, make_clip  # noqa: E402

from freemask import cli, install_resolve  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--device", default="cpu")
ap.add_argument("--skip-resolve-check", action="store_true")
args = ap.parse_args()

tmp = Path(tempfile.mkdtemp())
clip = make_clip(tmp / "smoke.mp4", n=8)
x, y = ball_center(4)
t0 = time.time()
result = cli.main([str(clip), "--headless", "--model", "tiny", "--device", args.device,
                   "--point", f"4:{x},{y}", "--format", "png,video,cutout", "-o", str(tmp / "out")])
print(f"headless run on {args.device}: {time.time() - t0:.1f}s ->", result)

for kind in ("png", "video", "cutout"):
    assert Path(result[kind]).exists(), kind
for i in range(8):
    m = cv2.imread(str(Path(result["png"]) / f"smoke_matte_{i:06d}.png"), cv2.IMREAD_UNCHANGED)
    truth = np.zeros(m.shape, np.uint8)
    cv2.circle(truth, ball_center(i), 25, 1, -1)
    sel, t = m > 32768, truth.astype(bool)
    iou = (sel & t).sum() / max(1, (sel | t).sum())
    print(f"frame {i}: IoU {iou:.3f}")
    assert iou > 0.8, f"matte doesn't follow the subject on frame {i}"

if not args.skip_resolve_check:
    (lua,) = [d / "FreeMask.lua" for d in install_resolve.scripts_dirs()]
    text = lua.read_text()
    assert f"[[{ROOT}]]" in text, f"{lua} doesn't point at {ROOT}"
    print("Resolve script installed:", lua)
print("SMOKE OK")
