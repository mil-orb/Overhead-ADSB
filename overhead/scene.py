"""The sky and the ground behind the aircraft in the plane view.

The sky is worked out from what the app already knows: where the sun and moon are for this
place and minute, and the cloud cover and visibility from the weather feed. Midday is blue,
a low sun turns the horizon orange on its side of the sky, night has stars and a moon in
the right phase, and an overcast day is grey.

Below the horizon is the real country around you once ground.py has made its pictures
(aerial photographs by day, lights at night), lit for the time of day and faded into the
haze with distance. Until then, and in the demo, it is drawn fields.

It is painted once, as a panorama of the whole sky and ground, on a background thread.
After that, showing any direction is a single copy of a window out of it, which is why
looking around costs nothing. The panorama is repainted when the light or the weather has
changed enough to see, every few minutes around sunset and rarely otherwise.

Directions map straight to pixels: azimuth across, elevation up. That stretches things
near the point overhead, where nothing has edges to notice.
"""
from __future__ import annotations

import math
import random
import threading

import pygame

from .ground import distance
from .sky import smooth

HALF_FOV = 24.0             # degrees from the middle of the window to its top edge
TOP = 90.0 + HALF_FOV + 1   # the panorama runs this far above and below the horizon

# sun elevation -> (sky overhead, sky at the horizon)
SKY = [(-18.0, (3, 4, 10), (8, 10, 20)), (-12.0, (6, 9, 24), (22, 26, 50)), (-6.0, (16, 24, 58), (96, 76, 96)),
       (-2.0, (30, 46, 98), (196, 124, 98)), (2.0, (44, 74, 140), (228, 168, 120)), (8.0, (48, 92, 164), (190, 194, 204)),
       (20.0, (44, 96, 176), (152, 188, 224)), (60.0, (36, 88, 176), (142, 182, 224))]
FIELDS_GREEN = [(74, 104, 52), (92, 116, 58), (110, 124, 66), (128, 128, 74), (60, 92, 50), (150, 140, 90), (84, 110, 60)]
FIELDS_BROWN = [(120, 108, 78), (134, 122, 90), (104, 96, 70), (146, 136, 104), (92, 90, 66), (110, 104, 84), (126, 118, 96)]
TOWN = (126, 124, 118)
CELL, REACH, FAR = 0.45, 7.0, 34.0      # field size, how far out single fields are drawn, and how far
                                        # the big distant ones go, all in eye heights


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def clip(c):
    return tuple(0 if v < 0 else 255 if v > 255 else int(v) for v in c)


def sky_colours(sun_elev, cloud=0.0, vis_m=40000.0):
    """(overhead, horizon) colours for this sun height, greyed by cloud and whitened by haze."""
    lo = SKY[0]
    for hi in SKY[1:]:
        if sun_elev <= hi[0]:
            break
        lo = hi
    else:
        hi = lo
    t = 0.0 if hi[0] == lo[0] else max(0.0, min(1.0, (sun_elev - lo[0]) / (hi[0] - lo[0])))
    top, bottom = mix(lo[1], hi[1], t), mix(lo[2], hi[2], t)
    grey = 0.9 * smooth(0.45, 1.0, cloud)
    if grey:
        for_top = sum(bottom) / 3 * 0.8 + sum(top) / 3 * 0.2          # an overcast is about as bright as the horizon was
        top = mix(top, (for_top * 0.94, for_top * 0.96, for_top), grey)
        lum = sum(bottom) / 3
        bottom = mix(bottom, (lum * 0.96, lum * 0.98, lum), grey)
    haze = smooth(16000.0, 1500.0, vis_m)
    if haze:
        lum = sum(bottom) / 3
        mist = (lum * 0.98, lum * 0.99, lum)
        top, bottom = mix(top, mist, 0.75 * haze), mix(bottom, mist, 0.6 * haze)
    return top, bottom


def tone(sky):
    """How the aircraft is lit: (ambient rgb, direct rgb, where the direct light comes from as
    (elevation, azimuth), a small key that changes when the look does).

    A face's colour is its paint times (ambient + direct * how squarely it faces the light).
    """
    h = sky.sun_elev
    cloud = sky.cloud_total if sky.known else 0.0
    day = smooth(-6.0, 8.0, h)
    low = smooth(-4.0, 2.0, h) * (1 - smooth(4.0, 16.0, h))         # sunrise and sunset
    direct = day * (1 - 0.8 * smooth(0.3, 1.0, cloud))
    dc = mix((1.0, 0.98, 0.94), (1.0, 0.66, 0.40), low)
    ac = mix((0.94, 0.97, 1.06), (0.98, 0.86, 0.92), low)
    amb_level = 0.17 + 0.47 * day + 0.10 * day * smooth(0.3, 1.0, cloud)
    amb = [amb_level * c for c in ac]
    if day < 0.5:
        night = (0.82, 0.92, 1.3)                                    # what is left of the light is blue
        amb = list(mix(amb, [amb_level * c for c in night], 1 - 2 * day))
    dirc = [0.60 * direct * c for c in dc]
    source = (max(4.0, h), sky.sun_az)
    if direct < 0.05 and sky.moon_elev > 2:
        m = 0.16 * sky.moon_lit * (1 - 0.8 * cloud)
        dirc = [m * 0.86, m * 0.94, m * 1.2]
        source = (max(8.0, sky.moon_elev), sky.moon_az)
    elif direct < 0.05:
        source = (70.0, sky.sun_az)                                  # skyglow from above
    key = tuple(int(v * 24 + 0.5) for v in amb + dirc)
    return tuple(amb), tuple(dirc), source, key


def _glow(w, h, colour, steps=40):
    """A soft elliptical glow to add onto the sky."""
    small = pygame.Surface((steps * 2, steps * 2), 0, 32)
    small.fill((0, 0, 0))
    for i in range(steps, 0, -1):
        k = (1 - i / steps) ** 2
        pygame.draw.circle(small, clip([c * k for c in colour]), (steps, steps), i)
    return pygame.transform.smoothscale(small, (max(2, int(w)), max(2, int(h))))


def _grey(data, w, h):
    """Grey bytes -> an RGB surface."""
    rgb = bytearray(w * h * 3)
    rgb[0::3] = rgb[1::3] = rgb[2::3] = data
    return pygame.image.frombytes(bytes(rgb), (w, h), "RGB")


def _noise(rng, w, h, cells_x, cells_y, octaves, amp=128):
    """Fractal value noise as a grey surface: random grids of growing fineness, each smoothed
    up to full size by pygame and added at half the strength of the last. More cells across
    than down gives streaks."""
    out = pygame.Surface((w, h), 0, 32)
    out.fill((0, 0, 0))
    for o in range(octaves):
        nx, ny = cells_x * 2 ** o, cells_y * 2 ** o
        layer = pygame.transform.smoothscale(_grey(rng.randbytes(nx * ny), nx, ny), (w, h))
        layer.fill((amp, amp, amp), special_flags=pygame.BLEND_RGB_MULT)
        out.blit(layer, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        amp //= 2
    return out


_FIELDS = {}


def _rows_through(field, pw, rows, table_for):
    """Each row of a grey field through its own byte table; table_for(row) gives the table."""
    out = bytearray(field)
    for y in range(rows):
        t = table_for(y)
        if t is not None:
            out[y * pw:(y + 1) * pw] = field[y * pw:(y + 1) * pw].translate(t)
    return bytes(out)


def _level(field, pw, rows, ppd):
    """Even out a cloud field with elevation, so a cover fraction looks the same at any height.

    Two things get in the way. Toward the horizon a pixel takes in more of the cloud sheet,
    so the field is smoother there and one threshold would leave the low sky clear. And
    everything above about 30 degrees comes from a small patch of the sheet, so a light or
    dark patch there would make the zenith cloudy or clear whatever the weather. Each band
    of 5 degrees is shifted to the same average and scaled to the same spread (at most 3
    times up, so a band of pure haze isn't blown up into noise). Shapes inside a band keep
    their size. Returns (levelled field, the spread they all have now)."""
    step = max(1, int(5 * ppd))
    means, devs = [], []
    for y0 in range(0, rows, step):
        seg = field[y0 * pw:min(rows, y0 + step) * pw:11]
        m = sum(seg) / len(seg)
        means.append(m)
        devs.append(max(1.0, math.sqrt(sum((v - m) ** 2 for v in seg) / len(seg))))
    mid = sum(means) / len(means)
    aim = sorted(devs)[len(devs) * 3 // 4]
    gains = [max(0.6, min(3.0, aim / d)) for d in devs]
    tables = {}

    def table_for(y):
        f = max(0.0, min(len(gains) - 1.0, (y + 0.5) / step - 0.5))
        k = (int(f), round((f - int(f)) * 4))                       # four steps between bands are plenty
        if k not in tables:
            b, t = k[0], k[1] / 4
            c = min(b + 1, len(gains) - 1)
            m, g = means[b] + (means[c] - means[b]) * t, gains[b] + (gains[c] - gains[b]) * t
            tables[k] = bytes([max(0, min(255, int(mid + (v - m) * g + 0.5))) for v in range(256)])
        return tables[k]
    return _rows_through(field, pw, rows, table_for), aim


def cloud_field(pw, rows, ppd):
    """Two cloud layers as they look from the ground, as grey bytes pw x rows (row 0 is the
    top of the panorama): low cumulus and high streaky cirrus. Thicker cloud is brighter.

    Each layer is a flat sheet of noise at a fixed height. A direction in the sky meets the
    sheet at cot(elevation) heights away, so clouds overhead are big and round and the ones
    toward the horizon get smaller and flatter, as they do. Far off, where a sheet would only
    be noise, it fades to its average.

    The sheets are sampled on a coarse grid in Python (about 33,000 points per layer, once per
    run) and smoothed up to the panorama by pygame. Returns (low, high, sorted low samples,
    sorted high samples); the sorted samples turn a cover fraction into a threshold."""
    key = (pw, rows, round(ppd, 4))
    hit = _FIELDS.get(key)
    if hit:
        return hit
    rng = random.Random(1234)
    size = 512
    half = size // 2

    def sheet(cx, cy):
        return pygame.image.tobytes(_noise(rng, size, size, cx, cy, 4), "RGB")[0::3]

    # (noise, texture pixels per sheet height, fade from, to, detail strength and cells)
    sheets = ((sheet(14, 14), 18.0, 9.0, 22.0, 30, (pw // 20, rows // 10)),
              (sheet(28, 4), 8.0, 5.0, 14.0, 14, (pw // 16, rows // 4)))
    cw, ch = 270, 120
    out = []
    for tex, scale, fade0, fade1, detail, cells in sheets:
        mean = sum(tex[::97]) / len(tex[::97])
        grid = bytearray(cw * ch)
        sin_a = [math.sin(math.radians((i + 0.5) * 360.0 / cw)) for i in range(cw)]
        cos_a = [math.cos(math.radians((i + 0.5) * 360.0 / cw)) for i in range(cw)]
        for j in range(ch):
            e = math.radians(TOP - (j + 0.5) / ch * rows / ppd)
            se, ce = math.sin(e), math.cos(e)
            if se < 0.02:
                grid[j * cw:(j + 1) * cw] = bytes([int(mean)]) * cw
                continue
            r = ce / se                                              # how many sheet heights away, horizontally
            fade = smooth(fade0, fade1, abs(r))
            reach = r * scale
            for i in range(cw):                                      # the sheet repeats past its edges
                u, v = int(half + reach * sin_a[i]) % size, int(half - reach * cos_a[i]) % size
                val = tex[v * size + u]
                grid[j * cw + i] = int(val + (mean - val) * fade)
        big = pygame.transform.smoothscale(_grey(grid, cw, ch), (pw, rows))
        # Fine detail added on the panorama itself, so cloud edges are ragged instead of
        # following the coarse grid. Its cells are wider than tall: a panorama pixel spans
        # less sky across than down everywhere above the horizon, and low clouds are flat.
        big.blit(_noise(rng, pw, rows, cells[0], cells[1], 2, detail), (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        big.fill((detail * 3 // 4,) * 3, special_flags=pygame.BLEND_RGB_SUB)
        field, aim = _level(pygame.image.tobytes(big, "RGB")[0::3], pw, rows, ppd)
        seen = sorted(field[:rows * pw:29])
        # Low in the sky the same cover shows as more cloud: distant clouds are seen side-on
        # and close the gaps between them. Added after the samples are taken.
        lift = {}

        def raise_low(y, aim=aim):
            k = round(0.7 * aim * (1 - smooth(3.0, 25.0, TOP - (y + 0.5) / ppd)))
            if k <= 0:
                return None
            if k not in lift:
                lift[k] = bytes(min(255, v + k) for v in range(256))
            return lift[k]
        out += [_rows_through(field, pw, rows, raise_low), seen]
    _FIELDS[key] = hit = (out[0], out[2], out[1], out[3])
    return hit


class Scene:
    """A panorama of sky and ground, and windows cut from it."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ppd = h / (2 * HALF_FOV)                                # pixels per degree
        self.pw = int(round(360 * self.ppd))
        self.ph = int(round(2 * TOP * self.ppd))
        self.pano = None
        self.key = None             # what the panorama on show was painted for
        self.fresh = False          # a new one arrived since anyone looked
        self.flat = (40, 80, 150)   # stand-in colour until the first one is ready
        self.ground = None          # a ground.Ground with real pictures of the ground, or None for drawn fields
        self.credit = ""            # who the ground pictures on show came from
        self._painted_credit = ""
        self._want = self._want_sky = None
        self._thread = None
        self._busy = False
        self._lock = threading.Lock()

    def use_ground(self, ground):
        """Real ground pictures for this location (ground.Ground), or None for the drawn fields."""
        self.ground = ground

    def _key(self, sky):
        """What a panorama is painted from: the sky's state, plus which ground pictures, once they exist."""
        g = self.ground
        state = self.state(sky)
        return state + (g.args[1:3],) if g is not None and g.ready() else state

    # ------------------------------------------------------------------ state --
    @staticmethod
    def state(sky):
        """The sky reduced to what can be seen to change. Two skies with the same state look the same."""
        h = sky.sun_elev
        known = bool(sky.known)
        return (
            round(h) if -20 < h < 24 else (-20 if h <= -20 else 24 + 12 * int((h - 24) / 12)),
            round(sky.sun_az / 3) if h > -9 else 0,
            round((max(sky.cloud_low, sky.cloud_mid) if known else 0) * 8), round((sky.cloud_high if known else 0) * 6),
            round((sky.cloud_total if known else 0) * 8),
            0 if not known or sky.vis_m >= 16000 else 1 + int(sky.vis_m // 2500),
            (round(sky.moon_elev / 3), round(sky.moon_az / 3), round(sky.moon_lit * 8), bool(sky.moon_waxing))
            if sky.moon_elev > -2 and h < 8 else None,
            bool(sky.growing),
        )

    def request(self, sky):
        """Ask for a panorama of this sky. Returns at once; the painting happens on a thread."""
        key = self._key(sky)
        with self._lock:
            if key == self.key or key == self._want:
                return
            self._want, self._want_sky = key, sky
            if self.pano is None:
                self.flat = clip(mix(*sky_colours(sky.sun_elev, sky.cloud_total if sky.known else 0, sky.vis_m), 0.35))
            if self._busy:
                return                                               # the running thread picks the new request up
            self._busy = True
            self._thread = threading.Thread(target=self._work, daemon=True, name="scene")
            self._thread.start()

    def _work(self):
        while True:
            with self._lock:
                key, sky = self._want, self._want_sky
            try:
                pano = self.paint(sky)
            except Exception:
                pano = None                                          # keep whatever is showing
            with self._lock:
                if pano is not None:
                    self.pano, self.key, self.fresh, self.credit = pano, key, True, self._painted_credit
                if self._want == key:
                    self._want, self._busy = None, False
                    return

    def wait(self, timeout=20.0):
        t = self._thread
        if t:
            t.join(timeout)

    def take_fresh(self):
        fresh, self.fresh = self.fresh, False
        return fresh

    # ----------------------------------------------------------------- window --
    def window(self, dst, az, elev):
        """Copy the view centred on this direction into dst (which is w by h)."""
        pano = self.pano
        if pano is None:
            dst.fill(self.flat)
            return
        w, h = self.w, self.h
        x0 = int(round((az % 360.0) * self.ppd - w / 2)) % self.pw
        y0 = max(0, min(self.ph - h, int(round((TOP - elev) * self.ppd - h / 2))))
        dst.blit(pano, (0, 0), (x0, y0, w, h))
        if x0 + w > self.pw:
            dst.blit(pano, (self.pw - x0, 0), (0, y0, x0 + w - self.pw, h))

    # --------------------------------------------------------------- painting --
    def _x(self, az):
        return (az % 360.0) * self.ppd

    def _y(self, elev):
        return (TOP - elev) * self.ppd

    def _wrap(self, pano, sprite, cx, cy, flags=0):
        """Blit centred at (cx, cy), and again across the seam where the panorama wraps."""
        w, h = sprite.get_size()
        x, y = int(cx - w / 2), int(cy - h / 2)
        pano.blit(sprite, (x, y), special_flags=flags)
        if x < 0:
            pano.blit(sprite, (x + self.pw, y), special_flags=flags)
        if x + w > self.pw:
            pano.blit(sprite, (x - self.pw, y), special_flags=flags)

    def paint(self, sky):
        ppd, pw, ph = self.ppd, self.pw, self.ph
        h = sky.sun_elev
        known = bool(sky.known)
        cloud = sky.cloud_total if known else 0.0
        vis = sky.vis_m if known else 40000.0
        top, bottom = sky_colours(h, cloud, vis)
        haze = smooth(16000.0, 1500.0, vis)
        rng = random.Random(20)
        pano = pygame.Surface((pw, ph), 0, 32)
        line = pygame.draw.line
        horizon = int(self._y(0.0))
        power = 0.55 + 1.2 * haze

        # sky: one colour per row, horizon colour fading up to the overhead colour
        for y in range(horizon + 1):
            e = TOP - y / ppd
            if e > 90:
                e = 180 - e
            line(pano, clip(mix(bottom, top, min(1.0, e / 75.0) ** power)), (0, y), (pw, y))

        day = smooth(-6.0, 8.0, h)
        clear = 1 - 0.85 * smooth(0.3, 1.0, cloud)
        add = pygame.BLEND_RGB_ADD
        # the band of colour along the horizon on the sun's side at sunrise and sunset
        low = smooth(-9.0, -1.0, h) * (1 - smooth(3.0, 14.0, h)) * clear * (1 - haze)
        if low > 0.02:
            self._wrap(pano, _glow(150 * ppd, 44 * ppd, [c * low for c in (150, 70, 14)]), self._x(sky.sun_az), horizon, add)
            self._wrap(pano, _glow(70 * ppd, 20 * ppd, [c * low for c in (110, 60, 10)]), self._x(sky.sun_az), horizon, add)

        # stars
        dark = smooth(-5.0, -14.0, h) * (1 - smooth(0.2, 0.9, cloud)) * (1 - haze)
        if dark > 0.03:
            for _ in range(int(520 * self.pw * self.ph / 1.6e6)):
                x, e = rng.randrange(pw), rng.uniform(1.0, TOP - 1)
                v = (50 + 205 * rng.random() ** 5) * dark * smooth(0.0, 14.0, e if e < 90 else 180 - e)
                tint = rng.choice(((1.0, 1.0, 1.0), (1.0, 0.95, 0.86), (0.86, 0.92, 1.0)))
                y = int(self._y(e))
                base = pano.get_at((x, y))
                pano.set_at((x, y), clip((base[0] + v * tint[0], base[1] + v * tint[1], base[2] + v * tint[2])))

        # the moon, in its phase
        if sky.moon_elev > -1.5 and h < 8 and cloud < 0.9:
            r = max(4, int(1.1 * ppd))
            pale = smooth(-6.0, 6.0, h)                              # washed out in daylight
            face = mix((236, 236, 222), mix(bottom, (255, 255, 255), 0.55), pale)
            shadow = mix(mix(top, bottom, 0.3), (0, 0, 0), 0.25 * (1 - pale))
            disc = pygame.Surface((r * 8, r * 8), pygame.SRCALPHA, 32)
            c = r * 4
            pygame.draw.circle(disc, (*clip(shadow), 255), (c, c), c)
            lit_right = bool(sky.moon_waxing)
            k = max(0.0, min(1.0, sky.moon_lit))
            half = pygame.Rect(c if lit_right else 0, 0, c, c * 2)
            disc.set_clip(half)
            pygame.draw.circle(disc, (*clip(face), 255), (c, c), c)
            disc.set_clip(None)
            bulge = int(abs(1 - 2 * k) * c)                          # the terminator is half an ellipse
            if bulge:
                pygame.draw.ellipse(disc, (*clip(face if k > 0.5 else shadow), 255), (c - bulge, 0, bulge * 2, c * 2))
            mask = pygame.Surface((r * 8, r * 8), pygame.SRCALPHA, 32)
            mask.fill((255, 255, 255, 0))
            pygame.draw.circle(mask, (255, 255, 255, 255), (c, c), c)
            disc.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            mx, my = self._x(sky.moon_az), self._y(sky.moon_elev)
            if pale < 0.7:
                g = 26 * k * (1 - pale) * clear
                self._wrap(pano, _glow(18 * ppd, 18 * ppd, (g, g, g * 1.2)), mx, my, add)
            self._wrap(pano, pygame.transform.smoothscale(disc, (r * 2, r * 2)), mx, my)

        # the sun and the brightness around it
        if h > -1.5 and clear > 0.2:
            sx, sy = self._x(sky.sun_az), self._y(h)
            warm = 1 - smooth(2.0, 14.0, h)
            col = mix((255, 250, 236), (255, 170, 90), warm)
            self._wrap(pano, _glow(60 * ppd, 60 * ppd, [c * 0.32 * clear for c in col]), sx, sy, add)
            self._wrap(pano, _glow(16 * ppd, 16 * ppd, [c * 0.7 * clear for c in col]), sx, sy, add)
            if clear > 0.45:
                pygame.draw.circle(pano, clip(mix(col, (255, 255, 255), 0.5 * (1 - warm))), (int(sx), int(sy)), max(3, int(0.9 * ppd)))
                if sx < 20 or sx > pw - 20:
                    pygame.draw.circle(pano, clip(col), (int(sx + (pw if sx < 20 else -pw)), int(sy)), max(3, int(0.9 * ppd)))

        # clouds: lit from above by day, from underneath by a sun at or just below the horizon,
        # dark shapes at night
        lowc = smooth(-7.0, -0.5, h) * (1 - smooth(4.0, 14.0, h))
        lit = mix(mix((34, 38, 52), (250, 250, 252), day), (255, 176, 136), lowc * 0.85)
        shade = mix(mix((14, 16, 24), (168, 176, 192), day), (132, 94, 116), lowc * 0.75)
        if cloud > 0.5:                                              # under a full deck everything is flatter and greyer
            flat = sum(bottom) / 3
            g = smooth(0.5, 1.0, cloud)
            lit, shade = mix(lit, (flat * 1.05,) * 3, g), mix(shade, (flat * 0.80,) * 3, g)
        if known:
            self._clouds(pano, sky, horizon, lit, shade, lowc, day, bottom)

        g = self.ground
        pictures = g.pictures() if g is not None and g._ready else None
        if pictures:
            self._photo_ground(pano, sky, pictures, bottom, vis)
            self._painted_credit = pictures[2]
        else:
            self._ground(pano, sky, rng, bottom, horizon, day, haze)
            self._painted_credit = ""

        # A little grain, so the gradients don't band on a 16-bit screen.
        grain = [pygame.Surface((64, 64), 0, 32) for _ in range(2)]
        for g in grain:
            for y in range(64):
                for x in range(0, 64, 2):
                    v = rng.randrange(4)
                    g.fill((v, v, v), (x, y, 2, 1))
        for y in range(0, ph, 64):
            for x in range(0, pw, 64):
                pano.blit(grain[0], (x, y), special_flags=add)
                pano.blit(grain[1], (x, y), special_flags=pygame.BLEND_RGB_SUB)
        return pano

    def _clouds(self, pano, sky, horizon, lit, shade, lowc, day, bottom):
        """Lay the weather's cloud over the sky: the high layer, then the low one over it.

        How much of the sky each layer covers comes from the weather feed. The cover fraction
        picks a threshold in the cloud field; brightness above it becomes opacity, and the
        thickest parts are shaded toward the colour of a cloud's base. Everything after the
        field itself is byte-table lookups and one blit per layer."""
        pw, rows = self.pw, horizon + 1
        low, high, low_seen, high_seen = cloud_field(pw, rows, self.ppd)
        n = pw * rows
        tint = None
        if lowc > 0.05:                                              # at sunrise and sunset the sun's side is warmer
            row = pygame.Surface((360, 1), 0, 32)
            away = mix((255, 255, 255), (160, 160, 196), lowc)
            for a in range(360):
                t = ((1 + math.cos(math.radians(a + 0.5 - sky.sun_az))) / 2) ** 2
                row.set_at((a, 0), clip(mix(away, (255, 255, 255), t)))
            tint = pygame.transform.smoothscale(row, (pw, rows))
        band = int(10 * self.ppd)
        layers = ((high, high_seen, sky.cloud_high, 1.6, 0.38 + 0.12 * day, 0.25),
                  (low, low_seen, max(sky.cloud_low, sky.cloud_mid), 0.5, 0.96, 0.55))
        for field, seen, cover, soft_k, most, depth in layers:
            if cover < 0.03 or not seen:
                continue
            n_seen = len(seen)
            q = seen[min(n_seen - 1, int((1 - cover) * n_seen))]
            spread = seen[int(n_seen * 0.84)] - seen[int(n_seen * 0.16)]
            soft = max(4.0, soft_k * spread / 2)
            q -= 2 * soft * smooth(0.8, 1.0, cover)                  # overcast: no gaps
            # Shading starts inside the soft edge and deepens toward the thickest cloud there is,
            # so a cloud is lightest at its edge without a bright rim around a grey middle.
            thick0 = q + soft
            thick1 = max(thick0 + 2 * soft, seen[-1 - n_seen // 200])
            at, rt, gt, bt = bytearray(256), bytearray(256), bytearray(256), bytearray(256)
            for v in range(256):
                a = smooth(q, q + soft, v)
                col = clip(mix(lit, shade, depth * smooth(thick0, thick1, v)))
                at[v], rt[v], gt[v], bt[v] = int(255 * most * a), col[0], col[1], col[2]
            buf = bytearray(4 * n)
            buf[0::4], buf[1::4], buf[2::4], buf[3::4] = (field.translate(t) for t in (rt, gt, bt, at))
            layer = pygame.image.frombytes(bytes(buf), (pw, rows), "RGBA")
            if tint is not None:
                layer.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
            # distant cloud fades into the haze along the horizon, like distant ground does
            edge = clip(bottom)
            for i in range(band):
                t = 0.7 * (i / band) ** 1.6
                r = (0, rows - band + i, pw, 1)
                layer.fill(clip((255 * (1 - t),) * 3), r, special_flags=pygame.BLEND_RGB_MULT)
                layer.fill(clip([c * t for c in edge]), r, special_flags=pygame.BLEND_RGB_ADD)
            pano.blit(layer, (0, 0))


    @staticmethod
    def _ground_light(sky):
        """(brightness, colour cast) of daylight on the ground."""
        h = sky.sun_elev
        cloud = sky.cloud_total if sky.known else 0.0
        lowc = smooth(-4.0, 2.0, h) * (1 - smooth(4.0, 16.0, h))
        day = smooth(-6.0, 8.0, h)
        level = (0.05 + 0.95 * smooth(-7.0, 14.0, h)) * (1 - 0.3 * smooth(0.4, 1.0, cloud))
        cast = mix((1.0, 1.0, 1.0), (1.12, 0.9, 0.74), lowc)
        if day < 0.5:
            cast = mix(cast, (0.7, 0.82, 1.2), 1 - 2 * day)
        return level, cast

    def _photo_ground(self, pano, sky, pictures, sky_bottom, vis):
        """The real ground from ground.py: the photographs lit for the hour, the lights after
        dark, then haze thickening with distance. All pygame fills and blits, no pixel loops."""
        photo, lamps, _, y0 = pictures
        pw, ph, ppd = self.pw, self.ph, self.ppd
        n = photo.get_height()
        level, cast = self._ground_light(sky)
        strip = photo.copy()
        if not sky.growing:
            # out of season: the summer photographs half faded to straw and bare earth
            dull = pygame.transform.grayscale(strip)
            dull.fill((128, 118, 100), special_flags=pygame.BLEND_RGB_MULT)
            strip.fill((128, 128, 128), special_flags=pygame.BLEND_RGB_MULT)
            strip.blit(dull, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        strip.fill(clip([255 * level * c for c in cast]), special_flags=pygame.BLEND_RGB_MULT)
        lit = smooth(3.0, -7.0, sky.sun_elev)
        if lit > 0.02:
            glow = lamps.copy()
            if lit < 0.99:
                glow.fill(clip((255 * lit,) * 3), special_flags=pygame.BLEND_RGB_MULT)
            strip.blit(glow, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        # Haze: each row is one distance, so the veil is one colour and one strength per row.
        # Visibility is where contrast drops to 2%; a little less than that reads better from above.
        far = mix(sky_bottom, [c * level for c in (104, 112, 108)], 0.3)
        veil = pygame.Surface((pw, n), pygame.SRCALPHA, 32)
        reach = max(800.0, vis)
        for r in range(n):
            d = distance((y0 + r + 0.5) / ppd - TOP)
            veil.fill((*clip(far), int(255 * (1 - math.exp(-2.0 * d / reach)))), (0, r, pw, 1))
        strip.blit(veil, (0, 0))
        pano.blit(strip, (0, y0))
        if y0 + n < ph:                                    # straight down, past the pictures: their colour
            pano.fill(pygame.transform.average_color(strip, (0, n - 6, pw, 6))[:3], (0, y0 + n, pw, ph - y0 - n))

    def _ground(self, pano, sky, rng, sky_bottom, horizon, day, haze):
        """Fields in perspective, as from a few thousand feet, fading into the haze at the horizon."""
        ppd, pw, ph = self.ppd, self.pw, self.ph
        h = sky.sun_elev
        cloud = sky.cloud_total if sky.known else 0.0
        lowc = smooth(-4.0, 2.0, h) * (1 - smooth(4.0, 16.0, h))
        level = (0.05 + 0.95 * smooth(-7.0, 14.0, h)) * (1 - 0.3 * smooth(0.4, 1.0, cloud))
        cast = mix((1.0, 1.0, 1.0), (1.12, 0.9, 0.74), lowc)
        if day < 0.5:
            cast = mix(cast, (0.7, 0.82, 1.2), 1 - 2 * day)
        fields = FIELDS_GREEN if sky.growing else FIELDS_BROWN

        def light(c):
            return (c[0] * level * cast[0], c[1] * level * cast[1], c[2] * level * cast[2])

        far = mix(sky_bottom, light((104, 112, 108)), 0.3)          # what the ground fades to at the horizon
        mean = light(fields[1])
        near_d, far_d = math.log(1.2), math.log(FAR * (1 - 0.7 * haze))

        def faded(d):
            return smooth(near_d, far_d, math.log(d))

        line = pygame.draw.line
        for y in range(horizon + 1, ph):
            dep = y / ppd - TOP                                      # degrees below the horizon
            d = 1 / math.tan(math.radians(min(89.9, max(0.05, dep))))
            line(pano, clip(mix(mean, far, faded(d))), (0, y), (pw, y))

        poly = pygame.draw.polygon
        lights = smooth(3.0, -7.0, h)
        lamps = []
        # Big fields far away first, then the near ones over them at full detail.
        for cell, inner, outer in ((CELL * 5, REACH - CELL * 5, FAR), (CELL, 0.34, REACH)):
            n = int(outer / cell) + 1
            corner = {}
            for i in range(-n, n):
                for j in range(-n, n):
                    cx, cy = (i + 0.5) * cell, (j + 0.5) * cell
                    d = math.hypot(cx, cy)
                    if d < inner or d > outer:
                        continue
                    code = (i * 73856093) ^ (j * 19349663)
                    town = ((i // 2) * 83492791 ^ (j // 2) * 2971215073) % 9 == 0
                    mid = math.degrees(math.atan2(cx, cy))
                    pts = []
                    for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                        at = corner.get((a, b))
                        if at is None:
                            x, y = a * cell, b * cell
                            at = corner[(a, b)] = (math.degrees(math.atan2(x, y)),
                                                   (TOP + math.degrees(math.atan2(1.0, math.hypot(x, y)))) * ppd)
                        pts.append(((mid + (at[0] - mid + 180) % 360 - 180) * ppd, at[1]))
                    fade = faded(d)
                    paint = fields[code % len(fields)]
                    col = clip(mix(light(mix(paint, TOWN, 0.45) if town else paint), far, fade))
                    lo, hi = min(q[0] for q in pts), max(q[0] for q in pts)
                    for shift in ((0,) + ((pw,) if lo < 0 else ()) + ((-pw,) if hi > pw else ())):
                        moved = [(x + shift, y) for x, y in pts]
                        poly(pano, col, moved)
                        if d < 2.6:                                  # hedges and roads between the near fields
                            poly(pano, clip(mix(col, (0, 0, 0), 0.22)), moved, 1)
                    if lights > 0.02 and (town or code % 13 == 0):
                        for _ in range(rng.randint(6, 12) if town else 1):
                            lamps.append((cx + rng.uniform(-0.5, 0.5) * cell, cy + rng.uniform(-0.5, 0.5) * cell, fade))
        for x, y, fade in lamps:
            r = math.hypot(x, y)
            px = int(self._x(math.degrees(math.atan2(x, y))))
            py = int((TOP + math.degrees(math.atan2(1.0, r))) * ppd)
            glow = lights * (1 - 0.7 * fade) * rng.uniform(0.55, 1.0)
            col = clip(mix((0, 0, 0), rng.choice(((255, 196, 120), (255, 214, 150), (236, 226, 210))), glow))
            if r < 1.6:
                pano.fill(col, (px, py, 2, 2))
            elif 0 <= px < pw and 0 <= py < ph:
                pano.set_at((px, py), col)
