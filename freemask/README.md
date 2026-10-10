# FreeMask: Magic Mask for the free DaVinci Resolve

FreeMask does the job of Resolve Studio's **Magic Mask** on any computer. You click on a
person or object, it tracks your selection **forwards and backwards** through the shot, and
it exports a matte that the **free** version of DaVinci Resolve can use for grading,
keying, blurring or compositing.

The tracking is done by Meta's **SAM 2.1** (Segment Anything 2) video model. It's one of
the strongest openly available video segmentation models: it works on people, faces,
cars, animals, products, skies, and almost anything else you can click on. FreeMask adds
the parts that make it work like Magic Mask: click/box selection, multi-frame
corrections, bidirectional tracking, Magic-Mask-style refine controls, and matte export.

| Magic Mask feature                         | FreeMask                                                      |
|--------------------------------------------|---------------------------------------------------------------|
| Draw strokes on the subject                | Left click = add, right click = subtract, drag = box          |
| Track forward / backward / both            | `]` / `[` / `T`                                                |
| Fix a frame where it drifts, re-track      | Click on any frame, press `T` again. All marked frames are used |
| Multiple masks                             | Objects `1`–`9`, combined into one matte (`H` hides one)       |
| Quality: Faster / Better                   | `--model tiny/small/base/large` and `--quality`               |
| Smart Refine                               | Edge-aware guided-filter snap to real image edges             |
| Expand / Contract, Blur                    | `grow`, `feather`                                             |
| Consolidate (reduce chatter)               | `consolidate` (temporal smoothing)                            |
| Matte finesse: denoise, clean black/white  | `despeckle`, `fill holes`, `clean black`, `clean white`        |
| Invert                                     | `invert`                                                      |

---

## 1. Install (one time)

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on
Windows tick *"Add python.exe to PATH"*).

* **Windows:** double-click `install_windows.bat`. If you have an NVIDIA GPU, it installs
  the GPU build automatically.
* **Mac / Linux:** in a terminal, run `./install_mac_linux.sh`. Apple Silicon Macs use
  the GPU (MPS) automatically.

The first time you run FreeMask it downloads the model (~180 MB for `small`).

## 2. Mask a shot

```bash
./freemask.sh path/to/shot.mov          # Mac / Linux
freemask.bat path\to\shot.mov           # Windows (or drag the clip onto freemask.bat)
```

Image-sequence folders (EXR/DPX/TIFF/PNG/JPG) work too. Pass the folder instead of a file.

1. Scrub to a frame where the subject is clear (`A`/`D` or the slider).
2. **Left click** on the subject. The mask appears instantly. **Right click** on anything
   that got included by mistake, or **drag a box** around the subject.
3. Press **`T`** to track forwards *and* backwards. It plays back while it tracks.
4. Scrub through. Wherever the mask slips, click to correct that frame and press `T` again.
   Every corrected frame becomes a keyframe for the whole track.
5. Adjust the **Refine** window (grow, feather, smart refine, consolidate…). Press `V` to
   view the finished matte or the cutout.
6. Press **`E`** to export. Press **`P`** to save your session so you can come back later.

| Key | Action | Key | Action |
|---|---|---|---|
| Left click / drag | include point / box | `T` or Space | track both ways |
| Right click | exclude point | `]` / `[` | track forward / backward |
| `Z` | undo last mark | `C` | clear marks on this frame |
| `A` `D` / arrows | prev / next frame | `W` `S` | −10 / +10 frames |
| `N` | next marked frame | `G` / `J` | first / last frame |
| `1`–`9` | choose object | `H` | hide/show object in matte |
| `V` | overlay → matte → cutout → original | `?` | help |
| `E` | export | `P` | save session |
| `Q` / Esc | quit (Esc also stops tracking) | | |

### Long shots and speed

Work on one shot at a time, as you would with Magic Mask. Use `--start`/`--end` (source
frame numbers) to process just part of a long file. Rough speed estimates (they vary with
resolution and how big the subject is):

| Hardware | `small` model at default quality |
|---|---|
| NVIDIA RTX GPU | ~10–25 frames/sec |
| Apple Silicon (M1–M4) | ~2–6 frames/sec |
| CPU only | ~0.5–2 frames/sec. Use `--model tiny --quality 720` |

SAM 2 keeps the shot's frames in memory, roughly 12 MB per frame (a 300-frame shot needs
about 4 GB of RAM). Split very long shots with `--start`/`--end`.

### Headless / batch mode

Coordinates are in source pixels, and frame numbers are source frame numbers:

```bash
./freemask.sh shot.mov --headless --point 120:960,540 --neg 120:1500,300 --format png,video
./freemask.sh shot.mov --headless --box 0:700,200,1250,1080@1 --point 0:300,600@2   # two objects
./freemask.sh shot.mov --headless --session shot_freemask/shot_session.json        # re-run a saved session
```

Run `./freemask.sh --help` for every option.

## 3. Outputs

Everything goes to `<clip name>_freemask/` next to the clip (or `--out`):

| Format (`--format`) | What you get | Best for |
|---|---|---|
| `png` (default) | `shot_matte/shot_matte_000000.png…`, a 16-bit grayscale matte sequence, white = selected | Colour page external matte, Fusion |
| `video` | `shot_matte.mov`, a ProRes 422 HQ grayscale matte | Easier media management |
| `cutout` | `shot_cutout/…png`, RGBA with the matte as alpha | Edit page compositing |

File numbers match the source frame numbers. If you used `--start 100`, the first file is
`…_000100.png`, so you can line it up with the clip. The matte is always rendered at the
clip's full resolution.

## 4. Use the matte in DaVinci Resolve (free)

**A. Colour page (closest to Magic Mask)**

1. Import the matte (the `shot_matte` PNG sequence or `shot_matte.mov`) into the Media Pool.
2. Link it to your clip as a matte: in recent versions, right-click the matte in the Media
   Pool and use the **matte** option to attach it to the clip, or drag it onto the clip.
   The exact wording varies between Resolve versions.
3. On the Colour page, right-click in the node editor, choose **Add Matte**, and pick the
   matte. Connect its output to the **key input** (small triangle) of a node. That node's
   grade now only affects the selection. Invert it in the Key palette to grade everything
   else.

**B. Edit page (cutout)**

Export with `--format cutout`, put the PNG sequence on the track above the original clip,
and line it up. You now have the subject isolated with transparency, so you can grade,
blur or replace the background underneath it.

**C. Fusion page**

Drag the matte sequence into the Fusion node graph (it becomes a MediaIn). Use it as the
effect mask of any tool, or feed it into a **Matte Control** node to set your clip's alpha.

If a matte looks scaled or offset in Resolve, check that the matte and the clip have the
same resolution (FreeMask always exports at the source size) and that both start on the
same frame.

## How it works

```
clip ──► frames at tracking resolution ──► SAM 2.1 video predictor
                                             ▲  your clicks / boxes on any frames
                                             └► propagate forward + backward with memory
masks ──► despeckle / fill holes ──► consolidate (temporal) ──► upscale to full res
      ──► grow/shrink ──► smart refine (guided filter on the real frame) ──► feather
      ──► clean black/white ──► invert ──► PNG / MOV / RGBA
```

## Honest limitations

* FreeMask is a separate app, not a plugin inside Resolve. The free Resolve doesn't let
  outside tools run inside it, so you work on the shot here and import the matte.
* Fine hair and motion blur are good, but not as soft as a dedicated roto artist would make
  them. Use `smart refine` + `feather`.
* Without a GPU it works, but slowly. Use `--model tiny` and a lower `--quality`.

## Development

```bash
pip install pytest
python -m pytest tests      # uses a stand-in tracker, so no model download is needed
```

SAM 2 is © Meta Platforms, Inc., Apache-2.0 licensed.
