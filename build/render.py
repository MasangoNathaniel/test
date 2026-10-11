"""Sofwire 9:16 ad — motion-graphics renderer.

Renders every frame with PIL at 2x supersampling and pipes raw RGB to ffmpeg.
Camera moves (zoom in/out) are deliberately NOT baked in, so they can be added in the edit.

usage: python3 render.py <logo_dir> <out.mov> [--fps 30] [--preview N]
"""
import argparse, math, subprocess, sys, os
from functools import lru_cache
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, SS = 1080, 1920, 2
SW, SH = W * SS, H * SS
DUR = 19.84

CHAR = (35, 31, 32)       # logo black
CHAR_DK = (24, 21, 22)
CARD = (48, 43, 45)
BLUE = (11, 82, 208)      # logo blue
BLUE_LT = (92, 145, 255)
WHITE = (255, 255, 255)
GREY = (150, 146, 147)
RED = (255, 66, 66)

FONT_DIR = '/usr/share/fonts/opentype/inter/'
LOGO_DIR = None
NO_GRAIN = bool(os.environ.get('NO_GRAIN'))


# ---------- helpers ----------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))

def prog(t, t0, d):
    return clamp((t - t0) / d) if d > 0 else float(t >= t0)

def ease_out(p):  # cubic
    return 1 - (1 - p) ** 3

def ease_in_out(p):
    return 4 * p ** 3 if p < .5 else 1 - (-2 * p + 2) ** 3 / 2

def ease_back(p, s=1.9):
    p -= 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2

@lru_cache(None)
def font(weight, size):
    return ImageFont.truetype(FONT_DIR + f'InterDisplay-{weight}.otf', size * SS)

@lru_cache(None)
def text_layer(txt, size, color, weight='Black', track=0):
    """RGBA image of a single-line string, tight bbox, at SS scale."""
    f = font(weight, size)
    if track:
        widths = [f.getlength(c) + track * SS for c in txt]
        tw = int(sum(widths))
    else:
        tw = int(f.getlength(txt))
    asc, desc = f.getmetrics()
    im = Image.new('RGBA', (tw + 8 * SS, asc + desc + 8 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if track:
        x = 4 * SS
        for c, w in zip(txt, widths):
            d.text((x, 4 * SS), c, font=f, fill=color); x += w
    else:
        d.text((4 * SS, 4 * SS), txt, font=f, fill=color)
    return im.crop(im.getbbox())

def paste(canvas, layer, cx, cy, scale=1.0, alpha=1.0, anchor='c'):
    """Paste RGBA layer centred at (cx, cy) in 1x coords."""
    if alpha <= 0.003 or scale <= 0.01:
        return
    if abs(scale - 1) > 1e-3:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BICUBIC)
    if alpha < 1:
        a = layer.getchannel('A').point(lambda v: int(v * alpha))
        layer = layer.copy(); layer.putalpha(a)
    x = int(cx * SS - layer.width / 2) if anchor == 'c' else int(cx * SS)
    y = int(cy * SS - layer.height / 2)
    canvas.alpha_composite(layer, (x, y)) if canvas.mode == 'RGBA' else canvas.paste(layer, (x, y), layer)

def pop(canvas, layer, cx, cy, t, t0, dur=0.22, start_scale=0.55, out_t=None):
    """Kinetic pop-in (overshoot scale + fade). Optional fast pop-out at out_t."""
    p = prog(t, t0, dur)
    if p <= 0:
        return
    s = start_scale + (1 - start_scale) * ease_back(p)
    a = clamp(p * 3)
    if out_t is not None and t >= out_t:
        q = prog(t, out_t, 0.12)
        s *= 1 + 0.25 * q; a *= 1 - q
    paste(canvas, layer, cx, cy, s, a)

def slide_up(canvas, layer, cx, cy, t, t0, dur=0.3, dist=60):
    p = prog(t, t0, dur)
    if p <= 0:
        return
    paste(canvas, layer, cx, cy + dist * (1 - ease_out(p)), 1, clamp(p * 2))

def S(v):
    return int(round(v * SS))

def rect(d, x0, y0, x1, y1, fill, r=0):
    d.rounded_rectangle([S(x0), S(y0), S(x1), S(y1)], radius=S(r), fill=fill)

def partial_polyline(d, pts, frac, color, width):
    """Draw first `frac` (0..1) of a polyline by length; return the tip point."""
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    lens = [math.dist(a, b) for a, b in segs]
    total = sum(lens); left = total * frac
    tip = pts[0]
    for (a, b), L in zip(segs, lens):
        if left <= 0:
            break
        f = min(1, left / L)
        tip = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        d.line([S(a[0]), S(a[1]), S(tip[0]), S(tip[1])], fill=color, width=S(width))
        r = width / 2
        d.ellipse([S(tip[0] - r), S(tip[1] - r), S(tip[0] + r), S(tip[1] + r)], fill=color)
        d.ellipse([S(a[0] - r), S(a[1] - r), S(a[0] + r), S(a[1] + r)], fill=color)
        left -= L
    return tip

def arrow_head(d, tip, angle, size, color):
    a1, a2 = angle + math.radians(150), angle - math.radians(150)
    p = [tip, (tip[0] + size * math.cos(a1), tip[1] + size * math.sin(a1)),
         (tip[0] + size * math.cos(a2), tip[1] + size * math.sin(a2))]
    d.polygon([(S(x), S(y)) for x, y in p], fill=color)

def corner_marks(d, color=(255, 255, 255, 70)):
    """Small fixed '+' marks in the corners (frame furniture, like the refs)."""
    for x, y in [(70, 260), (W - 70, 260), (70, H - 300), (W - 70, H - 300)]:
        d.line([S(x - 14), S(y), S(x + 14), S(y)], fill=color, width=S(3))
        d.line([S(x), S(y - 14), S(x), S(y + 14)], fill=color, width=S(3))


# ---------- logo ----------
@lru_cache(None)
def logo_mask(name):
    return Image.open(os.path.join(LOGO_DIR, name + '.png')).convert('L')

@lru_cache(None)
def logo_piece(name, color, width):
    """Colourised logo piece scaled so the FULL logo would be `width` px wide (1x)."""
    m = logo_mask(name)
    sc = width * SS / m.width
    m = m.resize((int(m.width * sc), int(m.height * sc)), Image.LANCZOS)
    im = Image.new('RGBA', m.size, color + (0,))
    im.putalpha(m)
    return im

@lru_cache(None)
def piece_offset(name):
    """Offset (fraction of canvas) from canvas centre to the piece's bbox centre."""
    m = logo_mask(name); x0, y0, x1, y1 = m.point(lambda v: 255 if v > 128 else 0).getbbox()
    return ((x0 + x1) / 2 / m.width - 0.5, (y0 + y1) / 2 / m.height - 0.5)

def paste_piece_centred(canvas, name, color, logo_w, cx, cy, scale=1.0, alpha=1.0):
    piece = logo_piece(name, color, logo_w)
    ox, oy = piece_offset(name)
    paste(canvas, piece, cx - ox * piece.width / SS * scale, cy - oy * piece.height / SS * scale, scale, alpha)

def full_logo(canvas, cx, cy, width, letters=WHITE, blue=BLUE, alpha=1.0):
    paste(canvas, logo_piece('dark', letters, width), cx, cy, 1, alpha)
    paste(canvas, logo_piece('blue', blue, width), cx, cy, 1, alpha)

def brand_tag(canvas, t, letters=WHITE, blue=BLUE):
    full_logo(canvas, W / 2, 175, 300, letters, blue, 0.95)


# ---------- grain ----------
_rng = np.random.default_rng(7)
GRAIN = [(_rng.normal(0, 1, (H, W)) * 5).astype(np.int16) for _ in range(6)]

def finish(canvas, fi, grain=True):
    im = canvas.convert('RGB').resize((W, H), Image.LANCZOS)
    a = np.asarray(im).astype(np.int16)
    if grain:
        a = a + GRAIN[fi % 6][..., None]
    return np.clip(a, 0, 255).astype(np.uint8)


# ---------- shots ----------
def bg(color):
    return Image.new('RGBA', (SW, SH), color + (255,))

def shot1(t):
    """0.00–4.30  'Stuck with zero sales, poor engagement, and bad leads?'"""
    c = bg(CHAR); d = ImageDraw.Draw(c)
    corner_marks(d)
    # power icon glitch-in, then rises into brand tag
    if t < 0.74:
        p = prog(t, 0.05, 0.25)
        flick = 1 if (int(t * 30) % 3) else 0.35
        paste_piece_centred(c, 'B_power', BLUE, 2600, W / 2, H / 2, 1, p * flick)
    else:
        brand_tag(c, t)
    # stacked kinetic words
    pop(c, text_layer('ZERO', 190, BLUE_LT), W / 2, 560, t, 1.18)
    pop(c, text_layer('SALES', 150, WHITE), W / 2, 730, t, 1.48)
    pop(c, text_layer('POOR ENGAGEMENT', 78, WHITE), W / 2, 880, t, 2.02)
    pop(c, text_layer('BAD LEADS?', 132, WHITE), W / 2, 1020, t, 3.06)
    # falling line graph
    if t > 1.18:
        gx0, gy0, gx1, gy1 = 150, 1180, 930, 1560
        for k in range(4):  # baseline grid
            y = gy0 + k * (gy1 - gy0) / 3
            d.line([S(gx0), S(y), S(gx1), S(y)], fill=(255, 255, 255, 28), width=S(2))
        pts = [(gx0, gy0 + 20), (300, 1250), (420, 1220), (540, 1340), (650, 1310), (780, 1460), (gx1 - 30, 1530)]
        frac = ease_in_out(prog(t, 1.18, 2.5))
        tip = partial_polyline(d, pts, frac, RED, 12)
        if frac > 0.02:
            i = min(len(pts) - 2, int(frac * (len(pts) - 1)))
            ang = math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0])
            arrow_head(d, tip, ang, 46, RED)
    return c

def shot2(t):
    """4.30–8.55  'Agar aap bhi business owner hain aur ads pe results nahi aa rahe!'"""
    c = bg(CHAR); d = ImageDraw.Draw(c)
    corner_marks(d); brand_tag(c, t)
    lt = t - 4.30
    pop(c, text_layer('BUSINESS OWNER?', 92, WHITE), W / 2, 470, t, 4.30)
    # ads dashboard card
    p = ease_out(prog(t, 4.40, 0.35))
    if p > 0:
        cy = 1010 + 80 * (1 - p)
        card = Image.new('RGBA', (SW, SH), (0, 0, 0, 0)); cd = ImageDraw.Draw(card)
        x0, x1, y0, y1 = 130, 950, cy - 330, cy + 330
        rect(cd, x0, y0, x1, y1, CARD + (255,), 36)
        rect(cd, x0, y0, x1, y0 + 96, (60, 55, 57, 255), 36); rect(cd, x0, y0 + 60, x1, y0 + 96, (60, 55, 57, 255))
        cd.ellipse([S(x0 + 40), S(y0 + 36), S(x0 + 64), S(y0 + 60)], fill=BLUE + (255,))
        paste(card, text_layer('Ad Campaign  •  Active', 30, (220, 216, 217), 'SemiBold'), x0 + 84, y0 + 48, anchor='l')
        spend = int(25 + 475 * ease_in_out(prog(t, 4.55, 1.9)))
        rows = [('Ad spend', f'${spend}', WHITE), ('Leads', '0', RED), ('Sales', '0', RED), ('ROI', '-100%', RED)]
        for k, (lab, val, col) in enumerate(rows):
            ry = y0 + 170 + k * 128
            paste(card, text_layer(lab, 42, GREY, 'Medium'), x0 + 60, ry, anchor='l')
            v = text_layer(val, 64, col, 'Black')
            jitter = (2 if (k and int(t * 20) % 2 and t < 6.3) else 0)
            paste(card, v, x1 - 60 - v.width / SS / 2 + jitter, ry)
            if k < 3:
                cd.line([S(x0 + 50), S(ry + 64), S(x1 - 50), S(ry + 64)], fill=(255, 255, 255, 25), width=S(2))
        if p < 1:
            a = card.getchannel('A').point(lambda v: int(v * p)); card.putalpha(a)
        c.alpha_composite(card)
    pop(c, text_layer('NO RESULTS?', 120, WHITE), W / 2, 1530, t, 5.78)
    # underline swipe under NO RESULTS
    u = ease_out(prog(t, 5.98, 0.3))
    if u > 0:
        rect(d, W / 2 - 340, 1610, W / 2 - 340 + 680 * u, 1626, BLUE + (255,), 8)
    # ring pops in at the end → transition handled in shot3
    return c

def ring_transition(c, t, t0=6.28, t_fill=6.46, t_end=6.80):
    """Logo 'o' ring pops at centre, then a blue circle grows from it to fill the frame."""
    p = prog(t, t0, 0.2)
    if p <= 0:
        return
    paste_piece_centred(c, 'B_ring', BLUE, 2600, W / 2, H / 2, max(0.02, ease_back(p)), 1)
    q = ease_in_out(prog(t, t_fill, t_end - t_fill))
    if q > 0:
        r = 140 + q * 1150
        d = ImageDraw.Draw(c)
        d.ellipse([S(W / 2 - r), S(H / 2 - r), S(W / 2 + r), S(H / 2 + r)], fill=BLUE + (255,))

def shot3(t):
    """8.55–12.95  'So don't worry… hum aapke brand ke liye offer… ek Free Consultancy Meeting'"""
    c = bg(BLUE); d = ImageDraw.Draw(c)
    corner_marks(d, (255, 255, 255, 90))
    brand_tag(c, t, WHITE, WHITE)
    pop(c, text_layer("DON'T", 170, WHITE), W / 2, 760, t, 6.92, out_t=7.95)
    pop(c, text_layer('WORRY.', 170, CHAR), W / 2, 950, t, 7.18, out_t=7.95)
    # offer
    pop(c, text_layer('FOR', 70, WHITE, 'Bold', track=2), W / 2, 680, t, 8.10, out_t=9.0)
    pop(c, text_layer('YOUR', 150, WHITE), W / 2, 820, t, 8.28, out_t=9.0)
    pop(c, text_layer('BRAND', 170, CHAR), W / 2, 990, t, 8.56, out_t=9.0)
    pop(c, text_layer('WE’RE OFFERING A', 54, WHITE, 'Bold', track=2), W / 2, 680, t, 9.06)
    # FREE stamp
    p = prog(t, 9.78, 0.25)
    if p > 0:
        s = 1.8 - 0.8 * ease_out(p)
        tag = Image.new('RGBA', (S(640), S(250)), (0, 0, 0, 0))
        td = ImageDraw.Draw(tag)
        td.rounded_rectangle([0, 0, S(640) - 1, S(250) - 1], radius=S(40), fill=WHITE + (255,))
        paste(tag, text_layer('FREE', 190, BLUE), 320, 125)
        tag = tag.rotate(-4, resample=Image.BICUBIC, expand=True)
        paste(c, tag, W / 2, 860, s, clamp(p * 2.5))
    slide_up(c, text_layer('BUSINESS', 80, WHITE, 'Black', track=6), W / 2, 1060, t, 10.0)
    slide_up(c, text_layer('CONSULTANCY', 118, WHITE), W / 2, 1180, t, 10.36)
    slide_up(c, text_layer('MEETING', 118, CHAR), W / 2, 1320, t, 10.88)
    return c

def check_icon(d, cx, cy, r, p):
    d.ellipse([S(cx - r), S(cy - r), S(cx + r), S(cy + r)], fill=BLUE + (255,))
    if p > 0:
        pts = [(cx - r * .45, cy + r * .02), (cx - r * .1, cy + r * .38), (cx + r * .5, cy - r * .35)]
        partial_polyline(d, pts, p, WHITE + (255,), r * .2)

def shot4(t):
    """12.95–18.10  'Jahan hum aapko exact action plan batayenge… kahan lag kar raha hai'"""
    c = bg(CHAR); d = ImageDraw.Draw(c)
    corner_marks(d); brand_tag(c, t)
    pop(c, text_layer('EXACT', 140, WHITE), W / 2, 450, t, 12.30)
    pop(c, text_layer('ACTION PLAN', 112, BLUE_LT), W / 2, 590, t, 12.66)
    items = [('Find where your brand is lacking', 13.76), ('Fix your ads & lead flow', 14.40), ('Grow with a clear plan', 14.84)]
    for k, (txt, t0) in enumerate(items):
        y = 780 + k * 120
        p = ease_out(prog(t, t0, 0.3))
        if p > 0:
            lay = Image.new('RGBA', (SW, SH), (0, 0, 0, 0)); ld = ImageDraw.Draw(lay)
            check_icon(ld, 160, y, 36, prog(t, t0 + 0.12, 0.25))
            paste(lay, text_layer(txt, 50, WHITE, 'Bold'), 220, y, anchor='l')
            a = lay.getchannel('A').point(lambda v: int(v * clamp(p * 2))); lay.putalpha(a)
            c.alpha_composite(lay, (int(S(-40 * (1 - p))), 0))
    # rising bars + arrow (answers shot 1's falling line)
    gx0, gy1 = 190, 1640
    bars = [120, 190, 270, 380]
    for k, h in enumerate(bars):
        p = ease_back(prog(t, 13.2 + k * 0.16, 0.4), 1.2)
        if p > 0:
            x = gx0 + k * 180
            col = (BLUE if k == 3 else (70, 64, 66)) + (255,)
            rect(d, x, gy1 - h * p, x + 120, gy1, col, 14)
    d.line([S(150), S(gy1 + 4), S(930), S(gy1 + 4)], fill=(255, 255, 255, 60), width=S(3))
    pts = [(200, 1540), (380, 1470), (560, 1380), (760, 1220), (900, 1150)]
    frac = ease_in_out(prog(t, 13.7, 1.4))
    if frac > 0:
        tip = partial_polyline(d, pts, frac, WHITE + (255,), 12)
        i = min(len(pts) - 2, int(frac * (len(pts) - 1)))
        arrow_head(d, tip, math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0]), 46, WHITE + (255,))
    return c

def clock_icon(d, cx, cy, r, color):
    d.ellipse([S(cx - r), S(cy - r), S(cx + r), S(cy + r)], outline=color, width=S(18))
    d.line([S(cx), S(cy), S(cx), S(cy - r * .6)], fill=color, width=S(18))
    d.line([S(cx), S(cy), S(cx + r * .45), S(cy + r * .1)], fill=color, width=S(18))
    d.ellipse([S(cx - 14), S(cy - 14), S(cx + 14), S(cy + 14)], fill=color)

def strike(d, x0, y0, x1, y1, p):
    if p > 0:
        partial_polyline(d, [(x0, y0), (x1, y1)], p, RED + (255,), 20)

def shot5(t):
    """18.10–20.35  "Don't waste your time and money!" — fast hard cuts."""
    if t < 15.90:
        c = bg(CHAR); d = ImageDraw.Draw(c); corner_marks(d)
        pop(c, text_layer("DON'T", 190, WHITE), W / 2, 820, t, 15.44, 0.16)
        pop(c, text_layer('WASTE', 190, BLUE_LT), W / 2, 1020, t, 15.68, 0.16)
    elif t < 16.44:
        c = bg(BLUE); d = ImageDraw.Draw(c); corner_marks(d, (255, 255, 255, 90))
        p = ease_back(prog(t, 15.90, 0.2))
        clock_icon(d, W / 2, 780, 190 * (0.6 + 0.4 * p), WHITE + (255,))
        pop(c, text_layer('YOUR TIME', 120, WHITE), W / 2, 1110, t, 15.92, 0.16)
        strike(d, 330, 1000, 750, 560, prog(t, 16.12, 0.18))
    else:
        c = bg(CHAR); d = ImageDraw.Draw(c); corner_marks(d)
        p = ease_back(prog(t, 16.44, 0.2))
        r = 190 * (0.6 + 0.4 * p)
        d.ellipse([S(W / 2 - r), S(780 - r), S(W / 2 + r), S(780 + r)], outline=(70, 200, 120, 255), width=S(18))
        paste(c, text_layer('$', 230, (70, 200, 120)), W / 2, 780, 0.6 + 0.4 * p)
        pop(c, text_layer('& MONEY', 120, WHITE), W / 2, 1110, t, 16.46, 0.16)
        strike(d, 330, 1000, 750, 560, prog(t, 16.70, 0.18))
    return c

def shot6(t):
    """20.35–23.10  'Click below and reserve your seat right now!' — logo build + CTA."""
    c = bg(WHITE); d = ImageDraw.Draw(c)
    LW = 860
    cx, cy = W / 2, 760
    # blue pieces first (ring, power icon), pop in
    for name, t0 in [('B_ring', 17.10), ('B_power', 17.20)]:
        p = prog(t, t0, 0.25)
        if p > 0:
            piece = logo_piece(name, BLUE, LW)
            paste(c, piece, cx, cy, 1, clamp(p * 2.5))
    # black letters build in around them, staggered, sliding up
    order = ['S', 'f', 'w', 'i', 'r', 'e']
    for k, L in enumerate(order):
        t0 = 17.30 + k * 0.07
        p = ease_out(prog(t, t0, 0.28))
        if p > 0:
            paste(c, logo_piece('L_' + L, CHAR, LW), cx, cy + 50 * (1 - p), 1, clamp(p * 1.6))
    # subtle settle bounce on the whole logo is left to the editor (zoom room)
    pop(c, text_layer('FREE CONSULTANCY  •  LIMITED SEATS', 34, (110, 106, 107), 'Bold', track=1), W / 2, 980, t, 17.95)
    # CTA button
    p = ease_back(prog(t, 18.30, 0.3), 1.4)
    if p > 0:
        pulse = 1 + 0.035 * math.sin(max(0, t - 18.75) * 2 * math.pi * 1.6) if t > 18.75 else 1
        btn = Image.new('RGBA', (S(820), S(170)), (0, 0, 0, 0)); bd = ImageDraw.Draw(btn)
        bd.rounded_rectangle([0, 0, S(820) - 1, S(170) - 1], radius=S(85), fill=BLUE + (255,))
        paste(btn, text_layer('RESERVE YOUR SEAT', 60, WHITE), 410, 85)
        paste(c, btn, W / 2, 1290, (0.7 + 0.3 * p) * pulse, clamp(p * 2))
    # 'right now' + bouncing arrow down
    pop(c, text_layer('RIGHT NOW', 46, CHAR, 'Black', track=4), W / 2, 1440, t, 19.12)
    if t > 17.46:
        b = abs(math.sin((t - 17.46) * math.pi * 2.2)) * 26
        a = clamp((t - 17.46) * 4)
        d.polygon([(S(W / 2 - 40), S(1530 + b)), (S(W / 2 + 40), S(1530 + b)), (S(W / 2), S(1585 + b))],
                  fill=BLUE + (int(255 * a),))
    pop(c, text_layer('CLICK BELOW', 40, BLUE, 'Black', track=4), W / 2, 1650, t, 17.46)
    return c


CUTS = [(0.00, shot1), (3.80, shot2), (6.80, shot3), (11.40, shot4), (15.24, shot5), (17.05, shot6)]

def render(fi, fps):
    t = fi / fps
    fn = [f for t0, f in CUTS if t >= t0][-1]
    c = fn(t)
    if fn is shot2 and t >= 6.26:
        ring_transition(c, t)
    return finish(c, fi, grain=(fn is not shot6) and not NO_GRAIN)

def _work(args):
    return render(*args).tobytes()

def init(logo_dir):
    global LOGO_DIR
    LOGO_DIR = logo_dir

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('logo_dir'); ap.add_argument('out')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--stills', type=str, default='', help='comma list of times → PNGs instead of video')
    a = ap.parse_args()
    init(a.logo_dir)
    if a.stills:
        os.makedirs(a.out, exist_ok=True)
        for s in a.stills.split(','):
            fi = int(round(float(s) * a.fps))
            Image.fromarray(render(fi, a.fps)).save(os.path.join(a.out, f't{float(s):05.2f}.png'))
        sys.exit()
    n = int(round(DUR * a.fps))
    ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                           '-s', f'{W}x{H}', '-r', str(a.fps), '-i', '-',
                           '-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le', a.out],
                          stdin=subprocess.PIPE)
    with Pool(os.cpu_count(), initializer=init, initargs=(a.logo_dir,)) as pool:
        for i, buf in enumerate(pool.imap(_work, [(i, a.fps) for i in range(n)], chunksize=4)):
            ff.stdin.write(buf)
            if i % 60 == 0:
                print(f'frame {i}/{n}', file=sys.stderr, flush=True)
    ff.stdin.close(); ff.wait()
