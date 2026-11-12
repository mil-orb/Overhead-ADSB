"""Split-flap board renderer.

How a real module works, and what is modelled here:

  * Each module is a drum of flaps in a fixed order. It can only go forward, so a
    change from B to A passes through every other flap first.
  * The drum turns at a constant rate and releases one flap per step. A released
    flap is a thin plate hinged at the middle of the window. It tips forward and
    falls under gravity: slow while it is near upright, fast through the bottom.
    The fall takes longer than one drum step, so while a module is cycling there
    are usually two flaps in the air at once.
  * The falling flap carries the top half of the old character on its front and
    the bottom half of the new one on its back. Behind it the top half of the new
    character is already showing.
  * Light comes from above. The front of a falling flap turns away from it and
    goes dark. Past horizontal its back faces up into the light and is briefly
    brighter than a flap at rest. The flap also shades the lower half of the
    window while it overhangs it.
  * Every module in a row starts at once and stops on its own when it arrives.

Faces are pre-rendered at 4x and scaled down. Squashed flap frames are cached, and
only modules that are moving get redrawn.
"""
from __future__ import annotations

import math
import random
import threading
import time
import pygame

SS = 4
ALPHA = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-/°"
NUM = " 0123456789."
CLOCK = "0123456789 "
REMARKS = ["", "IN VIEW", "FAINT", "INBOUND", "LOW", "FAR", "HAZE", "CLOUD", "GLARE",
           "MILITARY", "EMERGENCY"]

PANEL = (30, 31, 34)
RECESS = (7, 7, 8)
FLAP = (43, 43, 46)
INK = (242, 238, 226)
PAINT = (205, 200, 184)
AMBER = (238, 170, 34)
RED = (204, 46, 38)
DARK = (24, 19, 10)
GREEN_LAMP = (60, 255, 120)
AMBER_LAMP = (255, 170, 30)
RED_LAMP = (255, 60, 40)
WARM_LAMP = (255, 232, 190)

LIGHT = math.radians(50)      # key light elevation above the viewing axis
AMBIENT = 0.42
REST = AMBIENT + (1 - AMBIENT) * math.cos(LIGHT)

GAP_U, LAMP_U, LAMP_GAP_U = 0.45, 0.72, 0.18
BASE_ASPECT, MAX_ASPECT = 1.56, 1.85     # flap height over width

def surf(w, h, alpha=False):
    return pygame.Surface((max(1, int(w)), max(1, int(h))), pygame.SRCALPHA if alpha else 0, 32)


def fall_curve(rate: float, n_flaps: int, length=0.035, lean=math.radians(10), drag=0.027, samples=48):
    """Angle of a released flap over time, from the equation of motion.

    A thin plate hinged along one edge: theta'' = (3g / 2L) sin(theta) - drag * theta'^2.
    It starts leaning `lean` past upright, moving at the drum's own speed.
    Returns (fall duration in seconds, angles 0..pi at evenly spaced times).
    """
    k = 3 * 9.81 / (2 * length)
    th, om, t, dt = lean, 2 * math.pi * rate / n_flaps, 0.0, 1e-4
    path = [(0.0, th)]
    while th < math.pi and t < 2.0:
        om += (k * math.sin(th) - drag * om * abs(om)) * dt
        th += om * dt
        t += dt
        path.append((t, th))
    table, j = [], 0
    for i in range(samples + 1):
        want = t * i / samples
        while j < len(path) - 1 and path[j][0] < want:
            j += 1
        table.append((min(path[j][1], math.pi) - lean) / (math.pi - lean) * math.pi)
    table[-1] = math.pi
    return t, table


class Fonts:
    def __init__(self, path=None):
        self.path, self._f, self._cap = path, {}, {}

    def get(self, size):
        size = max(4, int(size))
        if size not in self._f:
            try:
                self._f[size] = pygame.font.Font(self.path, size)
            except Exception:
                self._f[size] = pygame.font.Font(None, size)
        return self._f[size]

    def for_cap(self, cap_px):
        """Font whose capital letters are cap_px tall, with the rows where caps start and end."""
        cap_px = max(3, int(cap_px))
        if cap_px not in self._cap:
            ref = self.get(200).render("H", True, (255, 255, 255)).get_bounding_rect()
            f = self.get(round(200 * cap_px / ref.h))
            r = f.render("H", True, (255, 255, 255)).get_bounding_rect()
            self._cap[cap_px] = (f, r.top, r.bottom)
        return self._cap[cap_px]


def text_line(fonts, text, cap, color, spacing=0, condense=0.88, max_w=None):
    """Text as an alpha surface. Returns (surface, row of the cap top, cap height) or None if blank."""
    text = "".join(c for c in str(text)[:96] if c.isprintable())     # pygame raises on a NUL
    f, top, base = fonts.for_cap(cap)
    capn = base - top
    y0 = max(0, top - capn // 3)
    y1 = min(f.get_height(), base + capn // 3)
    space = round(capn * 0.42)
    parts, x = [], 0
    for ch in text:
        src = None if ch == " " else f.render(ch, True, color)
        bb = src.get_bounding_rect() if src else None
        if not bb or bb.w == 0:
            x += space + spacing
            continue
        hh = min(y1, src.get_height()) - y0
        w = max(1, round(bb.w * condense))
        parts.append((pygame.transform.smoothscale(src.subsurface((bb.x, y0, bb.w, hh)), (w, hh)), x))
        x += w + spacing
    if not parts:
        return None
    w = max(1, x - spacing)
    out = surf(w, y1 - y0, True)
    for s, px in parts:
        out.blit(s, (px, 0))
    if max_w and w > max_w:
        out = pygame.transform.smoothscale(out, (int(max_w), y1 - y0))
    return out, top - y0, capn


_shade_cache = {}


def _shade(W, H, fr):
    """Multiply map for a flap at rest: lit top half, shadow under the axle, soft falloff below."""
    key = (W, H, fr.top, fr.h)
    s = _shade_cache.get(key)
    if s is None:
        s = surf(W, H)
        s.fill((255, 255, 255))
        for y in range(fr.top, fr.bottom):
            t = (y - fr.top) / fr.h
            if t < 0.5:
                k = 1.0 - 0.12 * (t / 0.5) ** 2
            else:
                v = (t - 0.5) / 0.5
                k = 0.93 - 0.36 * math.exp(-v / 0.075) - 0.07 * v
            c = int(255 * k)
            pygame.draw.line(s, (c, c, c), (0, y), (W, y))
        _shade_cache[key] = s
    return s


def make_face(w, h, u, art, flap=FLAP):
    """One flap pair at rest, w x h pixels. `art` is centred on the hinge so it is split in two."""
    W, H, U = w * SS, h * SS, u * SS
    s = surf(W, H)
    s.fill(RECESS)
    px = max(2, round(U * 0.05))
    py = max(2, round(U * 0.045))
    fr = pygame.Rect(px, py, W - 2 * px, H - 2 * py)
    rad = round(U * 0.085)
    hinge = H // 2
    pygame.draw.rect(s, flap, fr, border_radius=rad)
    if art:
        a, cap_top, cap_h = art
        s.blit(a, ((W - a.get_width()) // 2, hinge - cap_top - cap_h // 2))
    s.blit(_shade(W, H, fr), (0, 0), special_flags=pygame.BLEND_RGB_MULT)

    # edges of the flaps stacked behind the top one
    line = surf(fr.w - 2 * rad, max(1, SS // 3))
    line.fill((200, 200, 200))
    for i in (1, 2):
        s.blit(line, (fr.left + rad, fr.top + round(U * 0.04 * i)), special_flags=pygame.BLEND_RGB_MULT)
    # light catching the top edge
    line.fill((14, 14, 14))
    s.blit(line, (fr.left + rad, fr.top), special_flags=pygame.BLEND_RGB_ADD)

    # the split, the lit edge of the lower flap, and the two axle notches
    gap = max(SS - 1, round(U * 0.05))
    pygame.draw.rect(s, (0, 0, 0), (fr.left, hinge - gap // 2, fr.w, gap))
    lip = surf(fr.w, max(1, gap // 2))
    lip.fill((20, 20, 20))
    s.blit(lip, (fr.left, hinge - gap // 2 + gap), special_flags=pygame.BLEND_RGB_ADD)
    nw, nh = max(2, round(U * 0.075)), max(gap * 2, round(U * 0.13))
    for x in (fr.left, fr.right - nw):
        pygame.draw.rect(s, RECESS, (x, hinge - nh // 2, nw, nh))
    return pygame.transform.smoothscale(s, (w, h))


STEPS = 12      # how many positions a falling flap is drawn at


class FaceSet:
    """All the faces on one kind of drum, plus ready-made pictures of each half-window mid-flip.

    Drawing a moving flap live means several blits and some trig per module per frame, which
    is too slow for a Pi Zero with a whole card in motion. Instead the fall is sampled at
    STEPS moments, and for each one the upper half and the lower half of the window are
    composed once and kept. A frame of animation is then two blits per module.
    """

    def __init__(self, keys, faces, curve, fmt, dim=0.0):
        self.keys = {k: i for i, k in enumerate(keys)}
        self.fmt = fmt
        self.n = len(faces)
        self.w, self.h = faces[0].get_size()
        self.hh = self.h // 2
        self.hb = self.h - self.hh
        self.master = faces
        self.set_dim(dim)
        self.pen = max(2, self.hb // 4)
        self.shadow = surf(self.w, self.hb + self.pen, True)
        for y in range(self.hb + self.pen):
            a = 135 if y < self.hb else int(135 * (1 - (y - self.hb + 1) / (self.pen + 1)))
            pygame.draw.line(self.shadow, (0, 0, 0, a), (0, y), (self.w, y))
        # The fall at each step: is the flap past horizontal, how tall it looks, how far its shadow reaches.
        tan_l = math.tan(LIGHT)
        self.steps = []
        for k in range(STEPS):
            phi = curve((k + 0.5) / STEPS)
            if phi < math.pi / 2:
                reach = round(self.hb * (math.sin(phi) * tan_l - math.cos(phi)))
                self.steps.append((False, max(1, round(self.hh * math.cos(phi))), max(0, min(self.hb, reach))))
            else:
                reach = round(self.hb * math.sin(phi) * tan_l)
                self.steps.append((True, max(1, round(self.hb * -math.cos(phi))), max(0, min(self.hb, reach))))

    def set_dim(self, dim):
        """Software brightness. Baked into the pictures so a moving flap costs nothing extra."""
        self.dim = dim
        self.faces = [self._finish(f.copy()) for f in self.master]
        self.tops = [f.subsurface((0, 0, self.w, self.hh)) for f in self.faces]
        self.bots = [f.subsurface((0, self.hh, self.w, self.hb)) for f in self.faces]
        self._top, self._bot = {}, {}

    def _finish(self, s):
        """Apply the dimming and convert to the screen's pixel format, so blits are plain copies."""
        if self.dim > 0.004:
            v = int(255 * (1 - self.dim))
            s.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
        return s.convert(self.fmt)

    def index(self, key):
        return self.keys.get(key, 0)

    def _leaf(self, idx, back, hpx):
        """A falling flap seen at the angle where it projects to hpx pixels tall."""
        full = self.hb if back else self.hh
        area = (0, self.hh, self.w, self.hb) if back else (0, 0, self.w, self.hh)
        s = pygame.transform.smoothscale(self.master[idx].subsurface(area), (self.w, hpx))
        c = max(0.0, min(1.0, hpx / full))
        phi = math.pi - math.acos(c) if back else math.acos(c)
        ndl = math.cos(phi + LIGHT)
        k = (AMBIENT + (1 - AMBIENT) * max(0.0, -ndl if back else ndl)) / REST
        if k < 0.995:
            v = int(255 * k)
            s.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
        elif k > 1.005:
            extra = s.copy()
            v = int(255 * min(1.0, k - 1))
            extra.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
            s.blit(extra, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        if hpx >= 3:      # the free edge of the card catches the light
            edge = surf(self.w - 2 * max(1, self.w // 8), 1)
            edge.fill((30, 30, 30))
            s.blit(edge, (max(1, self.w // 8), hpx - 1 if back else 0), special_flags=pygame.BLEND_RGB_ADD)
        return s

    def _shade(self, s, y0, length):
        if length > 0:
            s.blit(self.shadow, (0, y0), (0, self.hb - length, self.w, length + self.pen))

    def top(self, i, k):
        """Upper half of the window at step k of flap i falling away to show flap i+1."""
        back, hpx, _ = self.steps[k]
        nxt = (i + 1) % self.n
        if back:
            return self.tops[nxt]                   # the flap has left the upper half
        key = (i, hpx)
        s = self._top.get(key)
        if s is None:
            s = self.master[nxt].subsurface((0, 0, self.w, self.hh)).copy()
            s.blit(self._leaf(i, False, hpx), (0, self.hh - hpx))
            s = self._top[key] = self._finish(s)
        return s

    def bottom(self, i, k):
        """Lower half of the window at step k of the same fall."""
        back, hpx, reach = self.steps[k]
        if not back and reach <= 0:
            return self.bots[i]                     # nothing has reached the lower half yet
        key = (i, back, hpx, reach)
        s = self._bot.get(key)
        if s is None:
            s = self.master[i].subsurface((0, self.hh, self.w, self.hb)).copy()
            if back:
                s.blit(self._leaf((i + 1) % self.n, True, hpx), (0, 0))
                self._shade(s, hpx, reach)          # the flap's shadow on the old lower half
            else:
                self._shade(s, 0, reach)            # the tilting flap above starts to shade it
            s = self._bot[key] = self._finish(s)
        return s

    def warm(self, pause=0.0):
        """Compose every picture ahead of time, so the first flips don't stutter."""
        for i in range(self.n):
            for k in range(STEPS):
                self.top(i, k)
                self.bottom(i, k)
            if pause:
                time.sleep(pause)


class Module:
    """One drum. `cur` is the face fully landed, `head` the last one released."""
    __slots__ = ("board", "fs", "rect", "cur", "head", "tgt", "leaves", "timer", "period")

    def __init__(self, board, fs, pos, period):
        self.board, self.fs = board, fs
        self.rect = pygame.Rect(pos, (fs.w, fs.h))
        self.cur = self.head = self.tgt = 0
        self.leaves = []
        self.timer = 0.0
        self.period = period

    def set(self, idx, delay=0.0):
        if idx == self.tgt:
            return
        if self.head == self.tgt and not self.leaves:
            self.timer = delay
        self.tgt = idx
        self.board.active.add(self)

    def update(self, dt):
        lv = self.leaves
        if lv:
            inc = dt / self.board.fall
            for i in range(len(lv)):
                lv[i] += inc
            while lv and lv[0] >= 1.0:
                lv.pop(0)
                self.cur = (self.cur + 1) % self.fs.n
        if self.head != self.tgt:
            self.timer -= dt
            while self.timer <= 0 and self.head != self.tgt:
                self.head = (self.head + 1) % self.fs.n
                lv.append(min(0.98, -self.timer / self.board.fall))
                self.timer += self.period
        return bool(lv) or self.head != self.tgt

    def draw(self, dst):
        fs = self.fs
        x, y = self.rect.topleft
        lv = self.leaves
        if not lv:
            dst.blit(fs.faces[self.cur], (x, y))
            return
        cur = self.cur
        k0 = min(STEPS - 1, int(lv[0] * STEPS))             # the oldest flap in the air
        if len(lv) > 1 and fs.steps[k0][0]:
            # A second flap has been released and the first is already past horizontal:
            # the upper half belongs to the newer one.
            top = fs.top((cur + len(lv) - 1) % fs.n, min(STEPS - 1, int(lv[-1] * STEPS)))
        else:
            top = fs.top(cur, k0)
        dst.blit(top, (x, y))
        dst.blit(fs.bottom(cur, k0), (x, y + fs.hh))


class Indicator:
    """A lamp. mode -1 hidden, 0 off, 1 on, 2 slow blink, 3 fast blink."""

    def __init__(self, rect, off, on):
        self.rect, self.off, self.on = rect, off, on
        self.mode, self.lit, self.dirty = 0, False, True

    def set(self, mode):
        if mode != self.mode:
            self.mode, self.dirty = mode, True

    def tick(self, t):
        lit = self.mode == 1 or (self.mode == 2 and t % 1.0 < 0.55) or (self.mode == 3 and t % 0.36 < 0.18)
        if lit != self.lit:
            self.lit, self.dirty = lit, True
        return self.dirty


SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
            "7": "abc", "8": "abcdefg", "9": "abfgcd", "-": "g"}


def led_glyph(w, h, ch, color):
    """One character of a seven-segment LED display, as an alpha surface."""
    W, H = w * SS, h * SS
    s = surf(W, H, True)
    t = max(2, round(W * 0.2))                  # segment thickness
    half = H // 2
    if ch == "/":
        pygame.draw.line(s, color, (W - t, t), (t, H - t), t)
    else:
        bars = {"a": (t // 2, 0, W - t, t), "g": (t // 2, half - t // 2, W - t, t), "d": (t // 2, H - t, W - t, t),
                "f": (0, t // 2, t, half - t // 2), "b": (W - t, t // 2, t, half - t // 2),
                "e": (0, half, t, half - t // 2), "c": (W - t, half, t, half - t // 2)}
        for seg in SEGMENTS.get(ch, ""):
            pygame.draw.rect(s, color, bars[seg], border_radius=t // 2)
    return pygame.transform.smoothscale(s, (w, h))


class Readout:
    """A small LED counter in the header: which aircraft is showing, out of how many (2/5)."""

    def __init__(self, x, y, dw, dh, color=AMBER_LAMP, ghost=(52, 30, 8)):
        self.dw, self.dh, self.sw = dw, dh, max(3, round(dw * 0.8))
        self.gap = max(1, dw // 4)
        self.rect = pygame.Rect(x - (2 * dw + self.sw + 2 * self.gap) // 2, y, 2 * dw + self.sw + 2 * self.gap, dh)
        self.lit_g = {c: led_glyph(self.sw if c == "/" else dw, dh, c, color) for c in "0123456789-/"}
        self.dim_g = {c: led_glyph(self.sw if c == "/" else dw, dh, c, ghost) for c in "8/"}
        self.text, self.blink, self.lit, self.dirty = "", False, True, True
        self.mode = 0

    def set(self, text, blink=False):
        if (text, blink) != (self.text, self.blink):
            self.text, self.blink, self.dirty = text, blink, True

    def tick(self, t):
        lit = not self.blink or t % 1.0 < 0.6
        if lit != self.lit:
            self.lit, self.dirty = lit, True
        return self.dirty

    def paint(self, dst):
        """Unlit segments show faintly, as they do on a real display."""
        x = self.rect.x
        for i in range(3):
            ch = self.text[i] if i < len(self.text) else ""
            slash = i == 1
            dst.blit(self.dim_g["/" if slash else "8"], (x, self.rect.y))
            if ch and self.lit and self.text:
                dst.blit(self.lit_g[ch], (x, self.rect.y))
            x += (self.sw if slash else self.dw) + self.gap


def _mix(a, b, f):
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def lamp_sprite(w, h, color, on):
    W, H = w * SS, h * SS
    s = surf(W, H, True)
    cx, cy, m = W // 2, H // 2, min(W, H)
    R = int(m * 0.30)
    if on:
        for i in range(20):
            f = i / 19
            pygame.draw.circle(s, (*color, int(120 * f * f)), (cx, cy), int(m * 0.5 - (m * 0.5 - R) * f))
    pygame.draw.circle(s, (70, 71, 76), (cx, cy), R)
    pygame.draw.circle(s, (12, 12, 14), (cx, cy), int(R * 0.82))
    lens = max(2, int(R * 0.68))
    if on:
        for i in range(12):
            f = i / 11
            pygame.draw.circle(s, _mix(color, (255, 255, 255), f * 0.85), (cx, cy), max(1, int(lens * (1 - 0.88 * f))))
    else:
        base = tuple(int(v * 0.13) + 8 for v in color)
        pygame.draw.circle(s, base, (cx, cy), lens)
        pygame.draw.circle(s, tuple(v + 26 for v in base), (cx - lens // 3, cy - lens // 3), max(1, lens // 4))
    return pygame.transform.smoothscale(s, (w, h))


def _field(key, label, chars, drum, align="l", suffix="", su=0.0):
    return dict(key=key, label=label, chars=chars, drum=drum, align=align, suffix=suffix, su=su)


def card_spec(landscape: bool, unit_label: str):
    """Lines of the card, top to bottom. `cap` limits a line's flap size relative to the first line."""
    flight = _field("flight", "FLIGHT", 7, ALPHA)
    frm, to = _field("from", "FROM", 3, ALPHA), _field("to", "TO", 3, ALPHA)
    typ, kt = _field("type", "TYPE", 4, ALPHA), _field("kt", "KNOTS", 3, NUM, "r")
    alt = _field("alt", "ALT FT", 3, NUM, "r", "00", 1.25)
    dist = _field("dist", unit_label, 3, NUM, "r")
    dirn = _field("dir", "DIR", 3, ALPHA)
    elev = _field("elev", "ELEV", 2, NUM, "r", "°", 0.6)
    if landscape:
        return [
            dict(cap=1.0, items=[flight, dict(key="remark", label="STATUS", word=3.1)]),
            dict(cap=0.86, items=[frm, to, typ, kt]),
            dict(cap=0.86, items=[alt, dist, dirn, elev]),
        ]
    return [
        dict(cap=1.0, items=[flight]),
        dict(cap=0.72, items=[dict(key="remark", label="STATUS", word=0)]),
        dict(cap=0.86, items=[frm, to]),
        dict(cap=0.86, items=[typ, kt]),
        dict(cap=0.86, items=[alt, dist]),
        dict(cap=0.86, items=[dirn, elev]),
    ]


class Board:
    """One aircraft at a time, on flaps as large as the screen allows."""
    MAX_PAGES = 8

    def __init__(self, screen, cfg):
        self.scr, self.cfg = screen, cfg
        self.W, self.H = screen.get_size()
        self.fonts = Fonts(cfg.get("font"))
        self.active = set()
        self.t = 0.0
        self.rate = max(4.0, min(30.0, float(cfg.get("flaps_per_sec", 12))))
        self.fall, self._table = fall_curve(self.rate, len(ALPHA))
        self.dim = 0.0
        self.status = ("", "", True)
        self._status_drawn = None
        self._fs = {}
        self._lettering = {}
        self._layout()
        self._modules()
        self._background()
        self._overlay()
        self.redraw()
        if cfg.get("warm", True):
            threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        """Background job: compose every mid-flip picture, most-used drums first."""
        try:
            for fs in sorted({m.fs for m in self.modules}, key=lambda f: -sum(1 for m in self.modules if m.fs is f)):
                fs.warm(0.004)
        except Exception:
            pass                # a cache miss later only costs a moment

    # ---------------------------------------------------------- geometry ----
    def curve(self, s):
        t = self._table
        x = s * (len(t) - 1)
        i = int(x)
        if i >= len(t) - 1:
            return math.pi
        if i < 0:
            return 0.0
        return t[i] + (t[i + 1] - t[i]) * (x - i)

    def _ch(self, u):
        return int(round(u * self.aspect))

    def _layout(self):
        W, H = self.W, self.H
        self.unit_label = {"mi": "MI", "km": "KM", "nm": "NM"}.get(self.cfg.get("units", "mi"), "MI")
        spec = card_spec(W >= H, self.unit_label)
        self.margin = margin = max(6, round(W * 0.017))
        avail = W - 2 * margin
        n = len(spec)

        def units(line):
            total = 0.0
            for it in line["items"]:
                if "word" in it:
                    total += it["word"]
                else:
                    total += it["chars"] + it["su"] + (LAMP_U + LAMP_GAP_U if it["key"] == "flight" else 0)
            return total + GAP_U * (len(line["items"]) - 1)

        def measure(U):
            us, fits = [], True
            for line in spec:
                full = [it for it in line["items"] if it.get("word") == 0]
                u = round(U * line["cap"]) if full else min(round(U * line["cap"]), int(avail / units(line)))
                if line["cap"] == 1.0 and u < U:
                    fits = False
                us.append(u)
            lab = max(6, round(U * 0.22))
            lab_h = lab + max(2, round(U * 0.1)) + max(3, round(lab * 0.5))   # label, bezel, breathing room
            cu = max(9, round(U * 0.36))
            head_h = int(round(cu * BASE_ASPECT)) + 2 * max(2, round(U * 0.07))
            foot_h = lab + max(4, round(U * 0.14))
            minpad = max(3, round(U * 0.1))
            return us, fits, lab, lab_h, cu, head_h, foot_h, minpad, head_h + foot_h + n * lab_h + minpad * (n + 1)

        self.aspect = BASE_ASPECT
        for U in range(96, 7, -1):
            us, fits, lab, lab_h, cu, head_h, foot_h, minpad, fixed = measure(U)
            if fits and fixed + sum(self._ch(u) for u in us) <= H:
                break
        # Flap width is set by the width of the screen. Height left over goes into taller flaps,
        # up to a limit, so the card fills the panel.
        self.aspect = max(BASE_ASPECT, min(MAX_ASPECT, (H - fixed) / sum(us)))
        while self.aspect > BASE_ASPECT and fixed + sum(self._ch(u) for u in us) > H:
            self.aspect -= 0.01
        total = fixed + sum(self._ch(u) for u in us)
        self.U, self.lab, self.lab_h, self.cu = U, lab, lab_h, cu
        self.cch = int(round(cu * BASE_ASPECT))
        self.head_h, self.foot_h = head_h, foot_h
        slack = max(0, H - total)
        pad = minpad + slack // (n + 1)
        y = head_h + pad + (slack % (n + 1)) // 2
        self.lines = []
        for u, line in zip(us, spec):
            ch = self._ch(u)
            L = dict(u=u, ch=ch, y=y + lab_h, items=[])
            widths = []
            for it in line["items"]:
                if "word" in it:
                    widths.append(avail if it["word"] == 0 else round(it["word"] * u))
                else:
                    lead = round((LAMP_U + LAMP_GAP_U) * u) if it["key"] == "flight" else 0
                    widths.append(lead + it["chars"] * u + round(it["su"] * u))
            gap = (avail - sum(widths)) / (len(widths) - 1) if len(widths) > 1 else 0
            x = float(margin)
            for it, w in zip(line["items"], widths):
                L["items"].append(dict(it, x=round(x), w=w))
                x += w + gap
            self.lines.append(L)
            y = L["y"] + ch + pad
        self.foot_y = H - foot_h

    def faces(self, drum, u, ch=None):
        """FaceSet for a character drum at flap width u."""
        ch = ch or self._ch(u)
        key = (drum, u, ch)
        if key not in self._fs:
            cap = round(ch * SS * 0.56)
            store = self._fs.setdefault(("chars", u, ch), {})
            out = []
            for c in drum:
                if c not in store:
                    art = text_line(self.fonts, c, cap, INK, 0, 0.86, max_w=round(u * SS * 0.72))
                    store[c] = make_face(u, ch, u, art)
                out.append(store[c])
            self._fs[key] = FaceSet(list(drum), out, self.curve, self.scr, self.dim)
        return self._fs[key]

    def word_faces(self, names, width, u, styles=None):
        ch = self._ch(u)
        out = []
        for name in names:
            flap, ink = (styles or {}).get(name, (FLAP, INK))
            art = text_line(self.fonts, name, round(ch * SS * 0.36), ink, round(u * SS * 0.05), 0.9,
                            max_w=round(width * SS * 0.86))
            out.append(make_face(width, ch, u, art, flap))
        return FaceSet(names, out, self.curve, self.scr, self.dim)

    def _period(self, slow=1.0):
        return slow / self.rate * random.uniform(0.95, 1.06)   # no two motors run at quite the same speed

    def _modules(self):
        self.modules, self.indicators, self.cells = [], [], {}
        self.insets, self.suffixes, self.labels = [], [], []
        for L in self.lines:
            u, ch, y = L["u"], L["ch"], L["y"]
            for it in L["items"]:
                x = it["x"]
                ly = y - max(2, round(u * 0.1)) - self.lab - 2
                self.labels.append((it["label"], x, ly))
                if "word" in it:
                    fs = self.word_faces(REMARKS, it["w"], u, {"MILITARY": (AMBER, DARK),
                                                               "EMERGENCY": (RED, (255, 255, 255))})
                    self.remark = Module(self, fs, (x, y), self._period(1.5))
                    self.modules.append(self.remark)
                    self.insets.append((pygame.Rect(x, y, it["w"], ch), u))
                    continue
                if it["key"] == "flight":
                    lw, lh = round(LAMP_U * u), ch // 2
                    self.view = Indicator(pygame.Rect(x, y, lw, lh), lamp_sprite(lw, lh, GREEN_LAMP, False),
                                          lamp_sprite(lw, lh, GREEN_LAMP, True))
                    self.mil = Indicator(pygame.Rect(x, y + lh, lw, ch - lh), lamp_sprite(lw, ch - lh, AMBER_LAMP, False),
                                         lamp_sprite(lw, ch - lh, AMBER_LAMP, True))
                    self.view_green, self.view_red = self.view.on, lamp_sprite(lw, lh, RED_LAMP, True)
                    self.indicators += [self.view, self.mil]
                    x += round((LAMP_U + LAMP_GAP_U) * u)
                    self.labels[-1] = (it["label"], x, ly)
                fs = self.faces(it["drum"], u)
                mods = [Module(self, fs, (x + j * u, y), self._period()) for j in range(it["chars"])]
                self.cells[it["key"]] = (mods, it["align"])
                self.modules += mods
                self.insets.append((pygame.Rect(x, y, it["chars"] * u, ch), u))
                if it["suffix"]:
                    self.suffixes.append((it["suffix"], x + it["chars"] * u + max(2, round(u * 0.16)), y, ch))
        # header: page lamps and a small clock
        cu, cch = self.cu, self.cch
        self.head_y = (self.head_h - cch) // 2
        self.colon_w = round(cu * 0.6)
        self.clock_x = self.W - self.margin - (4 * cu + self.colon_w)
        fs = self.faces(CLOCK, cu, cch)
        cx = self.clock_x
        self.clock = [Module(self, fs, (x, self.head_y), self._period(1.3))
                      for x in (cx, cx + cu, cx + 2 * cu + self.colon_w, cx + 3 * cu + self.colon_w)]
        for m in self.clock:
            m.cur = m.head = m.tgt = fs.index(" ")
        self.modules += self.clock
        dh = max(9, round(self.head_h * 0.56))
        self.count = Readout(self.W // 2, (self.head_h - dh) // 2, max(5, round(dh * 0.56)), dh)
        self.indicators.append(self.count)
        ld = max(5, self.lab + 2)
        self.led_green, self.led_red = lamp_sprite(ld, ld, GREEN_LAMP, True), lamp_sprite(ld, ld, RED_LAMP, True)
        self.led = Indicator(pygame.Rect(self.W - self.margin - ld, self.foot_y + (self.foot_h - ld) // 2 - 1, ld, ld),
                             lamp_sprite(ld, ld, RED_LAMP, False), self.led_green)
        self.led.set(1)
        self.indicators.append(self.led)

    # -------------------------------------------------------- static art ----
    def paint(self, dst, text, cap, x, y, anchor="l", color=PAINT, alpha=255, spacing=None):
        """Lettering painted on the panel. y is the top of the capitals. Returns its width."""
        key = (text, cap, color, alpha, spacing)
        hit = self._lettering.get(key)
        if hit is None:
            sp = round(cap * SS * 0.14) if spacing is None else spacing * SS
            art = text_line(self.fonts, text, cap * SS, color, sp, 0.92)
            if not art:
                return 0
            a, top, _ = art
            s = pygame.transform.smoothscale(a, (max(1, a.get_width() // SS), max(1, a.get_height() // SS)))
            if alpha < 255:
                s.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
            if len(self._lettering) > 400:          # counts and distances change all day: don't keep them all
                self._lettering.clear()
            hit = self._lettering[key] = (s, round(top / SS))
        s, top = hit
        w = s.get_width()
        x = x - w if anchor == "r" else x - w // 2 if anchor == "c" else x
        dst.blit(s, (x, y - top))
        return w

    def _inset(self, bg, rect, u):
        b = max(2, round(u * 0.1))
        r = rect.inflate(2 * b, 2 * b)
        rad = max(2, round(u * 0.14))
        pygame.draw.rect(bg, (58, 59, 64), r.move(0, 1), border_radius=rad)
        pygame.draw.rect(bg, (10, 10, 11), r.move(0, -1), border_radius=rad)
        pygame.draw.rect(bg, RECESS, r, border_radius=rad)

    def _background(self):
        W, H = self.W, self.H
        rnd = random.Random(11)
        bg = surf(W, H)
        for y in range(H):                      # brushed metal: faint horizontal streaks
            d = rnd.randint(-2, 2)
            pygame.draw.line(bg, tuple(c + d for c in PANEL), (0, y), (W, y))
        tile = surf(64, 64)
        for ty in range(64):
            for tx in range(64):
                v = rnd.randint(0, 5)
                tile.set_at((tx, ty), (v, v, v))
        for ty in range(0, H, 64):
            for tx in range(0, W, 64):
                bg.blit(tile, (tx, ty), special_flags=pygame.BLEND_RGB_ADD)

        self.texture = bg.copy()                # the bare panel, for other screens that share the look
        c = self.count.rect.inflate(max(6, self.count.dw), max(4, self.count.dh // 3))
        pygame.draw.rect(bg, (58, 59, 64), c.move(0, 1), border_radius=3)
        pygame.draw.rect(bg, (4, 4, 5), c, border_radius=3)             # the counter's dark window
        title = str(self.cfg.get("title") or "OVERHEAD").upper()
        tcap = max(6, round(self.head_h * 0.36))
        self.paint(bg, title, tcap, self.margin, (self.head_h - tcap) // 2, color=INK, alpha=225)
        cu, cch, cx = self.cu, self.cch, self.clock_x
        self._inset(bg, pygame.Rect(cx, self.head_y, 2 * cu, cch), cu)
        self._inset(bg, pygame.Rect(cx + 2 * cu + self.colon_w, self.head_y, 2 * cu, cch), cu)
        for dy in (0.36, 0.64):
            pygame.draw.circle(bg, PAINT, (cx + 2 * cu + self.colon_w // 2, self.head_y + round(cch * dy)),
                               max(1, round(cu * 0.1)))
        pygame.draw.line(bg, (12, 12, 13), (self.margin, self.head_h), (W - self.margin, self.head_h))
        pygame.draw.line(bg, (52, 53, 58), (self.margin, self.head_h + 1), (W - self.margin, self.head_h + 1))

        for label, x, y in self.labels:
            self.paint(bg, label, self.lab, x + 1, y, alpha=185)
        for rect, u in self.insets:
            self._inset(bg, rect, u)
        for text, x, y, ch in self.suffixes:      # fixed characters printed on the panel after the flaps
            cap = round(ch * 0.5)
            self.paint(bg, text, cap, x, y + (ch - cap) // 2, alpha=150, spacing=1)
        self.bg = bg.convert(self.scr)
        self.foot_rect = pygame.Rect(self.margin, self.foot_y, self.W - 2 * self.margin - self.led.rect.w - 4, self.foot_h)

    def _overlay(self):
        """Room lighting on the panel: darker corners, light falling off toward the bottom. The flaps
        themselves are drawn over it, evenly lit, so a moving flap needs no blending."""
        small = surf(40, 24, True)
        for y in range(24):
            for x in range(40):
                dx, dy = (x - 19.5) / 19.5, (y - 11.5) / 11.5
                v = max(0.0, math.sqrt(dx * dx + dy * dy) - 0.6) / 0.8
                a = 80 * v * v + 20 * (y / 23) + 255 * self.dim
                small.set_at((x, y), (0, 0, 0, max(0, min(235, int(a)))))
        self.overlay = pygame.transform.smoothscale(small, (self.W, self.H))

    # ------------------------------------------------------------- state ----
    def _spell(self, mods, text, align="l"):
        n = len(mods)
        text = text[:n]
        text = text.rjust(n) if align == "r" else text.ljust(n)
        for m, c in zip(mods, text):
            m.set(m.fs.index(c), random.uniform(0.0, 0.05))

    def set_card(self, d, alarm=False, message=""):
        """Show one aircraft. d maps field names to strings, plus remark, view and mil.

        None means there is nothing to show: every flap goes blank and the top lamp
        turns red. With `alarm` the lamp blinks, and `message` is spelled on the flight
        flaps to say why (NO DATA).
        """
        empty = d is None
        d = d or {"flight": message}
        for key, (mods, align) in self.cells.items():
            self._spell(mods, str(d.get(key, "")), align)
        self.remark.set(self.remark.fs.index(d.get("remark", "")), random.uniform(0.0, 0.05))
        self.view.on = self.view_red if empty else self.view_green
        self.view.set((3 if alarm else 1) if empty else 1 if d.get("view") else 0)
        self.view.dirty = True
        self.mil.set({0: 0, 1: 2, 2: 3}[int(d.get("mil", 0))])

    def set_pages(self, count, current, locked=False):
        """The counter in the header: 2/5 is the second of five aircraft. It blinks while the card
        is locked onto one. A locked aircraft that has dropped out of the rotation shows as -/5."""
        if count < 1 and not locked:
            self.count.set("")
        else:
            self.count.set("%s/%d" % (current + 1 if current >= 0 else "-", min(9, count)), locked)

    def set_clock(self, text):
        for m, c in zip(self.clock, text):
            m.set(m.fs.index(c), random.uniform(0.0, 0.04))

    def set_status(self, left, right, ok=True):
        self.status = (left, right, ok)
        self.led.on = self.led_green if ok else self.led_red
        self.led.set(1 if ok else 3)
        self.led.dirty = True

    def set_dim(self, dim):
        dim = max(0.0, min(0.85, dim))
        if abs(dim - self.dim) > 0.004:
            self.dim = dim
            self._overlay()
            for fs in {m.fs for m in self.modules}:
                fs.set_dim(dim)
            threading.Thread(target=self._warm, daemon=True).start()
            return self.redraw()
        return []

    @property
    def busy(self):
        return bool(self.active)

    # ----------------------------------------------------------- drawing ----
    def _draw_ind(self, ind):
        self.scr.blit(self.bg, ind.rect, ind.rect)
        if isinstance(ind, Readout):
            ind.paint(self.scr)
            ind.dirty = False
            return
        if ind.mode >= 0:
            self.scr.blit(ind.on if ind.lit else ind.off, ind.rect)
        self.scr.blit(self.overlay, ind.rect, ind.rect)
        ind.dirty = False

    def _draw_status(self):
        r = self.foot_rect
        self.scr.blit(self.bg, r, r)
        y = r.y + (r.h - self.lab) // 2 - 1
        left, right, _ = self.status
        self.paint(self.scr, left, self.lab, r.x, y, alpha=150)
        self.paint(self.scr, right, self.lab, r.right - 2, y, "r", alpha=150)
        self.scr.blit(self.overlay, r, r)
        self._status_drawn = self.status
        return r

    def redraw(self):
        self.scr.blit(self.bg, (0, 0))
        self.scr.blit(self.overlay, (0, 0))
        for m in self.modules:
            m.draw(self.scr)
        for ind in self.indicators:
            ind.tick(self.t)
            self._draw_ind(ind)
        self._draw_status()
        return [self.scr.get_rect()]

    def update(self, dt):
        """Advance every moving flap and repaint just those. Returns the rectangles that changed."""
        self.t += dt
        scr, rects, done = self.scr, [], []
        for m in self.active:
            busy = m.update(dt)
            m.draw(scr)
            rects.append(m.rect)
            if not busy:
                done.append(m)
        for m in done:
            self.active.discard(m)
        for ind in self.indicators:
            if ind.tick(self.t):
                self._draw_ind(ind)
                rects.append(ind.rect)
        if self._status_drawn != self.status:
            rects.append(self._draw_status())
        return rects
