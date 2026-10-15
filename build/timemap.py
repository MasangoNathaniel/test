"""Time map between the v2 voiceover (the timeline the visuals and SFX cues were authored on)
and a newer read of the same script, built from both ElevenLabs word timestamp files.

Words are aligned by text (difflib), and their start times become anchors of a piecewise-linear
map, so every cue keeps its place relative to the word it was locked to.

    to_new(t_v2) -> t in the new read        to_old(t_new) -> t on the v2 timeline

Set SOFWIRE_VO_WORDS=<new words.json> and render.py / sound.py / sound_desi.py use it.
"""
import difflib, json, os, re
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
V2_WORDS = os.path.join(HERE, '..', 'assets', 'voiceover_v2_words.json')
V2_DUR = 19.84
TAIL_MIN = 0.6          # keep at least this long after the last word ends (logo hold)


def _words(p):
    w = json.load(open(p))
    w = w['words'] if isinstance(w, dict) else w
    return [x for x in w if x.get('type', 'word') == 'word']


def _norm(s):
    return re.sub(r'[^\w]', '', s.lower())


class TimeMap:
    def __init__(self, new_words_path):
        a, b = _words(V2_WORDS), _words(new_words_path)
        sm = difflib.SequenceMatcher(None, [_norm(x['text']) for x in a], [_norm(x['text']) for x in b], autojunk=False)
        old, new = [0.0], [0.0]
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                to, tn = a[blk.a + k]['start'], b[blk.b + k]['start']
                if to > old[-1] + 1e-3 and tn > new[-1] + 1e-3:
                    old.append(to); new.append(tn)
        tail = V2_DUR - a[-1]['end']
        self.dur = round(max(b[-1]['end'] + TAIL_MIN, b[-1]['end'] + tail * 0.8), 2)
        old.append(V2_DUR); new.append(self.dur)
        self.old, self.new = np.array(old), np.array(new)
        self.matched = len(old) - 2
        self.new_words = [_norm(x['text']) for x in b]

    def says(self, word, after):
        """True if the new read has `word` right after `after` (e.g. 'business' after 'free')."""
        w = self.new_words
        return any(w[i] == after and i + 1 < len(w) and w[i + 1] == word for i in range(len(w)))

    def to_new(self, t):
        return np.interp(t, self.old, self.new)

    def to_old(self, t):
        return np.interp(t, self.new, self.old)


def from_env():
    p = os.environ.get('SOFWIRE_VO_WORDS')
    return TimeMap(p) if p else None


if __name__ == '__main__':
    import sys
    m = TimeMap(sys.argv[1])
    print(f'matched {m.matched} words, new duration {m.dur}s')
    r = np.diff(m.new) / np.diff(m.old)
    print(f'local speed ratio new/old: min {r.min():.2f} max {r.max():.2f}')
    for t in (3.80, 6.28, 6.80, 11.40, 15.24, 17.05):
        print(f'  {t:5.2f} -> {m.to_new(t):5.2f}')
