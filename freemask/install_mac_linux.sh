#!/usr/bin/env bash
# One-time setup for FreeMask on macOS or Linux.
#   bash install_mac_linux.sh
# Re-running it is safe: it updates everything in place.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

say() { printf "\n\033[1;35m▸ %s\033[0m\n" "$*"; }

# 1. Find Python 3.10+ (macOS's built-in python3 is often 3.9, which is too old).
PY=""
[ -n "${FREEMASK_FORCE_PRIVATE_PYTHON:-}" ] && SKIP_SEARCH=1 || SKIP_SEARCH=""
for c in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3; do
  [ -n "$SKIP_SEARCH" ] && break
  [ -z "$c" ] && continue
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  # No suitable Python: fetch a private copy with uv (no admin password, nothing system-wide).
  say "Python 3.10+ not found — downloading a private copy just for FreeMask"
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  fi
  uv python install 3.12
  PY="$(uv python find 3.12)"
fi
say "Using $("$PY" --version) ($PY)"

# PyTorch no longer ships builds for Intel Macs (2.3+), and SAM 2 needs PyTorch 2.5+.
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ] \
   && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" != "1" ]; then
  echo
  echo "This is an Intel Mac. The AI model FreeMask uses (SAM 2) needs PyTorch 2.5 or newer,"
  echo "which isn't available for Intel Macs. FreeMask needs an Apple Silicon Mac (M1 or newer),"
  echo "a Windows PC, or a Linux PC."
  exit 1
fi

# 2. Private environment so nothing touches your system Python.
say "Creating the FreeMask environment"
"$PY" -m venv .venv
VPY="$HERE/.venv/bin/python"
"$VPY" -m pip install --upgrade --quiet pip setuptools wheel

# 3. PyTorch (PyPI's build includes CUDA on Linux and Apple-GPU/MPS support on Mac).
say "Installing PyTorch (this is the big download)"
"$VPY" -m pip install --quiet "torch>=2.5.1" "torchvision>=0.20.1"

# 4. SAM 2 — its optional CUDA extension is only a post-processing speed-up; skip compiling it.
say "Installing SAM 2 and friends"
SAM2_BUILD_CUDA=0 "$VPY" -m pip install --quiet --no-build-isolation "sam2>=1.1.0"
"$VPY" -m pip install --quiet "huggingface_hub>=0.23" "numpy>=1.24" "opencv-python-headless>=4.8" "imageio-ffmpeg>=0.5"

# 5. Download the default model now so the first launch from Resolve is instant.
say "Downloading the SAM 2.1 model (~180 MB)"
"$VPY" -c "from sam2.build_sam import _hf_download; _hf_download('facebook/sam2.1-hiera-small')" 2>/dev/null \
  || echo "  (couldn't download now — it will download on first use)"

# 6. Add FreeMask to Resolve's Workspace > Scripts menu.
say "Adding FreeMask to DaVinci Resolve"
(cd "$HERE" && "$VPY" -m freemask.install_resolve)

chmod +x "$HERE/freemask.sh" 2>/dev/null || true
say "All set!"
echo "  • In DaVinci Resolve: put the playhead on a clip, then Workspace › Scripts › FreeMask"
echo "  • Or run it directly:  ./freemask.sh path/to/clip.mov"
