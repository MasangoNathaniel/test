"""Sofwire ad — alternate music bed in the client's reference style (Desi trap / drill).

Reference (assets/ref/client_music_ref.mp3, another ad with VO on top), measured in its
music-only stretches: ~140 BPM, A harmonic minor (sits on E major = V with G#), a tuned
drum hit with a fast downward pitch drop on every beat (fundamental + octave), heavy sub,
warm/dark top end (little energy above 6 kHz).

Same edit points as sound.py:
  0.00–6.80  4-bar intro: harmonium drone, filtered sitar riff, tabla comes in, build
  6.80       DROP on the blue fill ("Don't worry"): 808, tuned hits, clap, hats, sitar
 15.24       tape-stop ("Don't waste"); break: sub swell + reverse sitar
 17.05       logo: big hit, Am resolve, sitar flourish, rings out

usage: python3 sound_desi.py <voiceover.wav 48k 16-bit> <out_dir>
Re-uses sound.py's SFX and ducking (same stems/bed/mix-check file names).
"""
import sys
import numpy as np
import sound as S
from sound import (FS, N, DUR, DROP, STOP, T1, T, n, mtof, add, blank, lp, hp, bp, tv_filter,
                   saw, sine_sweep, env_exp, env_ar, noise, reverb, pingpong, IR, IR_SHORT)

BPM = 140.0
B = 60 / BPM
SX = B / 4                       # one 16th
rng = np.random.default_rng(23)

CHORDS = [  # (808 root, drone voicing)  Am – F – G – E
    ('A1', ['A3', 'C4', 'E4']),
    ('F1', ['F3', 'A3', 'C4']),
    ('G1', ['G3', 'B3', 'D4']),
    ('E1', ['G#3', 'B3', 'E4']),
]

# sitar riff, A harmonic minor: (16th step, note, length in 16ths, slide-in semitones)
RIFF = [
    [(0, 'A4', 2, -2), (3, 'C5', 1, 0), (4, 'E5', 2, -2), (7, 'D5', 1, 0), (8, 'C5', 2, 0),
     (10, 'B4', 1, 0), (11, 'C5', 1, 0), (12, 'B4', 2, 0), (14, 'A4', 2, 0)],
    [(0, 'C5', 2, -2), (3, 'A4', 1, 0), (4, 'C5', 2, 0), (6, 'D5', 2, 0), (8, 'E5', 3, -2),
     (12, 'D5', 1, 0), (13, 'C5', 1, 0), (14, 'A4', 2, 0)],
    [(0, 'B4', 2, -2), (3, 'D5', 1, 0), (4, 'G5', 2, -2), (7, 'F5', 1, 0), (8, 'E5', 2, 0),
     (10, 'D5', 1, 0), (11, 'E5', 1, 0), (12, 'D5', 2, 0), (14, 'B4', 2, 0)],
    [(0, 'G#4', 2, -1), (2, 'B4', 2, 0), (4, 'E5', 2, -2), (6, 'F5', 1, 0), (7, 'E5', 1, 0),
     (8, 'D5', 1, 0), (9, 'C5', 1, 0), (10, 'B4', 2, 0), (12, 'G#4', 2, -1), (14, 'B4', 2, 0)],
]


# ---------- instruments ----------
def sitar(m, dur, slide=0, bright=1.0):
    """Additive plucked string with a meend (slide into the note) and a jawari-style buzz bump."""
    d = dur + 0.35
    t = np.arange(int(d * FS)) / FS
    f = mtof(m) * 2 ** (slide * np.exp(-t / 0.045) / 12)
    ph = 2 * np.pi * np.cumsum(f) / FS
    x = np.zeros_like(t)
    for k in range(1, 22):
        fk = mtof(m) * k
        if fk > FS / 2 * 0.9:
            break
        bump = 1 + 1.6 * np.exp(-((fk - 2600) / 900) ** 2)       # jawari buzz region
        amp = bump / k ** 0.9 * bright ** (k / 8)
        x += amp * np.sin(k * ph * (1 + 0.0007 * k)) * np.exp(-t / (0.9 / (1 + 0.35 * k)))
    x *= np.minimum(1, t / 0.002)
    x *= env_ar(len(t), 0.0, min(0.12, d * 0.4))
    return lp(x, 7500) * 0.11


def tabla_na(f=520):
    d = 0.45; t = np.arange(int(d * FS)) / FS
    x = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t / dk)
            for r, a, dk in [(1, 1, 0.22), (2, 0.6, 0.15), (3, 0.4, 0.1), (4, 0.25, 0.07)])
    x += bp(noise(d), 1500, 5000) * np.exp(-t / 0.006) * 0.6
    return x * 0.35


def tabla_ge():
    d = 0.5; t = np.arange(int(d * FS)) / FS
    f = 78 * (1 + 0.45 * (1 - np.exp(-t / 0.08)))                  # bayan: pitch bends up
    x = np.sin(2 * np.pi * np.cumsum(f) / FS) * np.exp(-t / 0.18)
    x += lp(noise(d), 600) * np.exp(-t / 0.01) * 0.3
    return np.tanh(x * 1.5) * 0.45


def tabla_ka():
    d = 0.08
    return bp(noise(d), 300, 2500) * env_exp(d, 0.012) * 0.3


def tuned_hit(f, punch=1.0):
    """The reference's every-beat drum: fast pitch drop to f, plus an octave partial."""
    d = 0.42; t = np.arange(int(d * FS)) / FS
    fc = f * (1 + 1.6 * np.exp(-t / 0.035))
    x = np.sin(2 * np.pi * np.cumsum(fc) / FS) * np.exp(-t / 0.16)
    x += 0.55 * np.sin(2 * np.pi * np.cumsum(2 * fc) / FS) * np.exp(-t / 0.1)
    x[:int(0.004 * FS)] += hp(noise(0.004), 2000) * 0.4 * punch
    return np.tanh(x * 1.8) / np.tanh(1.8) * 0.8


def kick808(f0, dur, glide_to=None, glide_at=None):
    t = np.arange(int(dur * FS)) / FS
    f = np.full_like(t, f0)
    f *= 1 + 1.4 * np.exp(-t / 0.03)
    if glide_to:
        g0 = glide_at
        g = np.clip((t - g0) / 0.09, 0, 1)
        f *= (glide_to / f0) ** g
    ph = 2 * np.pi * np.cumsum(f) / FS
    x = np.sin(ph) + 0.9 * np.sin(2 * ph)                          # octave so it reads on phones
    x = np.tanh(x * 2.2) / np.tanh(2.2)
    return x * env_ar(len(t), 0.002, 0.06) * np.exp(-t / 1.6)


def drill_snare():
    d = 0.4; t = np.arange(int(d * FS)) / FS
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05)
    nz = bp(noise(d), 1200, 7000) * np.exp(-t / 0.09)
    return (body * 0.5 + nz * 0.8) * 0.45


def dark_hat(open_=False):
    d = 0.18 if open_ else 0.04
    return lp(hp(noise(d), 6000, 4), 12000) * env_exp(d, 0.07 if open_ else 0.012) * 0.3


def harmonium(m, dur, fc=1400):
    t = np.arange(int(dur * FS)) / FS
    vib = 1 + 0.003 * np.sin(2 * np.pi * 5.2 * t)
    ph = 2 * np.pi * np.cumsum(mtof(m) * vib) / FS
    x = sum(np.sin(k * ph) / k ** 1.2 * (1 if k % 2 else 0.6) for k in range(1, 14))
    x += 0.5 * saw(mtof(m) * 1.004, dur, hmax=14)
    return lp(x, fc) * env_ar(len(t), 0.25, 0.35) * 0.18


# ---------- arrangement ----------
def bar_time(b):
    return DROP + b * 4 * B


def build_music():
    perc, hits, bass, harm, lead, fx = (blank() for _ in range(6))
    hit_times = []

    for b in range(-4, 5):
        tb = bar_time(b)
        if tb >= STOP:
            break
        ci = b % 4
        root, voicing = CHORDS[ci]
        intro = b < 0
        lvl = (b + 4) / 4 if intro else 1.0                       # 0 .. 0.75 through the intro

        # drone: filter opens through the intro
        d = 4 * B + 0.3
        fc = 500 + 900 * lvl if intro else 1700
        for m in voicing:
            add(harm, harmonium(n(m), d, fc), max(tb, 0), 0.6 if intro else 0.45, rng.uniform(-.6, .6))

        # sitar: sparse + dark in the first intro bars, full riff from bar -2
        for st, note, ln, sl in RIFF[ci]:
            ts = tb + st * SX
            if ts < 0 or ts >= STOP - 0.05:
                continue
            if b < -2 and st >= 8:
                continue
            bright = 0.55 + 0.45 * lvl if intro else 1.0
            add(lead, sitar(n(note), ln * SX, sl, bright), ts + rng.uniform(0, 0.006),
                0.75 if intro else 1.0, rng.uniform(-0.25, 0.25))

        # tabla from bar -3
        if b >= -3:
            pat = {0: 'dha', 2: 'na', 3: 'tin', 4: 'ge', 6: 'na', 8: 'dha', 10: 'na', 11: 'tin',
                   12: 'ge', 13: 'ka', 14: 'na', 15: 'tin'}
            for st, bol in pat.items():
                ts = tb + st * SX
                if ts >= STOP:
                    break
                g = 0.55 + 0.45 * lvl if intro else 0.8
                sw = 0.01 if st % 2 else 0
                if bol in ('dha', 'ge'):
                    add(perc, tabla_ge(), ts + sw, g, -0.15)
                if bol in ('dha', 'na'):
                    add(perc, tabla_na(), ts + sw, g * 0.8, 0.25)
                if bol == 'tin':
                    add(perc, tabla_na(780), ts + sw, g * 0.45, 0.35)
                if bol == 'ka':
                    add(perc, tabla_ka(), ts + sw, g, 0)

        # tuned hits on every beat: muffled from bar -2, full after the drop
        if b >= -2:
            fr = mtof(n(root) + 24)                                 # ~ 80–110 Hz, like the ref's drop target
            for k in range(4):
                tk = tb + k * B
                if tk >= STOP or (b == -1 and k == 3):
                    continue
                h = tuned_hit(fr)
                if intro:
                    h = lp(h, 400 + 600 * (b + 2))
                add(hits, h, tk, 0.55 if intro else 1.0)
                hit_times.append(tk)

        # hats: 8ths, triplet roll at the bar end (groove), quiet 8ths in the last intro bars
        if b >= -2:
            for s in range(8):
                ts = tb + s * B / 2
                if ts >= STOP:
                    break
                add(perc, dark_hat(), ts + (0.012 if s % 2 else 0), 0.5 if intro else 0.9, 0.3)
            if not intro:
                r0 = tb + 3 * B
                for k in range(6):                                  # 16th-triplet roll on beat 4
                    tr = r0 + k * B / 6
                    if tr < STOP:
                        add(perc, dark_hat(), tr, 0.5 + 0.07 * k, 0.3)

        if not intro:
            # 808: root held through the bar, glides to the next root on the last 8th
            nroot = CHORDS[(ci + 1) % 4][0]
            f0, f1 = mtof(n(root)), mtof(n(nroot))                 # A1 55 Hz … E1 41 Hz: real sub
            dur = min(4 * B, STOP - tb)
            add(bass, kick808(f0, dur, f1 if b < 4 else None, 3.5 * B), tb, 0.9)
            add(bass, kick808(f0, 0.5) * 0.6, tb + 2.5 * B, 0.6) if tb + 2.5 * B < STOP else None
            # drill snare on 3, ghost on the "a" of 4 every other bar
            add(perc, drill_snare(), tb + 2 * B, 0.85)
            if b % 2 == 1 and tb + 3.75 * B < STOP:
                add(perc, drill_snare(), tb + 3.75 * B, 0.35)

    # build: snare roll + riser over the last intro bar, silent last beat
    r0 = bar_time(-1)
    for k in range(12):
        tr = r0 + k * B / 4
        add(perc, drill_snare(), tr, 0.12 + 0.4 * (k / 11) ** 2)
    d = DROP - r0
    rz = tv_filter(noise(d), np.geomspace(400, 7000, int(d * FS)), 'high') * np.linspace(0, 1, int(d * FS)) ** 2.4
    add(fx, rz, r0, 0.13)
    for tc, g in [(DROP, 0.25), (11.40, 0.15)]:
        add(fx, lp(hp(noise(1.8), 4000), 10000) * env_exp(1.8, 0.5), tc, g)
    add(hits, tuned_hit(mtof(n('A3')), 1.3), DROP, 0.6)          # extra weight on the drop

    # tape stop on the music buses at STOP, silence till the logo
    def tape_stop(buf, t_end, length=0.22):
        i1 = T(t_end); i0 = i1 - int(length * FS)
        seg = buf[:, i0 - int(length * FS):i1].copy()
        m = i1 - i0
        rate = np.linspace(1, 0.05, m) ** 1.6
        pos = np.cumsum(rate); pos = pos - pos[0] + int(length * FS)
        out = np.vstack([np.interp(pos, np.arange(seg.shape[1]), seg[c]) for c in range(2)])
        buf[:, i0:i1] = out * np.linspace(1, 0.2, m)
        buf[:, i1:T(T1) - int(0.02 * FS)] = 0
    for bus in (perc, hits, bass, harm, lead):
        tape_stop(bus, STOP)

    # break: sub swell on E + reversed sitar phrase into the logo
    d = T1 - STOP
    sw = np.sin(2 * np.pi * mtof(n('E2')) * np.arange(int(d * FS)) / FS) * np.linspace(0, 1, int(d * FS)) ** 2
    add(bass, sw, STOP, 0.22)
    ph = np.zeros(int(1.3 * FS))
    for k, m in enumerate(['E5', 'D5', 'C5', 'B4', 'G#4']):
        s = sitar(n(m), 0.25)
        i = int(k * 0.12 * FS); ph[i:i + len(s)] += s[:len(ph) - i]
    st = reverb(np.vstack([ph, ph]))
    rev = (np.vstack([ph, ph]) * 0.4 + st)[:, ::-1]
    add(fx, rev * np.linspace(0, 1, rev.shape[1]) ** 2, T1 - rev.shape[1] / FS, 0.5)

    # logo: big hit, Am resolve, sitar flourish up, tabla tail, ring out
    dl = DUR - T1 + 0.5
    for m in ['A3', 'C4', 'E4', 'A4']:
        add(harm, harmonium(n(m), dl, 1500), T1, 0.7, rng.uniform(-.6, .6))
    add(bass, kick808(mtof(n('A1')), dl) * np.exp(-np.arange(int(dl * FS)) / FS / 1.0), T1, 1.0)
    add(hits, tuned_hit(mtof(n('A2')), 1.4), T1, 1.1); hit_times.append(T1)
    for k, (m, sl) in enumerate([('A4', -2), ('C5', 0), ('E5', 0), ('G#5', -1), ('A5', -2)]):
        add(lead, sitar(n(m), 0.9 if k == 4 else 0.2, sl), T1 + 0.05 + k * SX, 1.0, (-0.3, 0.3)[k % 2])
    for k in range(1, 5):
        tk = T1 + k * B
        if tk < DUR - 0.3:
            add(perc, tabla_ge(), tk, 0.5 - 0.08 * k, -0.15)
            add(perc, tabla_na(), tk + 2 * SX, 0.4 - 0.07 * k, 0.25)
    add(fx, lp(hp(noise(2.5), 3500), 9000) * env_exp(2.5, 0.8), T1, 0.22)

    # light sidechain from the hits onto drone and lead
    sc = np.ones(N)
    for tk in hit_times:
        i = T(tk); m = min(int(0.3 * FS), N - i)
        if m <= 0:
            continue
        tt = np.arange(m) / FS
        sc[i:i + m] = np.minimum(sc[i:i + m], 1 - 0.4 * np.exp(-tt / 0.09))
    harm *= sc; lead *= 1 - (1 - sc) * 0.5

    lead_wet = reverb(lead, mix=0.28) + pingpong(lead, 3 * B / 4, 0.3, lp_fc=3500)
    harm_wet = reverb(harm, mix=0.25)
    perc_wet = reverb(perc, IR_SHORT, 0.12)
    fx_wet = reverb(fx, mix=0.25)
    music = perc + perc_wet + hits * 0.9 + bass * 0.85 + harm + harm_wet + lead + lead_wet + fx + fx_wet
    # master: small presence lift (ref sits ~4 dB brighter at 2–8 kHz), sub clean-up, soft clip
    music = np.vstack([music[c] + 0.7 * bp(music[c], 2000, 8000) for c in range(2)])
    music = np.vstack([np.tanh(hp(music[c], 30) * 0.9) for c in range(2)])
    fade = np.ones(N); fl = int(0.4 * FS); fade[-fl:] = np.linspace(1, 0, fl) ** 1.5
    return music * fade


if __name__ == '__main__':
    S.mix_and_write(sys.argv[1], sys.argv[2], build_music(), S.build_sfx())
