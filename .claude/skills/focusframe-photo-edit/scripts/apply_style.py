#!/usr/bin/env python3
"""Apply the measured FocusFrame style to photos.

Usage:
    python3 apply_style.py --profile ../style/profile.json --out OUT_DIR [options] PHOTO_OR_DIR...

The edit is adaptive: each photo is measured and then moved toward the style
target (tone curve, colour grade, saturation per hue, vignette), so a dark
night shot and a bright beach shot both land in the same look instead of
receiving one fixed preset. Originals are never modified.

Strength knobs (0 = leave as shot, 1 = full match):
    --tone            tone curve / contrast / black & white points   (default 0.75)
    --color           colour grade, white balance, saturation        (default 0.85)
    --keep-exposure   how much of the photo's own brightness to keep (default 0.5)
    --vignette        vignette matching                              (default 1.0)
    --match-set       measure all inputs together so a whole shoot gets one consistent edit
    --compare         also write before/after side-by-sides to OUT_DIR/compare/
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from analyze_style import aggregate, collect
from colorlib import (HUE_NAMES, PERCENTILES, band_weights, hue_angle, image_stats, interp_hue,
                      lab_to_rgb, load_image, radial_distance, rgb_to_lab, save_image)

CHUNK_ROWS = 512  # process big files in strips to keep memory modest


def build_params(target, source, adj, args):
    """Turn target-vs-source statistics into concrete edit parameters."""
    T, S = target["L_percentiles"], source["L_percentiles"]
    shift = args.keep_exposure * (S["50"] - T["50"])
    xs, ys = [], []
    for p in PERCENTILES:
        goal = T[str(p)] + (shift if 10 <= p <= 90 else 0)
        xs.append(S[str(p)])
        ys.append(S[str(p)] + args.tone * (goal - S[str(p)]))
    xs = np.array(xs) + np.arange(len(xs)) * 1e-3  # strictly increasing for interp
    ys = np.clip(np.maximum.accumulate(ys), 0, 100)
    xs = np.concatenate([[0], xs, [100]])
    ys = np.concatenate([[np.clip(ys[0] - xs[1], 0, ys[0])], ys, [np.clip(100 + ys[-1] - xs[-2], ys[-1], 100)]])

    mono = args.bw or (not args.colour and (adj.get("force_monochrome")
                                            if adj.get("force_monochrome") is not None
                                            else target["monochrome_fraction"] >= 0.5))

    if source["chroma_mean"] > 2:
        g = np.clip(target["chroma_mean"] / source["chroma_mean"], 0.6, 1.6)
        g = 1 + args.color * (g - 1)
    else:
        g = 1.0
    # Saturation scales the existing tint first, so the grade only adds what is still missing.
    tint = {band: np.clip([args.color * (target["tint"][band][i] - g * source["tint"][band][i]) for i in (0, 1)], -12, 12)
            for band in ("shadows", "midtones", "highlights")}
    per_hue = []
    for name in HUE_NAMES:
        th, sh = target["hues"][name], source["hues"][name]
        if th["chroma"] and sh["chroma"] and th["fraction"] > 0.01 and sh["fraction"] > 0.01:
            rel = (th["chroma"] / target["chroma_mean"]) / (sh["chroma"] / source["chroma_mean"])
            per_hue.append(1 + args.color * (np.clip(rel, 0.75, 1.35) - 1))
        else:
            per_hue.append(1.0)

    if adj.get("vignette_override") is not None:
        vig = float(adj["vignette_override"])
    else:
        vig = args.vignette * float(np.clip(source["vignette_ratio"] - target["vignette_ratio"], 0, 0.3))

    return {
        "curve_x": xs.tolist(), "curve_y": ys.tolist(), "monochrome": bool(mono),
        "tint": {k: v.tolist() for k, v in tint.items()}, "saturation": float(g),
        "hue_saturation": dict(zip(HUE_NAMES, map(float, per_hue))),
        "hue_shifts": {n: float(adj.get("hue_shifts", {}).get(n, 0)) for n in HUE_NAMES},
        "vignette": vig, "grain": float(adj.get("grain", 0)), "sharpen": int(adj.get("sharpen", 0)),
    }


def render(rgb, P, seed=0):
    h, w = rgb.shape[:2]
    out = np.empty_like(rgb)
    rng = np.random.default_rng(seed)
    sat = np.array([P["hue_saturation"][n] for n in HUE_NAMES])
    shifts = np.array([P["hue_shifts"][n] for n in HUE_NAMES])
    for y0 in range(0, h, CHUNK_ROWS):
        lab = rgb_to_lab(rgb[y0:y0 + CHUNK_ROWS])
        L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
        L = np.interp(L, P["curve_x"], P["curve_y"]).astype(np.float32)

        if P["monochrome"]:
            a, b = np.zeros_like(a), np.zeros_like(b)
        else:
            hue = hue_angle(a, b)
            if shifts.any():
                theta = np.radians(interp_hue(hue, shifts) * np.minimum(np.hypot(a, b) / 10, 1))
                a, b = a * np.cos(theta) - b * np.sin(theta), a * np.sin(theta) + b * np.cos(theta)
            factor = P["saturation"] * interp_hue(hue, sat)
            a, b = a * factor, b * factor
            ws, wm, wh = band_weights(L)
            for wgt, band in ((ws, "shadows"), (wm, "midtones"), (wh, "highlights")):
                a = a + wgt * P["tint"][band][0]
                b = b + wgt * P["tint"][band][1]

        if P["vignette"] > 0:
            r = radial_distance(h, w)[y0:y0 + CHUNK_ROWS]
            t = np.clip((r - 0.45) / 0.55, 0, 1)
            L = L * (1 - P["vignette"] * t * t * (3 - 2 * t))
        if P["grain"] > 0:
            L = L + rng.normal(0, P["grain"], L.shape).astype(np.float32)

        out[y0:y0 + CHUNK_ROWS] = lab_to_rgb(np.stack([np.clip(L, 0, 100), a, b], -1))
    return out


def side_by_side(before, after, path, height=900):
    ims = []
    for arr in (before, after):
        im = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
        im.thumbnail((10_000, height), Image.LANCZOS)
        ims.append(im)
    sheet = Image.new("RGB", (ims[0].width * 2 + 12, ims[0].height), (20, 20, 20))
    sheet.paste(ims[0], (0, 0))
    sheet.paste(ims[1], (ims[0].width + 12, 0))
    sheet.save(path, quality=88)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tone", type=float, default=0.75)
    ap.add_argument("--color", type=float, default=0.85)
    ap.add_argument("--keep-exposure", type=float, default=0.5)
    ap.add_argument("--vignette", type=float, default=1.0)
    ap.add_argument("--bw", action="store_true", help="force black & white")
    ap.add_argument("--colour", action="store_true", help="force colour even if the style is mostly B&W")
    ap.add_argument("--match-set", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--suffix", default="_ff")
    ap.add_argument("--format", default="jpg", choices=["jpg", "png", "tif"])
    args = ap.parse_args()

    profile = json.loads(Path(args.profile).read_text())
    if profile.get("status") != "measured":
        raise SystemExit("The style profile has not been measured yet. Run analyze_style.py on reference photos first.")
    target, adj = profile["target"], profile.get("adjustments", {})

    files = collect(args.inputs)
    if not files:
        raise SystemExit("No images found.")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.compare:
        (out_dir / "compare").mkdir(exist_ok=True)

    measure = lambda f: image_stats(rgb_to_lab(load_image(f, max_side=1600)[0]))
    pooled = aggregate([measure(f) for f in files]) if args.match_set else None

    log = {}
    for i, f in enumerate(files):
        rgb, exif = load_image(f)
        source = pooled or aggregate([measure(f)])
        P = build_params(target, source, adj, args)
        result = render(rgb, P, seed=i)
        dest = out_dir / f"{f.stem}{args.suffix}.{args.format}"
        im = save_image(result, dest, exif=exif)
        if P["sharpen"]:
            im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=P["sharpen"], threshold=2)).save(
                dest, **({"quality": 95, "subsampling": 0} if args.format == "jpg" else {}),
                **({"exif": exif} if exif else {}))
            result = np.asarray(Image.open(dest).convert("RGB"), dtype=np.float32) / 255
        if args.compare:
            side_by_side(rgb, result, out_dir / "compare" / f"{f.stem}_compare.jpg")
        log[f.name] = P
        print(f"{f.name} -> {dest.name}  (sat x{P['saturation']:.2f}, vignette {P['vignette']:.2f}"
              f"{', B&W' if P['monochrome'] else ''})")

    (out_dir / "edit_log.json").write_text(json.dumps({"settings": vars(args) | {"inputs": None}, "photos": log}, indent=2))


if __name__ == "__main__":
    main()
