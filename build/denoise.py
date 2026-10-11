"""Voiceover noise reduction: spectral gating with a noise profile taken from the pauses.

usage: python3 denoise.py <in.wav 48k> <out.wav> [reduction_db]
"""
import sys, wave
import numpy as np
from scipy.signal import stft, istft, butter, sosfiltfilt

GAPS = [(0.0, 0.6), (3.55, 3.9), (7.5, 8.05), (11.2, 11.55), (16.9, 17.4)]  # silent stretches (s), from the word timings
NFFT, HOP = 2048, 512
STD_K = 2.0


def read(p):
    w = wave.open(p)
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, w.getnchannels()).astype(float) / 32768
    return a.mean(1), w.getframerate()


def write(p, x, fs):
    x = np.clip(x, -1, 1)
    w = wave.open(p, 'wb'); w.setnchannels(2); w.setsampwidth(3); w.setframerate(fs)
    v = (np.repeat(x[:, None], 2, 1).reshape(-1) * (2 ** 23 - 1)).astype('<i4')
    w.writeframes(np.frombuffer(v.tobytes(), np.uint8).reshape(-1, 4)[:, :3].tobytes())


def denoise(x, fs, reduction_db=22.0):
    x = sosfiltfilt(butter(4, 75, 'high', fs=fs, output='sos'), x)        # remove sub rumble below the voice
    f, t, Z = stft(x, fs, nperseg=NFFT, noverlap=NFFT - HOP)
    mag = np.abs(Z)
    gi = np.zeros(len(t), bool)
    for a, b in GAPS:
        gi |= (t >= a + 0.05) & (t <= b - 0.05)
    noise_db = 20 * np.log10(mag[:, gi] + 1e-10)
    thresh = noise_db.mean(1) + STD_K * noise_db.std(1)                       # per-bin gate threshold
    sig_db = 20 * np.log10(mag + 1e-10)
    # soft mask: 0 dB well above the threshold, down to -reduction_db at/below it
    over = sig_db - thresh[:, None]
    mask = np.clip((over + 3) / 9, 0, 1)
    # smooth the mask over time and frequency to avoid "musical noise"
    k_t, k_f = 5, 5
    pad = np.pad(mask, ((k_f // 2,) * 2, (k_t // 2,) * 2), mode='edge')
    sm = np.zeros_like(mask)
    for i in range(k_f):
        for j in range(k_t):
            sm += pad[i:i + mask.shape[0], j:j + mask.shape[1]]
    sm /= k_f * k_t
    floor = 10 ** (-reduction_db / 20)
    gain = floor + (1 - floor) * sm
    _, y = istft(Z * gain, fs, nperseg=NFFT, noverlap=NFFT - HOP)
    return y[:len(x)]


if __name__ == '__main__':
    x, fs = read(sys.argv[1])
    red = float(sys.argv[3]) if len(sys.argv) > 3 else 22.0
    y = denoise(x, fs, red)
    write(sys.argv[2], y, fs)
