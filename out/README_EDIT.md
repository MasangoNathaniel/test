# Sofwire ad — edit package (9:16, 1080×1920, 30fps, 23.10s)

## Files
- `sofwire_ad_preview.mp4`: quick-look preview with the voiceover (compressed, for sharing).
- `sofwire_graphics_prores.mov`: full graphics track, ProRes 422 HQ, no audio. This is the master.
- `shots/S1…S6.mov`: the same master cut at each shot change, so you can zoom, retime or swap shots one by one.
- `voiceover.m4a`: the original voiceover.
- `Sofwire_Ad_timeline.fcpxml`: timeline for DaVinci Resolve.

## Into DaVinci Resolve
1. Copy this folder to your machine.
2. File → Import → Timeline… → `Sofwire_Ad_timeline.fcpxml`.
3. Resolve will say media is offline. Relink it to the `shots/` folder and to `voiceover.m4a`.
   The paths in the XML are placeholders (`/Sofwire_Ad/...`).
4. Project settings: 1080×1920, 30fps (vertical).

If the XML gives you any trouble, the six shots are numbered. Drop them on V1 in order with `voiceover.m4a` on A1 at 0:00 and they line up exactly.

## Left for you
- **Camera moves:** none baked in. Suggested punch-ins are in the cue sheet.
- **Music:** A2 is empty. The refs use upbeat electronic/house at about 120–125 BPM, ducked under the voiceover.
- **Sound effects:** A3 is empty. Cues are below.

## SFX / zoom cue sheet
| Time | Shot | What happens on screen | Suggested SFX | Zoom idea |
|---|---|---|---|---|
| 0:00.05 | S1 | Power icon glitches on | digital glitch / power-on blip | — |
| 0:00.62 | S1 | "ZERO" pops | pop/thump | quick punch-in 105% |
| 0:00.85 | S1 | "SALES" pops | pop | — |
| 0:00.70–3.80 | S1 | Red graph line falls | descending synth / riser-down | slow push-in |
| 0:02.05 | S1 | "POOR ENGAGEMENT" | pop | — |
| 0:03.00 | S1 | "BAD LEADS?" | heavier pop | punch-in on "LEADS?" |
| 0:04.30 | S2 | Cut to dashboard; "BUSINESS OWNER?" | whoosh | — |
| 0:04.60–8.20 | S2 | Ad spend counts up | cash-register ticks / counter | slow push on card |
| 0:06.75 | S2 | "NO RESULTS?" pops, blue underline swipes | error buzz + swipe | — |
| 0:08.42 | S2 | Logo ring pops in centre | pop | — |
| 0:08.62–9.00 | S2→S3 | Blue circle grows to fill the frame | big whoosh / riser | zoom-through feels great here |
| 0:09.02 | S3 | "DON'T WORRY." | pop pop | — |
| 0:09.95–10.30 | S3 | "FOR YOUR BRAND" | pops | — |
| 0:11.20 | S3 | "FREE" tag slams in | impact / stamp | shake 2–3 frames |
| 0:11.45 / 11.75 | S3 | CONSULTANCY / MEETING slide up | swish ×2 | — |
| 0:12.95 | S4 | Cut to dark; "EXACT ACTION PLAN" | whoosh | — |
| 0:14.60 / 15.75 / 16.60 | S4 | Checklist ticks | tick/ding ×3 | — |
| 0:15.20–17.50 | S4 | Bars rise and the arrow climbs (the answer to S1's falling line) | rising synth | slow push-in |
| 0:18.10 | S5 | Hard cut "DON'T WASTE" | hit | punch-in |
| 0:18.85 | S5 | Clock on blue | clock tick | — |
| 0:19.20 | S5 | Red strike | slash | — |
| 0:19.55 | S5 | $ on dark | coin / cash | — |
| 0:19.85 | S5 | Red strike | slash | — |
| 0:20.38–21.10 | S6 | Logo build: blue ring and power icon, then letters slide up | soft whoosh + logo sting | gentle zoom-out settle |
| 0:21.15 | S6 | "RESERVE YOUR SEAT" button pops (pulses from 21.6) | UI click | — |
| 0:22.30 | S6 | "RIGHT NOW" | pop | — |

## Notes
- Word timings come from the voiceover's pauses and energy dips, not a transcript. Each beat lands on its phrase, but nudge a pop by a frame or two if a word reads early or late.
- Fonts: Inter Display (Black/Bold). Colours: Sofwire blue #0B52D0, a lighter blue #5C91FF for text on dark, charcoal #231F20, and red #FF4242 for the "bad" stats.
- To re-render after copy changes: `python3 build/render.py build/logo out/sofwire_graphics_prores.mov`
