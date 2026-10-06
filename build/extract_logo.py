"""Split the Sofwire logo into blue/black alpha masks and per-letter pieces."""
import numpy as np
from PIL import Image
import sys, os

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
im = np.asarray(Image.open(src).convert('RGB')).astype(float)
r, g, b = im[..., 0], im[..., 1], im[..., 2]
lum = 0.299 * r + 0.587 * g + 0.114 * b
blueness = b - (r + g) / 2
# soft alphas (anti-aliased edges survive)
blue_a = np.clip((blueness - 20) / 80, 0, 1)
dark_a = np.clip((200 - lum) / 150, 0, 1) * (1 - blue_a)

ys, xs = np.where((blue_a + dark_a) > 0.5)
y0, y1, x0, x1 = ys.min() - 4, ys.max() + 5, xs.min() - 4, xs.max() + 5
print('logo bbox', x0, y0, x1, y1)

def save(a, name):
    Image.fromarray((a[y0:y1, x0:x1] * 255).astype(np.uint8), 'L').save(f'{out}/{name}.png')

save(blue_a, 'blue')
save(dark_a, 'dark')
# black letters, split by x (in source coords). f's crossbar touches w, so split at w's left stem.
letters = {'S': (0, 510), 'f': (790, 963), 'w': (963, 1250), 'i': (1250, 1335), 'r': (1335, 1478), 'e': (1478, 2000)}
for k, (a, bnd) in letters.items():
    m = np.zeros_like(dark_a); m[:, a:bnd] = dark_a[:, a:bnd]
    save(m, f'L_{k}')
bm = np.zeros_like(blue_a); bm[:, :900] = blue_a[:, :900]; save(bm, 'B_ring')
bm = np.zeros_like(blue_a); bm[:, 900:] = blue_a[:, 900:]; save(bm, 'B_power')
