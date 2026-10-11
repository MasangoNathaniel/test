#!/usr/bin/env bash
# One-line install for macOS (Apple Silicon) and Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/MasangoNathaniel/test/claude/davinci-magic-mask-tool-fcenxg/freemask/install_from_github.sh | bash
#
# Downloads FreeMask into ~/FreeMask (or $FREEMASK_DIR) and runs the installer, which sets up
# Python if needed, PyTorch, SAM 2, the model, and the DaVinci Resolve menu item.
set -euo pipefail
REPO="MasangoNathaniel/test"
BRANCH="${FREEMASK_BRANCH:-claude/davinci-magic-mask-tool-fcenxg}"
DEST="${FREEMASK_DIR:-$HOME/FreeMask}"

echo "▸ Downloading FreeMask to $DEST"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$tmp"
src="$(find "$tmp" -mindepth 2 -maxdepth 2 -type d -name freemask | head -n 1)"
[ -n "$src" ] || { echo "Download looked wrong — couldn't find the freemask folder."; exit 1; }
mkdir -p "$DEST"
cp -R "$src"/. "$DEST"/   # updates in place; keeps an existing .venv

bash "$DEST/install_mac_linux.sh"
