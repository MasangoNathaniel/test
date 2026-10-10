#!/usr/bin/env bash
# Launch FreeMask. Usage: ./freemask.sh clip.mp4 [options]   (./freemask.sh --help for all options)
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec "$HERE/.venv/bin/python" -m freemask "$@"
