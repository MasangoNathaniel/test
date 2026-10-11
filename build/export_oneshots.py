"""Export each sound-design element from sound.py as its own one-shot WAV."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sound as S

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
shots = {
    '01_power_on_glitch': np.concatenate([S.glitch(0.55) * 0.45, np.zeros(1)])[:int(0.55 * S.FS)] + np.pad(S.power_on(), (0, max(0, int(0.55 * S.FS) - len(S.power_on()))))[:int(0.55 * S.FS)],
    '02_pop_soft': S.pop(1.2),
    '03_pop_mid': S.pop(1.0),
    '04_pop_heavy': S.pop(0.8, 0.9),
    '05_whoosh_short': S.whoosh(0.35),
    '06_whoosh_big_transition': S.whoosh(0.55, 200, 7000, 0.85),
    '07_swipe': S.swipe(0.25),
    '08_impact_drop': S.impact(52, 1.4),
    '09_impact_stamp': S.impact(70, 0.6),
    '10_impact_deep': S.impact(48, 1.2),
    '11_tick_ui': S.tick(2200, 0.03),
    '12_tick_counter': S.tick(4200),
    '13_clock_tick': S.tick(3400, 0.03),
    '14_slash': S.slash(),
    '15_coin': S.coin(),
    '16_error_tone': S.error_tone(),
    '17_ding_checklist': S.bell(S.n('G6'), 1.0, 1.2),
    '18_falling_tone': S.sine_sweep(880, 260, 2.5) * S.env_ar(int(2.5 * S.FS), 0.3, 0.6),
}
for name, x in shots.items():
    x = np.asarray(x, dtype=float)
    pad = np.zeros(int(0.25 * S.FS))           # room for tails
    x = np.concatenate([x, pad])
    st = np.vstack([x, x])
    st = st + S.reverb(st, S.IR_SHORT, 0.18)
    st *= 10 ** (-3 / 20) / max(1e-9, np.abs(st).max())   # peak −3 dBFS
    S.write_wav(os.path.join(out, name + '.wav'), st)
print(len(shots), 'one-shots written')
