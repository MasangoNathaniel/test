"""Command-line entry point: ``python -m freemask <clip>``."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import tracker
from .refine import RefineSettings
from .session import Session
from .tracker import MODELS, PromptSet, apply_prompts


def _parse_xy(spec: str, n: int, what: str):
    """Parse ``FRAME:x,y[,..][@OBJ]`` into (frame, [floats], obj)."""
    try:
        frame_s, rest = spec.split(":", 1)
        obj = 1
        if "@" in rest:
            rest, obj_s = rest.split("@", 1)
            obj = int(obj_s)
        vals = [float(v) for v in rest.split(",")]
        if len(vals) != n:
            raise ValueError
        return int(frame_s), vals, obj
    except ValueError:
        raise SystemExit(f"Bad {what} '{spec}'. Expected FRAME:" + ",".join("XY"[:n] if n == 2 else
                                                                            ["X1", "Y1", "X2", "Y2"]) + "[@OBJECT]")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="freemask",
        description="Magic-Mask-style object tracking and matte export for DaVinci Resolve (free version).",
    )
    p.add_argument("input", nargs="?", help="Video file or image-sequence folder (optional: you can open one in the app)")
    p.add_argument("-o", "--out", help="Output folder (default: <input name>_freemask next to the input)")
    p.add_argument("--name", help="Base name for exported files (default: input name)")

    g = p.add_argument_group("tracking")
    g.add_argument("--model", default="small", choices=list(MODELS),
                   help="SAM 2.1 model size: tiny (fastest) ... large (best). Default: small")
    g.add_argument("--device", default="auto", help="auto, cuda, mps or cpu")
    g.add_argument("--quality", type=int, default=1024, metavar="PX",
                   help="Longest side in px the tracker works at (default 1024; lower = faster)")
    g.add_argument("--start", type=int, default=0, help="First source frame to process (in point)")
    g.add_argument("--end", type=int, default=None, help="Last source frame to process (out point)")
    g.add_argument("--fps", type=float, default=None, help="Frame rate for image-sequence input")

    g = p.add_argument_group("headless mode (no window) — coordinates are source pixels, frames are source frame numbers")
    g.add_argument("--headless", action="store_true", help="Track and export without opening the editor")
    g.add_argument("--point", action="append", default=[], metavar="F:X,Y[@OBJ]", help="Include point")
    g.add_argument("--neg", action="append", default=[], metavar="F:X,Y[@OBJ]", help="Exclude point")
    g.add_argument("--box", action="append", default=[], metavar="F:X1,Y1,X2,Y2[@OBJ]", help="Box around object")
    g.add_argument("--session", help="Load marks + refine settings saved from the editor (P key)")
    g.add_argument("--direction", default="both", choices=["both", "forward", "backward"])

    g = p.add_argument_group("refine (all also adjustable live in the editor)")
    d = RefineSettings()
    g.add_argument("--grow", type=int, default=None, help=f"Expand(+)/contract(-) px (default {d.grow})")
    g.add_argument("--feather", type=float, default=None, help=f"Blur radius px (default {d.feather})")
    g.add_argument("--smart-refine", type=int, default=None,
                   help="Edge-snap radius px, -1 auto, 0 off (default auto)")
    g.add_argument("--consolidate", type=int, default=None, help="Temporal smoothing in frames (default 0)")
    g.add_argument("--remove-specks", type=int, default=None, help=f"Min island area (default {d.remove_specks})")
    g.add_argument("--fill-holes", type=int, default=None, help=f"Max hole area to fill (default {d.fill_holes})")
    g.add_argument("--clean-black", type=float, default=None, help="0..0.5 crush low matte values")
    g.add_argument("--clean-white", type=float, default=None, help="0..0.5 lift high matte values")
    g.add_argument("--invert", action="store_true", default=None, help="Invert the matte")

    g = p.add_argument_group("output")
    g.add_argument("--format", default=None,
                   help="Comma list of: png (16-bit matte sequence), cutout (RGBA PNGs), video (matte .mov). "
                        "Default: all three in the app, png in headless mode")

    g = p.add_argument_group("app")
    g.add_argument("--frame", type=int, default=None, help="Source frame to show first")
    g.add_argument("--port", type=int, default=0, help="Port for the local UI server (default: any free port)")
    g.add_argument("--no-window", action="store_true", help="Don't open a browser window (print the URL)")
    g.add_argument("--done-file", help=argparse.SUPPRESS)  # written on exit for the Resolve script
    g.add_argument("--log", help=argparse.SUPPRESS)  # handled in __main__
    return p


def _engine_factory(frames_dir, model, device):
    if os.environ.get("FREEMASK_ENGINE"):
        from .session import default_engine_factory

        return default_engine_factory(frames_dir, model, device)
    return tracker.Sam2Engine(frames_dir, model, device)


def main(argv=None):
    args = build_parser().parse_args(argv)
    fmt = args.format or ("png" if args.headless else "png,video,cutout")
    formats = {f.strip() for f in fmt.split(",") if f.strip()}
    bad = formats - {"png", "cutout", "video"}
    if bad or not formats:
        raise SystemExit(f"Unknown format(s): {', '.join(sorted(bad)) or '(none)'}")

    session = Session(model=args.model, device=args.device, quality=args.quality,
                      engine_factory=_engine_factory, formats=formats)
    for key in RefineSettings.__dataclass_fields__:
        val = getattr(args, key, None)
        if val is not None:
            setattr(session.settings, key, val)

    if args.headless:
        return _run_headless(session, args, formats)

    from .server import App

    if args.input:
        src = Path(args.input)
        if not src.exists():
            raise SystemExit(f"Input not found: {src}")
        session.open_clip(str(src), args.start, args.end, args.fps, args.out, args.name,
                          frame=args.frame, session_file=args.session)
    App(session, done_file=args.done_file).serve(port=args.port, open_window=not args.no_window)


def _run_headless(session: Session, args, formats):
    if not args.input:
        raise SystemExit("Headless mode needs an input clip.")
    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"Input not found: {src}")
    cli_settings = session.settings.to_dict()
    print(f"Reading {src} and loading SAM 2.1 '{args.model}' (first run downloads it) ...")
    job = session.open_clip(str(src), args.start, args.end, args.fps, args.out, args.name,
                            session_file=args.session, wait=True)
    if job.error:
        raise SystemExit(f"Error: {job.error}")
    info = session.info
    print(f"  {info.frame_count} frames, {info.width}x{info.height} @ {info.fps:.3f} fps "
          f"(tracking at {info.proc_width}x{info.proc_height}) on {session.engine.device}")
    # Flags given on the command line win over the session file.
    defaults = RefineSettings().to_dict()
    for k, v in cli_settings.items():
        if v != defaults[k]:
            setattr(session.settings, k, v)

    to_proc = info.proc_width / info.width
    _add_cli_prompts(session.prompts, args, to_proc, info)
    if not session.prompts.frames():
        raise SystemExit("Headless mode needs at least one --point, --box or a --session file.")
    apply_prompts(session.engine, session.prompts, session.store)

    t0 = time.time()
    job = session.track(args.direction, wait=True)
    if job.error:
        raise SystemExit(f"Tracking failed: {job.error}")
    print(f"  tracked {session.store.tracked_count()} frames in {time.time() - t0:.1f}s")
    job = session.export(formats, wait=True)
    if job.error:
        raise SystemExit(f"Export failed: {job.error}")
    for kind, path in job.result.items():
        print(f"  {kind:7s} -> {path}")
    session.close()
    return job.result


def _add_cli_prompts(prompts: PromptSet, args, to_proc: float, info):
    def frame_of(f):
        rel = f - info.first_frame
        if not 0 <= rel < info.frame_count:
            raise SystemExit(f"Frame {f} is outside the processed range "
                             f"{info.first_frame}-{info.first_frame + info.frame_count - 1}")
        return rel

    for spec, label in [(s, 1) for s in args.point] + [(s, 0) for s in args.neg]:
        f, (x, y), obj = _parse_xy(spec, 2, "point")
        p = prompts.get(frame_of(f), obj)
        p.points.append([x * to_proc, y * to_proc])
        p.labels.append(label)
    for spec in args.box:
        f, vals, obj = _parse_xy(spec, 4, "box")
        prompts.get(frame_of(f), obj).box = [v * to_proc for v in vals]


if __name__ == "__main__":
    sys.exit(main())
