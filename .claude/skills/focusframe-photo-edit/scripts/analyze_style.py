#!/usr/bin/env python3
"""Measure an editing style from a set of finished photos.

Usage:
    python3 analyze_style.py --out ../style/profile.json [--sheet ../style/contact_sheet.jpg] PHOTO_OR_DIR...

Writes a style profile (median tone curve, colour grade, saturation per hue,
vignette, noise) and a contact sheet so the style can also be judged by eye.
Re-running keeps the hand-tuned "adjustments" and "notes" of an existing profile.
"""

import argparse
import datetime
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from colorlib import HUE_NAMES, PERCENTILES, image_stats, load_image, rgb_to_lab

EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

DEFAULT_ADJUSTMENTS = {
    "hue_shifts": {name: 0 for name in HUE_NAMES},
    "grain": 0.0,
    "sharpen": 0,
    "vignette_override": None,
    "force_monochrome": None,
}


def collect(paths):
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTS and not f.name.startswith("._"))
        elif p.suffix.lower() in EXTS and not p.name.startswith("._"):
            files.append(p)
    return files


def median(values):
    values = [v for v in values if v is not None]
    return round(float(np.median(values)), 2) if values else None


def aggregate(per_image):
    colour = [s for s in per_image if not s["is_monochrome"]] or per_image
    prof = {
        "L_percentiles": {str(p): median([s["L_percentiles"][str(p)] for s in per_image]) for p in PERCENTILES},
        "contrast_std": median([s["contrast_std"] for s in per_image]),
        "chroma_mean": median([s["chroma_mean"] for s in colour]),
        "chroma_p90": median([s["chroma_p90"] for s in colour]),
        "monochrome_fraction": round(sum(s["is_monochrome"] for s in per_image) / len(per_image), 2),
        "tint": {band: [median([s["tint"][band][i] for s in colour]) for i in (0, 1)]
                 for band in ("shadows", "midtones", "highlights")},
        "hues": {name: {"fraction": round(float(np.mean([s["hues"][name]["fraction"] for s in colour])), 4),
                        "chroma": median([s["hues"][name]["chroma"] for s in colour])}
                 for name in HUE_NAMES},
        "vignette_ratio": median([s["vignette_ratio"] for s in per_image]),
        "noise": median([s["noise"] for s in per_image]),
    }
    # Spread across the set: low values mean the look is very consistent.
    prof["consistency"] = {
        "median_L_std": round(float(np.std([s["L_percentiles"]["50"] for s in per_image])), 2),
        "chroma_mean_std": round(float(np.std([s["chroma_mean"] for s in colour])), 2),
    }
    return prof


def contact_sheet(files, out, cell=360, cols=6):
    rows = math.ceil(len(files) / cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), (20, 20, 20))
    for i, f in enumerate(files):
        rgb, _ = load_image(f, max_side=cell - 8)
        im = Image.fromarray((rgb * 255).astype(np.uint8))
        x = (i % cols) * cell + (cell - im.width) // 2
        y = (i // cols) * cell + (cell - im.height) // 2
        sheet.paste(im, (x, y))
    sheet.save(out, quality=88)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True, help="profile.json to write")
    ap.add_argument("--sheet", help="optional contact-sheet JPEG to write")
    ap.add_argument("--source", default="", help="where the reference photos came from (URL, folder)")
    args = ap.parse_args()

    files = collect(args.inputs)
    if not files:
        raise SystemExit("No images found.")

    per_image = []
    for f in files:
        rgb, _ = load_image(f, max_side=1600)
        stats = image_stats(rgb_to_lab(rgb))
        stats["file"] = f.name
        per_image.append(stats)
        print(f"measured {f.name}: median L {stats['L_percentiles']['50']}, chroma {stats['chroma_mean']}"
              f"{' (B&W)' if stats['is_monochrome'] else ''}")

    out = Path(args.out)
    old = json.loads(out.read_text()) if out.exists() else {}
    profile = {
        "status": "measured",
        "measured_on": datetime.date.today().isoformat(),
        "source": args.source or old.get("source", ""),
        "image_count": len(files),
        "target": aggregate(per_image),
        "adjustments": {**DEFAULT_ADJUSTMENTS, **old.get("adjustments", {})},
        "notes": old.get("notes", ""),
        "per_image": per_image,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2))
    print(f"\nwrote {out} from {len(files)} images")
    print(json.dumps({k: v for k, v in profile["target"].items() if k != "hues"}, indent=2))

    if args.sheet:
        contact_sheet(files, args.sheet)
        print(f"wrote {args.sheet}")


if __name__ == "__main__":
    main()
