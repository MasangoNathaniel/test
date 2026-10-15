# Sofwire ad v3: edit package (9:16, 1080×1920, 30fps, 19.50s)

Re-timed to the client's **updated voiceover (v3, 19.05s)**, `voiceover_clean.wav`. This recording is already clean (pauses at −69 dB, no wind), so it is used as delivered, with no noise processing. Every on-screen word and sound effect is mapped onto the v3 word timestamps from ElevenLabs (`assets/voiceover_v3_words.json`), not estimated.

**Music (new):** a Desi trap beat in the style of the client's reference (`assets/ref/client_music_ref.mp3`): 140 BPM, A harmonic minor, sitar riff, tabla, tuned drum hits on every beat and a heavy 808. Same edit points as before: drop at 6.80s, tape-stop at 15.24s, logo resolve at 17.05s. The previous melodic-house track is kept in `audio/alt_house_music/` (also re-timed to v3).

## Files
| File | What |
|---|---|
| `sofwire_ad_preview_with_sound.mp4` | Preview with everything: voiceover, music and sound effects, −14 LUFS / −1.2 dBTP |
| `sofwire_graphics_master_h264.mp4` | **Graphics master for Resolve.** Grain-free, near-lossless (SSIM 0.9994), no audio. Add film grain in Resolve if you want the textured look |
| `sofwire_graphics_prores.mov` | ProRes 422 HQ master, grain-free (kept in the repo folder only) |
| `shots/S1…S6.mov` | The master split at each cut |
| `voiceover_clean.wav` | **Use this one.** The client's updated voiceover (v3), unprocessed, 48 kHz / 16-bit |
| `audio/older_v2_voiceover/` | The previous voiceover (v2): cleaned, gentle, spectral and original versions |
| `audio/sofwire_music_sfx_bed.wav` | New Desi-style music + sound effects, already ducked under the voiceover. Put it on A2 at 0:00 |
| `audio/music_unducked.wav` | Music stem, not ducked |
| `audio/sfx_unducked.wav` | All sound effects on one track, each placed on its cue |
| `audio/sfx_oneshots/*.wav` | 18 separate sound-effect files to place or swap yourself |
| `audio/full_mix_reference_-14LUFS.wav` | Finished mix, for reference |
| `audio/alt_house_music/` | The previous melodic-house version: bed, music stem, mix and preview (repo folder only) |
| `Sofwire_Ad_timeline.fcpxml` | Resolve timeline: V1 shots, A1 voiceover, A2 bed |

All music/SFX audio is 48 kHz / 24-bit stereo and sample-aligned from 0:00.

## Into DaVinci Resolve
1. File → Import → Timeline → `Sofwire_Ad_timeline.fcpxml`.
2. Relink the offline media to this folder (the paths in the XML are placeholders under `/Sofwire_Ad/...`).
3. Leave the voiceover and the bed at 0 dB, then master to −14 LUFS.
If the XML gives trouble: drop S1–S6 on V1 in order, the voiceover on A1 and the bed on A2, all at 0:00.

## Cue sheet (v3)
| Time | Word | On screen | Sound effect | One-shot |
|---|---|---|---|---|
| 0.02 | — | Power icon glitches on | glitch + power-on | 01 |
| 0.82 | zero | "ZERO" pops; red graph starts falling | heavy pop + falling tone | 04, 18 |
| 1.08 | sales | "SALES" | pop | 03 |
| 1.66 | poor | "POOR ENGAGEMENT" | pop | 02 |
| 2.74 | bad | "BAD LEADS?" | heavy pop | 04 |
| 3.45 | (agar) | Cut to dashboard | whoosh | 05 |
| 3.95 | business owner | "BUSINESS OWNER?", card slides in | pop + swipe | 03, 07 |
| 4.20–6.02 | — | Ad spend counts up | counter ticks | 12 |
| 5.34 | result | "NO RESULTS?" + underline | pop + error tone + swipe | 03, 16, 07 |
| 5.90 | (aa rahe) | Logo ring pops in | heavy pop | 04 |
| 5.90–6.46 | — | Blue circle fills the frame | big whoosh → **drop** | 06, 08 |
| 6.60 / 6.88 | don't / worry | "DON'T WORRY." | pops | 02, 03 |
| 7.70 / 7.84 / 8.04 | hum / aapke / brand | "FOR YOUR BRAND" | pops | 02–04 |
| 8.54 | offer | "WE'RE OFFERING A" | pop | 02 |
| 9.38 | free | "FREE" tag slams in | stamp | 09 |
| 9.60 / 10.18 | consultancy / meeting | Lines slide up (no BUSINESS line: v3 doesn't say it) | swipes | 07 |
| 10.63 | (jahan) | Cut to dark | whoosh | 05 |
| 11.32 / 12.28 | exact / batayenge | "EXACT ACTION PLAN" | pops | 03 |
| 12.74–13.11 | — | Bars rise, arrow climbs | rising pops | 02 |
| 13.28 / 13.97 / 14.49 | brand / lack / hai | Checklist ticks ×3 | dings | 17 |
| 14.97 | so | **Music tape-stops** | swipe | 07 |
| 15.14 / 15.36 | don't / waste | "DON'T WASTE" | deep impact + impact | 10 |
| 15.60 | your time | Clock on blue | clock ticks | 13 |
| 15.79 | — | Red strike | slash | 14 |
| 16.06 | and money | $ on dark | coin | 15 |
| 16.27 | — | Red strike | slash | 14 |
| 16.43 | — | Logo build begins; music blooms | whoosh + impact | 06, 08 |
| 16.55–16.84 | — | Letters slide up | ticks | 11 |
| 16.62 | click below | "CLICK BELOW ↓" | soft pop | 02 |
| 17.48 | reserve | "RESERVE YOUR SEAT" button | UI click + pop | 11, 03 |
| 18.46 | right now | "RIGHT NOW" | pop | 03 |

## What changed most recently
- **New voiceover (v3).** Same script, different pacing (19.05s, was 19.84s). Every cut, text pop, transition and sound effect is moved onto the matching word of the new read; the music's drop, stop and logo hit move with it (drop 6.46s, stop 14.97s, logo 16.43s). v3 says "free consultancy meeting" (no "business"), so the BUSINESS line is gone.
- Music replaced with a Desi trap beat matching the client's reference track; same cue points, same sound effects.

## What changed from v1
- New voiceover is 19.8s (was 23.1s); the whole cut is re-timed to it.
- **Sync fix:** "DON'T WORRY" now appears exactly on "so don't worry" (6.9s). In v1 it came after the blue transition, late against this read. The blue fill now lands just before the line.
- Each text pop, checklist tick and strike sits on its spoken word, from ElevenLabs word timestamps.
- Copy now follows the read: "FREE **BUSINESS** CONSULTANCY MEETING", and the first checklist item now reads "where your brand is lacking".
- Music re-arranged: a 3-bar intro, the drop at 6.80s, the tape-stop at 15.24s, the logo bloom at 17.05s.

Re-render: `SOFWIRE_VO_WORDS=assets/voiceover_v3_words.json` in front of each command re-times it to v3: `python3 build/render.py build/logo out/sofwire_graphics_prores.mov` · `python3 build/sound_desi.py <vo 48k 16-bit wav> out/audio` (house version: `build/sound.py`) · `python3 build/export_oneshots.py out/audio/sfx_oneshots` · `python3 build/denoise.py <vo 48k wav> out/voiceover_clean.wav 26`
(v1 versions kept as `build/render_v1_23s.py`, `build/sound_v1_23s.py`)
