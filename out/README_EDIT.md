# Sofwire ad v2: edit package (9:16, 1080×1920, 30fps, 19.83s)

Re-timed to the client's new voiceover (v2), now with background noise removed (`voiceover_clean.wav`). Every on-screen word and sound effect is locked to ElevenLabs word timestamps (`assets/voiceover_v2_words.json`), not estimated.

## Files
| File | What |
|---|---|
| `sofwire_ad_preview_with_sound.mp4` | Preview with everything: voiceover, music and sound effects, −14 LUFS / −1.2 dBTP |
| `sofwire_graphics_master_h264.mp4` | **Graphics master for Resolve.** Grain-free, near-lossless (SSIM 0.9994), no audio. Add film grain in Resolve if you want the textured look |
| `sofwire_graphics_prores.mov` | ProRes 422 HQ master with grain baked in (489 MB, kept in the repo folder only) |
| `shots/S1…S6.mov` | The master split at each cut |
| `voiceover_clean.wav` | Client voiceover v2, **background noise removed** (room rumble/hum cut by ~15 dB). Use this one |
| `audio/voiceover_clean_gentle.wav` | Lighter noise reduction (~9 dB), if the main one sounds too processed |
| `audio/voiceover_original_noisy.m4a` | The original recording, untouched |
| `audio/sofwire_music_sfx_bed.wav` | Music + sound effects, already ducked under the voiceover. Put it on A2 at 0:00 |
| `audio/music_unducked.wav` | Music stem, not ducked |
| `audio/sfx_unducked.wav` | All sound effects on one track, each placed on its cue |
| `audio/sfx_oneshots/*.wav` | 18 separate sound-effect files to place or swap yourself |
| `audio/full_mix_reference_-14LUFS.wav` | Finished mix, for reference |
| `Sofwire_Ad_timeline.fcpxml` | Resolve timeline: V1 shots, A1 voiceover, A2 bed |

All audio is 48 kHz / 24-bit stereo and sample-aligned from 0:00.

## Into DaVinci Resolve
1. File → Import → Timeline → `Sofwire_Ad_timeline.fcpxml`.
2. Relink the offline media to this folder (the paths in the XML are placeholders under `/Sofwire_Ad/...`).
3. Leave the voiceover and the bed at 0 dB, then master to −14 LUFS.
If the XML gives trouble: drop S1–S6 on V1 in order, the voiceover on A1 and the bed on A2, all at 0:00.

## Cue sheet (v2)
| Time | Word | On screen | Sound effect | One-shot |
|---|---|---|---|---|
| 0.05 | — | Power icon glitches on | glitch + power-on | 01 |
| 1.18 | zero | "ZERO" pops; red graph starts falling | heavy pop + falling tone | 04, 18 |
| 1.48 | sales | "SALES" | pop | 03 |
| 2.02 | poor | "POOR ENGAGEMENT" | pop | 02 |
| 3.06 | bad | "BAD LEADS?" | heavy pop | 04 |
| 3.80 | (agar) | Cut to dashboard | whoosh | 05 |
| 4.30 | business owner | "BUSINESS OWNER?", card slides in | pop + swipe | 03, 07 |
| 4.55–6.40 | — | Ad spend counts up | counter ticks | 12 |
| 5.78 | result | "NO RESULTS?" + underline | pop + error tone + swipe | 03, 16, 07 |
| 6.28 | (aa rahe) | Logo ring pops in | heavy pop | 04 |
| 6.28–6.80 | — | Blue circle fills the frame | big whoosh → **drop** | 06, 08 |
| 6.92 / 7.18 | don't / worry | "DON'T WORRY." | pops | 02, 03 |
| 8.10 / 8.28 / 8.56 | hum / aapke / brand | "FOR YOUR BRAND" | pops | 02–04 |
| 9.06 | offer | "WE'RE OFFERING A" | pop | 02 |
| 9.78 | free | "FREE" tag slams in | stamp | 09 |
| 10.00 / 10.36 / 10.88 | business / consultancy / meeting | Lines slide up | swipes | 07 |
| 11.40 | (jahan) | Cut to dark | whoosh | 05 |
| 12.30 / 12.66 | exact / batayenge | "EXACT ACTION PLAN" | pops | 03 |
| 13.2–13.7 | — | Bars rise, arrow climbs | rising pops | 02 |
| 13.88 / 14.52 / 14.96 | brand / lack / hai | Checklist ticks ×3 | dings | 17 |
| 15.24 | so | **Music tape-stops** | swipe | 07 |
| 15.44 / 15.68 | don't / waste | "DON'T WASTE" | deep impact + impact | 10 |
| 15.92 | your time | Clock on blue | clock ticks | 13 |
| 16.12 | — | Red strike | slash | 14 |
| 16.46 | and money | $ on dark | coin | 15 |
| 16.70 | — | Red strike | slash | 14 |
| 17.05 | — | Logo build begins; music blooms | whoosh + impact | 06, 08 |
| 17.30–17.65 | — | Letters slide up | ticks | 11 |
| 17.46 | click below | "CLICK BELOW ↓" | soft pop | 02 |
| 18.30 | reserve | "RESERVE YOUR SEAT" button | UI click + pop | 11, 03 |
| 19.12 | right now | "RIGHT NOW" | pop | 03 |

## What changed from v1
- New voiceover is 19.8s (was 23.1s); the whole cut is re-timed to it.
- **Sync fix:** "DON'T WORRY" now appears exactly on "so don't worry" (6.9s). In v1 it came after the blue transition, late against this read. The blue fill now lands just before the line.
- Each text pop, checklist tick and strike sits on its spoken word, from ElevenLabs word timestamps.
- Copy now follows the read: "FREE **BUSINESS** CONSULTANCY MEETING", and the first checklist item now reads "where your brand is lacking".
- Music re-arranged: a 3-bar intro, the drop at 6.80s, the tape-stop at 15.24s, the logo bloom at 17.05s.

Re-render: `python3 build/render.py build/logo out/sofwire_graphics_prores.mov` · `python3 build/sound.py <vo 48k wav> out/audio` · `python3 build/export_oneshots.py out/audio/sfx_oneshots` · `python3 build/denoise.py <vo 48k wav> out/voiceover_clean.wav 26`
(v1 versions kept as `build/render_v1_23s.py`, `build/sound_v1_23s.py`)
