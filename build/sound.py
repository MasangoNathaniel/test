"""Sofwire ad — music + sound design, synthesised and timed to the edit.

124 BPM melodic house in A minor (Am9 – Fmaj9 – Cmaj9 – G6/9).
  0.00–1.26  atmosphere + power-on glitch
  1.26–9.00  4-bar filtered intro (tension), filter opens, riser, ring whoosh
  9.00       DROP on the blue fill ("Don't worry")
  9.00–18.10 full groove; tape-stop at 18.10 ("Don't waste")
 18.10–20.35 break: sound design only, sub swell
 20.35–23.10 logo sting + soft groove, rings out

usage: python3 sound.py <voiceover.wav 48k stereo> <out_dir>
writes music.wav, sfx.wav (unducked stems) and bed_ducked.wav (music+sfx ducked under VO)
"""
import sys, os, wave
import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, fftconvolve

FS = 48000
DUR = 19.84
N = int(round(DUR * FS))
BPM = 124.0
B = 60 / BPM
DROP = 6.80
T0 = DROP - 12 * B          # first intro downbeat (bar -3)
STOP = 15.24
T1 = 17.05                  # logo section downbeat
import timemap                # SOFWIRE_VO_WORDS=<words.json> re-times everything to a newer VO read
TMAP = timemap.from_env()
CUE = TMAP.to_new if TMAP else (lambda t: t)    # v2-authored cue time -> output time
SAY_BUSINESS = TMAP is None or TMAP.says('business', 'free')
if TMAP:
    DUR, DROP, STOP, T1 = TMAP.dur, float(CUE(DROP)), float(CUE(STOP)), float(CUE(T1))
    N = int(round(DUR * FS)); T0 = DROP - 12 * B
rng = np.random.default_rng(11)


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12)

NOTE = {n: i for i, n in enumerate(['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'])}

def n(s):
    """'A3' -> midi"""
    name, octv = s[:-1], int(s[-1])
    return 12 * (octv + 1) + NOTE[name]

CHORDS = [  # (bass root, pad voicing, arp tones)
    ('A1', ['A3', 'C4', 'E4', 'G4', 'B4'], ['A4', 'C5', 'E5', 'G5', 'B5', 'E5']),
    ('F1', ['F3', 'A3', 'C4', 'E4', 'G4'], ['F4', 'A4', 'C5', 'E5', 'G5', 'C5']),
    ('C2', ['G3', 'B3', 'D4', 'E4', 'G4'], ['C5', 'E5', 'G5', 'B5', 'D6', 'G5']),
    ('G1', ['G3', 'B3', 'D4', 'E4', 'A4'], ['B4', 'D5', 'E5', 'G5', 'A5', 'D5']),
]


# ---------- dsp helpers ----------
def T(t):
    return int(round(t * FS))

def blank():
    return np.zeros((2, N))

def add(buf, x, t, gain=1.0, pan=0.0):
    """Mix mono or stereo x into buf at time t (s). pan -1..1 (mono only)."""
    i = T(t)
    if x.ndim == 1:
        l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        x = np.vstack([x * l * 1.414, x * r * 1.414])
    if i < 0:
        x = x[:, -i:]; i = 0
    m = min(x.shape[1], N - i)
    if m > 0:
        buf[:, i:i + m] += x[:, :m] * gain

def lp(x, fc, order=2):
    return sosfilt(butter(order, min(fc, FS / 2 * 0.95), 'low', fs=FS, output='sos'), x)

def hp(x, fc, order=2):
    return sosfilt(butter(order, fc, 'high', fs=FS, output='sos'), x)

def bp(x, f0, f1, order=2):
    return sosfilt(butter(order, [f0, min(f1, FS / 2 * 0.95)], 'band', fs=FS, output='sos'), x)

def tv_filter(x, fc_curve, kind='low', block=256):
    """Time-varying 2-pole filter (cutoff per block). x mono."""
    y = np.zeros_like(x); zi = None
    for s in range(0, len(x), block):
        fc = float(np.clip(fc_curve[min(s, len(fc_curve) - 1)], 30, FS / 2 * 0.9))
        sos = butter(2, fc, kind, fs=FS, output='sos')
        if zi is None:
            zi = sosfilt_zi(sos) * 0
        y[s:s + block], zi = sosfilt(sos, x[s:s + block], zi=zi)
    return y

def saw(f, dur, detune_cents=0.0, phase=None, hmax=48):
    t = np.arange(int(dur * FS)) / FS
    f = f * 2 ** (detune_cents / 1200)
    ph = rng.uniform(0, 2 * np.pi) if phase is None else phase
    k = np.arange(1, min(hmax, int(FS / 2 / f)) + 1)
    return (np.sin(2 * np.pi * f * np.outer(k, t) + ph * k[:, None]) / k[:, None]).sum(0) * (2 / np.pi)

def sine_sweep(f0, f1, dur, curve='exp'):
    t = np.arange(int(dur * FS)) / FS
    if curve == 'exp':
        f = f0 * (f1 / f0) ** (t / dur)
    else:
        f = f0 + (f1 - f0) * (t / dur)
    return np.sin(2 * np.pi * np.cumsum(f) / FS)

def env_exp(dur, decay, attack=0.002):
    t = np.arange(int(dur * FS)) / FS
    return np.minimum(1, t / attack) * np.exp(-t / decay)

def env_ar(n_s, a, r):
    e = np.ones(n_s); na, nr = int(a * FS), int(r * FS)
    if na: e[:na] = np.linspace(0, 1, na)
    if nr: e[-nr:] *= np.linspace(1, 0, nr)
    return e

def noise(dur):
    return rng.standard_normal(int(dur * FS))

def make_ir(dur=2.4, decay=0.9, lp_fc=6000, predelay=0.012):
    m = int(dur * FS); t = np.arange(m) / FS
    ir = np.zeros((2, m + int(predelay * FS)))
    for c in range(2):
        x = rng.standard_normal(m) * np.exp(-t * 6.9 / (decay * 2.4))
        x = lp(x, lp_fc) ; x = hp(x, 180)
        ir[c, int(predelay * FS):] = x
    return ir / np.sqrt((ir ** 2).sum() / 2)

IR = make_ir()
IR_SHORT = make_ir(0.9, 0.35, 8000, 0.005)

def reverb(x, ir=IR, mix=1.0):
    """x stereo (2,N); returns wet only, same length."""
    return np.vstack([fftconvolve(x[c], ir[c])[:x.shape[1]] for c in range(2)]) * mix

def pingpong(x, delay_s, fb=0.42, repeats=6, lp_fc=4500):
    """x stereo; returns wet ping-pong echoes."""
    d = T(delay_s); out = np.zeros_like(x); cur = x.mean(0)
    for k in range(1, repeats + 1):
        cur = lp(cur, lp_fc) * fb
        ch = k % 2
        out[ch, d * k:] += cur[:x.shape[1] - d * k]
    return out


# ---------- instruments ----------
def kick(punch=1.0):
    d = 0.5
    body = sine_sweep(165, 46, 0.09)
    body = np.concatenate([body, np.sin(2 * np.pi * 46 * np.arange(int((d - 0.09) * FS)) / FS + np.pi * 0)])
    body *= env_exp(d, 0.17, 0.001)
    click = hp(noise(0.006), 2500) * env_exp(0.006, 0.0015) * 0.35 * punch
    out = body; out[:len(click)] += click
    return np.tanh(out * 1.6) / np.tanh(1.6)

def clap():
    d = 0.35; x = np.zeros(int(d * FS))
    for k, off in enumerate([0, 0.009, 0.019]):
        i = int(off * FS); m = len(x) - i
        b = bp(rng.standard_normal(m), 900, 3200) * env_exp(m / FS, 0.012 if k < 2 else 0.12)[:m]
        x[i:i + len(b)] += b
    return x * 0.6

def hat(open_=False):
    d = 0.25 if open_ else 0.05
    x = hp(noise(d), 7500, 4) * env_exp(d, 0.09 if open_ else 0.014)
    return x * (0.5 if open_ else 0.4)

def shaker():
    d = 0.06
    return bp(noise(d), 5000, 12000) * env_exp(d, 0.02, 0.008) * 0.25

def pad_note(m, dur, fc_curve=None):
    x = sum(saw(mtof(m), dur, c, hmax=36) for c in (-9, -3, 4, 10)) / 4
    x = lp(x, 2600)
    if fc_curve is not None:
        x = tv_filter(x, fc_curve)
    return x * env_ar(len(x), 0.35, 0.5)

def pluck(m, dur=0.42, bright=4000):
    f = mtof(m)
    x = 0.6 * saw(f, dur, -5, hmax=30) + 0.6 * saw(f, dur, 6, hmax=30) + 0.3 * np.sin(2 * np.pi * 2 * f * np.arange(int(dur * FS)) / FS)
    e = env_exp(dur, 0.11, 0.002)
    # filter envelope approximated by mixing bright + dark versions
    xb, xd = lp(x, bright), lp(x, max(300, bright * 0.18))
    w = np.exp(-np.arange(len(x)) / FS / 0.05)
    return (xb * w + xd * (1 - w)) * e * 0.5

def bass_note(m, dur):
    f = mtof(m)
    t = np.arange(int(dur * FS)) / FS
    sub = np.sin(2 * np.pi * f * t)
    mid = lp(saw(f, dur, hmax=20), 380)
    return np.tanh((sub * 0.9 + mid * 0.5) * 1.3) * env_ar(len(t), 0.004, 0.04)

def bell(m, dur=1.6, idx=2.2):
    f = mtof(m); t = np.arange(int(dur * FS)) / FS
    modi = idx * np.exp(-t / 0.25)
    x = np.sin(2 * np.pi * f * t + modi * np.sin(2 * np.pi * f * 3.5 * t))
    return x * env_exp(dur, 0.45, 0.003) * 0.4


# ---------- arrangement ----------
def bar_time(b, origin=DROP):
    return origin + b * 4 * B

def chord_at(b):
    return CHORDS[b % 4]

def build_music():
    drums, bass, pads, arps, fx = blank(), blank(), blank(), blank(), blank()
    kick_times = []
    tl = np.arange(N) / FS

    # --- atmosphere 0 → T0: Am9 pad very dark, swelling
    for m in CHORDS[0][1]:
        d = T0 + 4 * B
        x = pad_note(n(m), d, np.full(int(d * FS), 380.0))
        x *= np.linspace(0, 1, len(x)) ** 1.0
        add(pads, x, 0.0, 0.22, rng.uniform(-.5, .5))

    # --- intro bars -4..-1 and groove bars 0..4
    for b in range(-3, 5):
        tb = bar_time(b)
        if tb >= STOP:
            break
        root, voicing, arp = chord_at(b)
        intro = b < 0
        # pad: filter opens across intro, fully open after drop
        d = 4 * B + 0.4
        if intro:
            f0 = 520 + (b + 3) * 480; f1 = f0 + 480
            curve = np.linspace(f0, f1, int(d * FS))
        else:
            curve = np.full(int(d * FS), 3200.0)
        for m in voicing:
            add(pads, pad_note(n(m), d, curve), tb, 0.15 if intro else 0.13, rng.uniform(-.7, .7))
        # kick: muffled in intro, out for last beat before the drop
        for k in range(4):
            tk = tb + k * B
            if tk >= STOP or (b == -1 and k == 3):
                continue
            kx = kick()
            if intro:
                kx = lp(kx, 260)
            add(drums, kx, tk, 0.38 if intro else 0.78)
            kick_times.append(tk)
        # hats & claps
        for s in range(16):
            ts = tb + s * B / 4
            if ts >= STOP:
                break
            swing = 0.012 if s % 2 else 0
            if intro:
                if b >= -2 and s % 2 == 1:
                    add(drums, shaker(), ts + swing, 0.5, 0.3)
            else:
                if s % 4 == 2:
                    add(drums, hat(True), ts, 0.55, -0.2)
                elif s % 2 == 1:
                    add(drums, hat(), ts + swing, 0.5, 0.35)
                if s in (4, 12):
                    add(drums, clap(), ts, 0.7)
        # bass (groove only): offbeat 8ths + a pickup
        if not intro:
            for k in range(4):
                tn = tb + k * B + B / 2
                if tn < STOP - 0.05:
                    add(bass, bass_note(n(root) + 12, B * 0.42), tn, 0.42)
            tn = tb + 3 * B + 3 * B / 4
            if tn < STOP - 0.05:
                add(bass, bass_note(n(root) + 24, B * 0.2), tn, 0.3)
        # arp: from bar -3; 16ths with a lilting pattern
        if b >= -3:
            pat = [0, None, 1, 2, None, 3, 1, None, 4, None, 2, 5, None, 3, 1, None]
            for s, idx in enumerate(pat):
                if idx is None:
                    continue
                ts = tb + s * B / 4
                if ts >= STOP:
                    break
                bright = 700 + (b + 3) * 650 if intro else 5200
                add(arps, pluck(n(arp[idx]), bright=bright), ts + (0.008 if s % 2 else 0), 0.52, (-0.45, 0.45)[s % 2])

    # --- riser into the drop (last intro bar)
    tr0, tr1 = bar_time(-1), DROP
    d = tr1 - tr0
    rz = tv_filter(noise(d), np.geomspace(500, 9000, int(d * FS)), 'high') * np.linspace(0, 1, int(d * FS)) ** 2.2
    add(fx, rz, tr0, 0.16)
    gl = sine_sweep(mtof(n('A3')), mtof(n('A5')), d) * np.linspace(0, 1, int(d * FS)) ** 2
    add(fx, lp(gl, 3000), tr0, 0.05)

    # crash/fill at bar 2 (S4 cut) and the drop
    for tc, g in [(DROP, 0.35), (float(CUE(11.40)), 0.22)]:
        add(fx, hp(noise(2.0), 4500) * env_exp(2.0, 0.55), tc, g)

    # --- tape stop at STOP on the music buses
    def tape_stop(buf, t_end, length=0.22):
        i1 = T(t_end); i0 = i1 - int(length * FS)
        seg = buf[:, i0 - int(length * FS):i1].copy()
        m = i1 - i0
        rate = np.linspace(1, 0.05, m) ** 1.6
        pos = np.cumsum(rate); pos = pos - pos[0] + int(length * FS)
        out = np.vstack([np.interp(pos, np.arange(seg.shape[1]), seg[c]) for c in range(2)])
        buf[:, i0:i1] = out * np.linspace(1, 0.2, m)
        buf[:, i1:T(T1) - int(0.02 * FS)] = 0  # silence the break (logo section added later)
    for bus in (drums, bass, pads, arps):
        tape_stop(bus, STOP)

    # --- break: low sub swell + reverse shimmer into the logo
    d = T1 - STOP
    sw = np.sin(2 * np.pi * mtof(n('C2')) * np.arange(int(d * FS)) / FS) * np.linspace(0, 1, int(d * FS)) ** 2
    add(bass, sw, STOP, 0.18)
    sting = sum(bell(n(m), 2.2, 1.4) for m in ['C5', 'E5', 'G5', 'B5', 'D6'])
    rev = reverb(np.vstack([sting, sting]))[:, ::-1][:, -int(1.2 * FS):]
    add(fx, rev * np.linspace(0, 1, rev.shape[1]) ** 2, T1 - 1.2, 0.20)

    # --- logo section: Cmaj9 bloom → Am9 tail, soft groove
    for i, (m_root, voicing) in enumerate([('C2', ['G3', 'B3', 'D4', 'E4', 'G4', 'C5']), ]):
        d = DUR - T1 + 0.5
        for m in voicing:
            add(pads, pad_note(n(m), d, np.full(int(d * FS), 3600.0)), T1, 0.14, rng.uniform(-.7, .7))
        add(bass, bass_note(n(m_root), 1.2) * env_exp(1.2, 0.6), T1, 0.6)
    for k, m in enumerate(['C5', 'E5', 'G5', 'B5', 'D6', 'E6']):   # sparkle arpeggio up
        add(arps, bell(n(m), 1.8, 1.8), T1 + 0.05 + k * B / 4, 0.32, (-0.5, 0.5)[k % 2])
    add(drums, kick(), T1, 1.0); kick_times.append(T1)
    for k in range(1, 6):
        tk = T1 + k * B
        if tk < DUR - 0.25:
            add(drums, lp(kick(), 1200), tk, 0.7); kick_times.append(tk)
        th = T1 + k * B - B / 2
        add(drums, hat(True), th, 0.35, -0.2)
    add(fx, hp(noise(2.5), 4000) * env_exp(2.5, 0.8), T1, 0.28)

    # --- sidechain pump on pads/arps/bass
    sc = np.ones(N)
    for tk in kick_times:
        i = T(tk); m = min(int(0.4 * FS), N - i)
        if m <= 0: continue
        tt = np.arange(m) / FS
        sc[i:i + m] = np.minimum(sc[i:i + m], 1 - 0.62 * np.exp(-tt / 0.11) * np.minimum(1, tt / 0.004 + 0.5))
    for bus in (pads, arps):
        bus *= sc
    bass *= 1 - (1 - sc) * 0.6

    # --- space
    arps_wet = reverb(arps, mix=0.35) + pingpong(arps, 3 * B / 4, 0.38)
    pads_wet = reverb(pads, mix=0.30)
    claps_wet = reverb(drums, IR_SHORT, 0.10)
    fx_wet = reverb(fx, mix=0.25)
    music = drums + claps_wet + bass + pads + pads_wet + arps + arps_wet + fx + fx_wet
    # gentle master glue: tilt + soft clip
    music = np.vstack([music[c] + 0.35 * hp(music[c], 5000) for c in range(2)])
    music = np.vstack([np.tanh(hp(music[c], 28) * 0.9) for c in range(2)])
    fade = np.ones(N); fl = int(0.35 * FS); fade[-fl:] = np.linspace(1, 0, fl) ** 1.5
    return music * fade


# ---------- sound design ----------
def pop(pitch=1.0, weight=0.0):
    d = 0.12
    x = sine_sweep(900 * pitch, 380 * pitch, d) * env_exp(d, 0.035, 0.001)
    click = bp(noise(0.006), 2000, 6000) * env_exp(0.006, 0.0012) * 0.3
    x[:len(click)] += click
    if weight:
        x += sine_sweep(140, 50, d) * env_exp(d, 0.06) * weight
    return x

def whoosh(d, f0=300, f1=5000, peak=0.75):
    x = tv_filter(noise(d), np.geomspace(f0, f1, int(d * FS)), 'low')
    x = hp(x, f0 * 0.6)
    t = np.arange(int(d * FS)) / d / FS
    e = np.where(t < peak, (t / peak) ** 2, np.exp(-(t - peak) / (1 - peak) * 4))
    return x * e

def swipe(d=0.22):
    return whoosh(d, 1200, 9000, 0.4) * 0.8

def impact(sub=55, d=1.0):
    t = np.arange(int(d * FS)) / FS
    x = np.sin(2 * np.pi * (sub + 40 * np.exp(-t / 0.03)) * t) * env_exp(d, 0.28)
    x += lp(noise(d), 400) * env_exp(d, 0.05) * 0.5
    x += bp(noise(d), 1500, 7000) * env_exp(d, 0.012) * 0.25
    return np.tanh(x * 1.5)

def tick(f=3500, d=0.02, g=1.0):
    return bp(noise(d), f * 0.7, f * 1.4) * env_exp(d, 0.003) * g

def slash():
    d = 0.18
    x = tv_filter(noise(d), np.geomspace(9000, 1200, int(d * FS)), 'low') * env_exp(d, 0.05, 0.004)
    ring = np.sin(2 * np.pi * 3100 * np.arange(int(d * FS)) / FS) * env_exp(d, 0.04) * 0.12
    return x + ring

def glitch(d=0.55):
    x = np.zeros(int(d * FS)); s = 0
    while s < len(x):
        L = int(rng.uniform(0.008, 0.04) * FS)
        if rng.random() < 0.6:
            f = rng.choice([220, 440, 880, 1320, 1760])
            seg = np.sign(np.sin(2 * np.pi * f * np.arange(L) / FS)) * rng.uniform(0.2, 0.6)
            x[s:s + L] = seg[:len(x) - s]
        s += L + int(rng.uniform(0.0, 0.03) * FS)
    x = np.round(x * 8) / 8
    return lp(x, 6000) * np.linspace(0.3, 1, len(x))

def power_on():
    d = 0.45
    x = sine_sweep(180, 720, d) * env_ar(int(d * FS), 0.05, 0.25) * 0.5
    return x + bell(n('A5'), d, 1.0)[:int(d * FS)] * 0.6

def error_tone():
    out = np.zeros(int(0.6 * FS))
    for k, m in enumerate(['E4', 'C4']):
        f = mtof(n(m)); d = 0.22
        t = np.arange(int(d * FS)) / FS
        x = lp(np.tanh(3 * np.sin(2 * np.pi * f * t)), 1400) * env_exp(d, 0.08, 0.004)
        i = int(k * 0.13 * FS); out[i:i + len(x)] += x
    return out * 0.6

def coin():
    return np.concatenate([bell(n('B6'), 0.08, 0.6)[:int(0.08 * FS)], bell(n('E7'), 0.7, 0.8)]) * 1.2

def build_sfx():
    """Cue times come from the ElevenLabs word timestamps of voiceover v2 (mapped by CUE to a newer read)."""
    s = blank()
    def A(x, t, g=1.0, pan=0.0):
        add(s, x, float(CUE(t)), g, pan)
    def P(t, g=0.32, pitch=1.0, w=0.0, pan=0.0):
        A(pop(pitch, w), t, g, pan)
    # S1  stuck(0.74) with zero(1.18) sales(1.48), poor(2.02) engagement and bad(3.06) leads(3.28)
    A(glitch(0.55), 0.05, 0.10)
    A(power_on(), 0.10, 0.22)
    P(1.18, 0.40, 0.8, 0.9); P(1.48, 0.30, 1.0)
    A(sine_sweep(880, 260, 2.5) * env_ar(int(2.5 * FS), 0.3, 0.6), 1.18, 0.025)   # falling graph tone
    P(2.02, 0.28, 1.15, pan=-0.2); P(3.06, 0.40, 0.75, 1.0)
    # S2  agar aap bhi business(4.36) owner hain, aur ads pe result(5.78) nahi aa rahe(6.56)
    A(whoosh(0.35), 3.80 - 0.30, 0.22, 0.3)
    P(4.30, 0.26, 1.1)
    A(swipe(0.3), 4.40, 0.12, -0.3)
    tt = 4.55
    while tt < 6.40:   # spend counter ticks, sparse and quiet
        A(tick(4200, g=1.0), tt, 0.035, 0.4)
        tt += 0.09
    P(5.78, 0.36, 0.85, 0.6); A(error_tone(), 5.83, 0.20)
    A(swipe(0.25), 5.98, 0.12, 0.4)
    P(6.28, 0.35, 0.7, 0.8)
    A(whoosh(0.55, 200, 7000, 0.85), 6.80 - 0.52, 0.38)
    A(impact(52, 1.4), 6.80, 0.45)
    # S3  so don't(6.92) worry(7.18). hum(8.10) aapke(8.28) brand(8.56)… offer(9.06)… free(9.78) business(10.0) consultancy(10.36) meeting(10.88)
    P(6.92, 0.24, 1.2); P(7.18, 0.28, 0.9)
    A(swipe(), 7.93, 0.10)
    P(8.10, 0.20, 1.3); P(8.28, 0.24, 1.1); P(8.56, 0.28, 0.9, 0.4)
    A(swipe(), 8.98, 0.10)
    P(9.06, 0.18, 1.3)
    A(impact(70, 0.6), 9.78, 0.38); A(tick(2500, 0.03), 9.78, 0.5)
    if SAY_BUSINESS:
        A(swipe(0.22), 10.00, 0.11, 0.0)
    A(swipe(0.25), 10.36, 0.14, -0.3); A(swipe(0.25), 10.88, 0.14, 0.3)
    # S4  exact(12.30) batayenge(12.66) ki aapka brand(13.76) kahan pe lack(14.40) kar raha hai(14.84)
    A(whoosh(0.3), 11.40 - 0.26, 0.20, -0.3)
    P(12.30, 0.30, 1.0, 0.4); P(12.66, 0.26, 0.85)
    for t0, m in [(13.88, 'E6'), (14.52, 'G6'), (14.96, 'A6')]:
        A(bell(n(m), 1.0, 1.2), t0, 0.16, 0.2)
        A(tick(5000), t0, 0.15)
    for k in range(4):
        A(pop(1.0 + k * 0.18), 13.2 + k * 0.16, 0.14, -0.3 + k * 0.2)
    A(sine_sweep(330, 990, 1.4) * env_ar(int(1.4 * FS), 0.4, 0.4), 13.7, 0.02)
    # S5  so don't(15.44) waste(15.68) your(15.92) time(16.08) and(16.46) money(16.64) — music stopped
    A(swipe(0.2), 15.26, 0.12)
    A(impact(48, 1.2), 15.44, 0.55)
    A(impact(62, 0.6), 15.68, 0.30)
    A(whoosh(0.18, 600, 8000, 0.7), 15.76, 0.18)
    for k in range(3):
        A(tick(2600 if k % 2 else 3400, 0.03, 1.0), 15.92 + k * 0.12, 0.45, (-0.3, 0.3)[k % 2])
    A(slash(), 16.12, 0.35, 0.2)
    A(whoosh(0.18, 600, 8000, 0.7), 16.30, 0.18)
    A(coin(), 16.46, 0.20, 0.1)
    A(slash(), 16.70, 0.35, -0.2)
    # S6  click(17.46) below and reserve(18.30) your seat right(19.12) now(19.36)
    A(whoosh(0.5, 300, 9000, 0.9), 17.05 - 0.45, 0.18)
    A(impact(45, 1.6), 17.05 + 0.03, 0.30)
    for k in range(6):
        A(tick(3000 + k * 400, 0.025), 17.30 + k * 0.07, 0.22, -0.5 + k * 0.2)
    P(17.46, 0.16, 1.4)
    P(17.95, 0.12, 1.5)
    A(tick(2200, 0.03), 18.30, 0.4); A(tick(3200, 0.03), 18.34, 0.3); P(18.32, 0.22, 1.0, 0.3)
    P(19.12, 0.24, 1.1, 0.3)
    s = s + reverb(s, IR_SHORT, 0.18)
    return s


# ---------- io ----------
def read_wav(path):
    with wave.open(path) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float) / 32768
        return a.reshape(-1, w.getnchannels()).T

def write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(path, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(3); w.setframerate(FS)
        v = (x.T * (2 ** 23 - 1)).astype(np.int32).reshape(-1)
        b = np.frombuffer(v.astype('<i4').tobytes(), dtype=np.uint8).reshape(-1, 4)[:, :3]
        w.writeframes(b.tobytes())

def rms_db(x):
    return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)

def mix_and_write(vo_path, out, music, sfx, ref_span=(DROP, STOP)):
    """Level music+sfx against the VO, duck under speech, write stems, bed and mix check."""
    os.makedirs(out, exist_ok=True)
    vo = read_wav(vo_path)[:, :N]
    vo = np.pad(vo, ((0, 0), (0, N - vo.shape[1])))
    # VO envelope → ducking gain (attack 25ms, release 300ms)
    env = np.abs(vo).mean(0)
    w = int(0.02 * FS); env = np.convolve(env, np.ones(w) / w, 'same')
    active = np.clip((20 * np.log10(env + 1e-9) + 42) / 12, 0, 1)
    g = np.zeros(N); a_c, r_c = np.exp(-1 / (0.025 * FS)), np.exp(-1 / (0.30 * FS))
    acc = 0.0
    for i in range(0, N, 32):  # control-rate smoothing
        tgt = active[i]
        c = a_c if tgt > acc else r_c
        acc = tgt + (acc - tgt) * c ** 32
        g[i:i + 32] = acc
    duck_music = 10 ** (-7 * g / 20)
    duck_sfx = 10 ** (-3 * g / 20)
    # levels: speech-active VO RMS as reference
    vo_ref = rms_db(vo[:, active > 0.5])
    m_gain = 10 ** ((vo_ref - 7 - rms_db(music[:, T(ref_span[0]):T(ref_span[1])])) / 20)
    s_gain = m_gain * 1.0
    music *= m_gain; sfx *= s_gain
    write_wav(f'{out}/music_unducked.wav', music)
    write_wav(f'{out}/sfx_unducked.wav', sfx)
    bed = music * duck_music + sfx * duck_sfx
    write_wav(f'{out}/sofwire_music_sfx_bed.wav', bed)
    mix = bed + vo
    write_wav(f'{out}/_mix_check.wav', mix)
    print('vo_ref dB', round(vo_ref, 1), 'music gain dB', round(20 * np.log10(m_gain), 1),
          'bed peak', round(20 * np.log10(np.abs(bed).max()), 1), 'mix peak', round(20 * np.log10(np.abs(mix).max()), 1))


if __name__ == '__main__':
    mix_and_write(sys.argv[1], sys.argv[2], build_music(), build_sfx())
