"""Add FreeMask to DaVinci Resolve's Workspace > Scripts menu.

    python -m freemask.install_resolve            # install / update
    python -m freemask.install_resolve --remove   # uninstall
"""

from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
SCRIPT = HOME / "resolve" / "FreeMask.lua"


def scripts_dirs() -> list[Path]:
    """Resolve's per-user "Utility" scripts folder(s) for this OS."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts"
    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        base = appdata / "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts"
    else:
        base = Path.home() / ".local/share/DaVinciResolve/Fusion/Scripts"
    return [base / "Utility"]


def install(home: Path = HOME) -> list[Path]:
    text = SCRIPT.read_text(encoding="utf-8").replace("__FREEMASK_HOME__", str(home))
    written = []
    for d in scripts_dirs():
        d.mkdir(parents=True, exist_ok=True)
        target = d / "FreeMask.lua"
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def remove() -> list[Path]:
    removed = []
    for d in scripts_dirs():
        target = d / "FreeMask.lua"
        if target.exists():
            target.unlink()
            removed.append(target)
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args(argv)
    if args.remove:
        for p in remove():
            print(f"Removed {p}")
        return
    for p in install():
        print(f"Installed the Resolve script: {p}")
    print("In DaVinci Resolve: Workspace > Scripts > FreeMask  (restart Resolve if it was open)")


if __name__ == "__main__":
    main()
