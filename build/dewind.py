"""Wind clean-up for a voice that has already been through a voice isolator.

1. 4th-order high-pass at 90 Hz (speaker's F0 is ~111-178 Hz, so the voice is untouched).
2. Dynamic low-band suppression: in 20 ms frames, when the <160 Hz band jumps above its
   usual ratio to the voice band (a wind gust), duck only that band, up to MAX_DUCK_DB.

usage: python3 dewind.py <in.wav 48k 16-bit> <out.wav>
"""
import sys, wave
import numpy as np
from scipy.signal import butter, sosfiltfilt

HPF_HZ = 90
SPLIT_HZ = 160
MAX_DUCK_DB = 14.0
FRAME_S = 0.02


def read(p):
    w = wave.open(p)
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, w.getnchannels()).astype(float) / 32768
    return a.mean(1), w.getframerate()


def write(p, x, fs):
    x = np.clip(x, -1, 1)
    w = wave.open(p, 'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(fs)
    w.writeframes((np.repeat(x[:, None], 2, 1) * 32767).astype('<i2').tobytes())


def dewind(x, fs):
    x = sosfiltfilt(butter(4, HPF_HZ, 'high', fs=fs, output='sos'), x)
    low = sosfiltfilt(butter(4, SPLIT_HZ, 'low', fs=fs, output='sos'), x)
    high = x - low                                                     # complementary split (zero-phase)
    voice = sosfiltfilt(butter(4, [300, 3400], 'band', fs=fs, output='sos'), x)
    n = int(FRAME_S * fs); nf = len(x) // n
    rms = lambda s: np.sqrt((s[:nf * n].reshape(nf, n) ** 2).mean(1)) + 1e-9
    ratio_db = 20 * np.log10(rms(low) / rms(voice))
    active = 20 * np.log10(rms(voice)) > -45                           # frames with speech
    base = np.percentile(ratio_db[active], 60) if active.any() else np.median(ratio_db)
    excess = np.clip(ratio_db - base, 0, None)                         # how far above normal the low band is
    gain_db = -np.minimum(excess * 1.2, MAX_DUCK_DB)
    gain_db[~active] = np.minimum(gain_db[~active], -6.0)              # pauses: keep low band down anyway
    # smooth: fast attack, slower release
    g = np.empty_like(gain_db); cur = 0.0
    for i, v in enumerate(gain_db):
        cur = v if v < cur else cur + (v - cur) * 0.25
        g[i] = cur
    gs = np.repeat(10 ** (g / 20), n)
    gs = np.concatenate([gs, np.full(len(x) - len(gs), gs[-1] if len(gs) else 1.0)])
    gs = np.convolve(gs, np.ones(n) / n, 'same')                       # de-click the gain curve
    return high + low * gs, (excess > 3).mean()


if __name__ == '__main__':
    x, fs = read(sys.argv[1])
    y, frac = dewind(x, fs)
    write(sys.argv[2], y, fs)
    print(f'gust frames ducked: {frac * 100:.1f}%')
