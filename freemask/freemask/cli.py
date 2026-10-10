"""Command-line entry point: ``python -m freemask <clip>``."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from .refine import RefineSettings
from .tracker import MODELS, MaskStore, PromptSet, apply_prompts, track
from .video_io import default_name, extract_frames, safe_remove


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
    p.add_argument("input", help="Video file or folder containing an image sequence")
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
    g.add_argument("--format", default="png",
                   help="Comma list of: png (16-bit matte sequence), cutout (RGBA PNGs), video (matte .mov). "
                        "Default: png")
    g.add_argument("--keep-frames", action="store_true", help="Keep the extracted working frames")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"Input not found: {src}")
    name = args.name or default_name(str(src))
    out_dir = Path(args.out) if args.out else src.parent / f"{name}_freemask"
    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    bad = formats - {"png", "cutout", "video"}
    if bad:
        raise SystemExit(f"Unknown format(s): {', '.join(sorted(bad))}")

    session = {}
    if args.session:
        session = json.loads(Path(args.session).read_text())
    settings = RefineSettings.from_dict(session.get("refine", {}))
    for key in RefineSettings.__dataclass_fields__:
        val = getattr(args, key, None)
        if val is not None:
            setattr(settings, key, val)

    work = tempfile.mkdtemp(prefix="freemask_")
    frames_dir = str(Path(work) / "frames")
    try:
        print(f"Reading {src} ...")
        info = extract_frames(
            str(src), frames_dir, max_side=args.quality, start=args.start, end=args.end,
            fps_override=args.fps,
            progress=lambda n: print(f"\r  extracted {n} frames", end="", flush=True),
        )
        print(f"\n  {info.frame_count} frames, {info.width}x{info.height} @ {info.fps:.3f} fps "
              f"(tracking at {info.proc_width}x{info.proc_height})")
        to_proc = info.proc_width / info.width

        prompts = PromptSet.from_json(session, to_proc, info.first_frame) if session else PromptSet()
        _add_cli_prompts(prompts, args, to_proc, info)

        print(f"Loading SAM 2.1 '{args.model}' model (first run downloads it) ...")
        from .tracker import Sam2Engine

        engine = Sam2Engine(frames_dir, args.model, args.device)
        print(f"  running on {engine.device}")

        def save_session(editor) -> Path:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{name}_session.json"
            data = editor.prompts.to_json(1 / to_proc, info.first_frame)
            data["refine"] = editor.settings.to_dict()
            data["source"] = str(src)
            path.write_text(json.dumps(data, indent=2))
            return path

        def export(store_owner) -> dict:
            from .export import export_mattes

            t0 = time.time()
            paths = export_mattes(
                info, store_owner.store, store_owner.settings, str(out_dir), name, formats,
                progress=lambda i, n: print(f"\r  exporting {i}/{n}", end="", flush=True),
            )
            print(f"\n  done in {time.time() - t0:.1f}s")
            return paths

        if args.headless:
            _run_headless(engine, prompts, settings, info, args.direction, export)
        else:
            try:
                from .gui import Editor
            except ImportError as exc:  # pragma: no cover
                raise SystemExit(f"Could not start the editor: {exc}")
            editor = Editor(info, frames_dir, engine, prompts, settings, save_session, export)
            try:
                editor.run()
            except Exception as exc:  # the headless OpenCV build has no GUI support
                if "is not implemented" in str(exc) or "highgui" in str(exc).lower():
                    raise SystemExit(
                        "OpenCV was installed without window support. Run:\n"
                        "  pip uninstall -y opencv-python-headless && pip install opencv-python"
                    )
                raise
    finally:
        if args.keep_frames:
            print(f"Working frames kept in {frames_dir}")
        else:
            safe_remove(work)


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


def _run_headless(engine, prompts, settings, info, direction, export):
    if not prompts.frames():
        raise SystemExit("Headless mode needs at least one --point, --box or a --session file.")

    class Owner:
        pass

    owner = Owner()
    owner.settings = settings
    owner.store = MaskStore(info.frame_count, info.proc_height, info.proc_width)
    apply_prompts(engine, prompts, owner.store)

    t0 = time.time()
    track(engine, prompts, owner.store, direction,
          progress=lambda f, n, _: print(f"\r  tracked {n} frames", end="", flush=True))
    print(f"\n  tracking took {time.time() - t0:.1f}s")
    paths = export(owner)
    for kind, path in paths.items():
        print(f"  {kind:7s} -> {path}")


if __name__ == "__main__":
    sys.exit(main())
