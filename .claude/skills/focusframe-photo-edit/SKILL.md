---
name: focusframe-photo-edit
description: Edit photos in the user's own FocusFrame photography style (their signature tone, colour grade and mood, measured from their portfolio at focusframe50.pixieset.com). Use this whenever the user asks to edit, grade, colour-correct, retouch, "do my edit on", batch-process or make photos "look like my work", even if they don't mention a style or this skill. Also use it to (re)build or update the style profile from new reference photos, or to describe their editing style. Not for video grading.
---

# FocusFrame photo editing

The user is a photographer (and video editor) with a consistent editing look.
This skill stores that look as a measured **style profile** plus a written
**style guide**, and applies it to new photos so they match the portfolio.

Files:
- `style/profile.json`: measured numbers (tone curve, colour grade per tonal band, saturation per hue, vignette) plus hand-tuned `adjustments` and `notes`.
- `style/STYLE.md`: the style in words: what makes the look, what to protect, what to avoid. Read it before every edit.
- `style/contact_sheet.jpg`: thumbnails of the reference photos. View it to remind yourself what "right" looks like.
- `scripts/analyze_style.py`: measures reference photos and writes the profile and contact sheet.
- `scripts/apply_style.py`: applies the profile to new photos (adaptive, non-destructive).

Requires Python 3 with Pillow and numpy.

## Step 0: check the style has been captured

Open `style/profile.json`. If `"status"` is `"pending"`, the user's style hasn't
been measured yet. Don't quietly fall back to a generic "nice" edit, because the
whole point is that the edits look like *their* work. Tell them, and build the
profile first (next section) from the portfolio or from photos they provide.

## Building or refreshing the style profile

1. **Get 15–40 finished, edited photos** that represent the look. Sources, in order of preference:
   - The portfolio at https://focusframe50.pixieset.com/ . Pixieset pages load images from `images.pixieset.com`. Download the largest size available from each gallery. If the network blocks the site, say so and ask for another source.
   - A folder the user points to: in the repo, an upload, or a Google Drive folder through the Drive connector.
   Use final edits, not raw files. A mix of subjects (portrait, landscape, low light) gives a profile that generalises.
2. **Measure:**
   ```bash
   python3 scripts/analyze_style.py --source "<where they came from>" \
     --out style/profile.json --sheet style/contact_sheet.jpg <photos or folders>
   ```
   Check `consistency` in the output. Low spread means a tight, consistent look. High spread means mixed styles (e.g. bright-airy portraits next to moody street work). In that case ask whether to make separate profiles (`style/profile-<name>.json`) rather than averaging two looks into mud.
3. **Look at the photos yourself.** Numbers capture tone and colour but not intent. View the contact sheet and several full images, then write `style/STYLE.md` covering:
   - Overall mood in one line (e.g. "warm, soft, filmic; lifted blacks; muted greens").
   - Tone: brightness, contrast, how deep the blacks go (crushed or faded), highlight roll-off.
   - Colour: white-balance lean, shadow and highlight tints (split toning), which hues are pushed or muted, skin-tone handling.
   - Finishing: grain, vignette, sharpness/clarity, B&W usage, typical crops/aspect ratios.
   - Things that would break the look (e.g. "never oversaturated skies", "skin never orange").
4. **Tune `adjustments`** in the profile for what statistics can't infer: `hue_shifts` in degrees per hue (e.g. greens toward teal `"green": -12`; positive values rotate toward the next hue up the colour wheel), `grain` (0–3, Lab L units), `sharpen` (unsharp-mask percent, 0–150), `vignette_override` (0–0.4, or null to measure), `force_monochrome`. Re-running the analysis keeps these.
5. Show the user a summary of what you found and a before/after on one of their photos, and ask if it matches how they see their own style. Update the notes from their feedback, because their words outrank the statistics.

## Editing photos

1. Find the inputs. Accept JPEG, PNG, TIFF or WebP. RAW files (.CR3, .NEF, .ARW, .DNG) need converting first. `rawpy` isn't installed by default, so ask for exports or install it if the network allows. Never overwrite originals.
2. Read `style/STYLE.md`, then run:
   ```bash
   python3 scripts/apply_style.py --profile style/profile.json --out <output dir> --compare <photos or folder>
   ```
   - For a set from one shoot, add `--match-set` so every frame gets the same correction and the set stays consistent. Without it each frame is corrected on its own, which suits mixed one-offs.
   - Knobs (0 = as shot, 1 = full match): `--tone` (0.75), `--color` (0.85), `--keep-exposure` (0.5; raise it for night or deliberately low-key shots so they aren't pulled to daylight brightness), `--vignette` (1.0). `--bw` / `--colour` force monochrome or colour.
3. **Review every before/after** in `<output dir>/compare/` by viewing the images. Things to catch:
   - Skin tones gone orange, grey or green: lower `--color` or adjust the orange/red `hue_shifts`.
   - Night or low-key shots dragged too bright: raise `--keep-exposure`.
   - High-key shots dragged too dark, or clipped highlights: lower `--tone`.
   - A scene with a strong colour cast (sunset, neon, stage light) that the grade fights: lower `--color` for that photo. Keeping the mood of the moment matters more than matching the average.
   Re-run individual photos with different settings where needed. The script is deterministic, so iterate freely.
4. **Deliver**: put the finals in the output folder (the user's choice, otherwise `edited/` next to the originals) as `<name>_ff.jpg`. `edit_log.json` records the exact parameters per photo. Tell the user briefly what was done and anything you handled differently (e.g. "kept the warm sunset cast on 3 and 7 rather than neutralising it"). Send the compare images if the user can view files.

Local fixes like spot healing, object removal, masking a sky or relighting a face are outside what these scripts do. Say so. If Adobe tools are connected (e.g. `image_fill_area`, `image_select_subject`), offer to use them for those steps, then finish with the style pass so the look stays consistent.

## When the user gives feedback

Treat comments like "too warm", "my blacks are deeper than that" or "less saturated greens" as corrections to the *style*, not just that photo. Update `profile.json` adjustments or the notes in `STYLE.md` so the next edit is right first time. Mention that you updated the saved style.

## Lightroom / Camera Raw presets

If the user wants the look as a preset for their own workflow, translate the profile into Lightroom slider values (tone curve points from `L_percentiles`, split toning/colour grading from `tint`, HSL from `hues` and `hue_shifts`, grain, vignette) and write an `.xmp` preset. Tell them it's an approximation to fine-tune, since the script's adaptive matching can't be expressed as one fixed preset.
