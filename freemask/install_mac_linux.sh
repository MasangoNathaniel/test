#!/usr/bin/env bash
# One-time setup for FreeMask on macOS or Linux. Needs Python 3.10+.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'

"$PY" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel

# PyPI's torch already includes CUDA on Linux and Apple-GPU (MPS) support on Mac.
python -m pip install "torch>=2.5.1" "torchvision>=0.20.1"
# SAM 2's optional CUDA extension is only a post-processing speed-up; skip compiling it.
SAM2_BUILD_CUDA=0 python -m pip install --no-build-isolation "sam2>=1.1.0"
python -m pip install "huggingface_hub>=0.23" "numpy>=1.24" "opencv-python>=4.8"

echo
echo "Done. Run FreeMask with:  ./freemask.sh path/to/clip.mp4"
