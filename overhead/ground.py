"""The real ground under the plane view's horizon: aerial photographs by day, lights at night.

Once a location is set, the ground below the horizon is the country around it as it looks
from 500 m up. Map tiles are bent into the same azimuth-and-elevation panorama that the
sky is painted in, so a river or a town that lies west of you is in the west of the picture.
Free, keyless, public sources:

- USGS National Map aerial imagery (public domain) inside the United States.
- EOxCloudless 2017, a cloud-free Sentinel-2 mosaic by EOX (CC BY 4.0), everywhere else and
  for any gap in the USGS coverage near a border.
- NASA Black Marble 2016 (public domain) for where the lights are at night. Its pixels are
  half a kilometre wide, so the lights are drawn where the photograph shows roofs and roads
  inside the lit areas, which puts them along the streets.

Cost:
- About a hundred tiles, 1 to 3 MB in all, fetched once per location and kept in
  data/ground/tiles. Which tiles are fetched depends only on the location rounded to 0.01
  degree, the same as every other request the app makes, and no tile is narrower than
  1.2 km, so the set says no more about where you are than the rounding does.
- Bending them into the panorama is a Python loop over 260,000 pixels. It runs once, in its
  own process at the lowest CPU priority (python -m overhead.ground), so the board keeps
  its frame rate while it works. The result is two small pictures in data/ground. After
  that each repaint of the sky only tints them for the time of day and the weather, with
  pygame's blend fills.

Where nothing is set (the demo, tests, screenshots) the plane view keeps the drawn fields
in scene.py and nothing here runs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import random
import subprocess
import sys
import time
import urllib.error

from . import feeds

EYE_M = 500.0             # the ground is drawn as seen from this height
DEEP = 36.0               # degrees below the horizon covered by photographs; below that, a plain colour
REACH_M = 72000.0         # the photographs reach this far; the first row below the horizon is at about 68 km
LIGHTS_REACH_M = 50000.0
EARTH_R = 6371000.0
TILE = 256
MAX_TILE = 300_000        # bytes; the tiles are 4 to 70 kB
NARROWEST_M = 1200.0      # no tile narrower than this
MIN_ZOOM, LIGHTS_ZOOM = 5, 8
SLACK_M = 900.0           # the real location is up to 790 m from the rounded one the tiles are chosen around
VERSION = 1               # bump when the warp changes, so the pictures kept on disk are made again
GAP = (96, 100, 86)       # where a tile is missing

SOURCES = {
    "usgs": ("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}", "jpg",
             "IMAGERY USGS"),
    "eox": ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2017_3857/default/g/{z}/{y}/{x}.jpg", "jpg",
            "S2 CLOUDLESS 2017 BY EOX, COPERNICUS DATA"),
    "lights": ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/"
               "GoogleMapsCompatible_Level8/{z}/{y}/{x}.png", "png", "LIGHTS NASA"),
}
# Boxes around the US states and Puerto Rico, where the USGS imagery is. Lat, lat, lon, lon.
US = [(24.3, 49.5, -125.0, -66.8), (51.0, 71.6, -180.0, -129.0), (18.8, 22.4, -160.5, -154.6), (17.8, 18.6, -67.4, -65.2)]


# --------------------------------------------------------------- geometry ---
def rounded(lat, lon):
    return round(lat, 2), round(lon, 2)


def in_us(lat, lon):
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in US)


def metres_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / 2 ** z


def world_px(lat, lon, z):
    """Web Mercator pixel coordinates of a point at zoom z: x east, y south."""
    n = TILE * 2 ** z
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    return (lon + 180.0) / 360.0 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def max_zoom(lat):
    """The finest zoom whose tiles are still at least NARROWEST_M across here."""
    return max(MIN_ZOOM, min(15, int(math.log2(TILE * metres_per_px(lat, 0) / NARROWEST_M))))


def distance(dep, eye=EYE_M):
    """Ground distance in metres to where a line of sight this many degrees below the
    horizon meets the curved earth, from eye metres up."""
    t = math.tan(math.radians(dep))
    a = 2 * eye / EARTH_R
    return EARTH_R * a / (math.sqrt(t * t + a) + t)


def rows(ppd, top):
    """(first panorama row, depression of the middle of each row) for the rows the
    photographs cover. Matches Scene: row y is (y / ppd - top) degrees below the horizon."""
    y0 = int(top * ppd) + 1
    deps = []
    while True:
        dep = (y0 + len(deps) + 0.5) / ppd - top
        if dep > DEEP:
            return y0, deps
        deps.append(dep)


def zoom_for(d, lat, pw, zmax):
    """The zoom whose pixels are about as wide as the gap between two columns at distance d."""
    spacing = 2 * math.pi * max(d, 1.0) / pw
    z = int(round(math.log2(metres_per_px(lat, 0) / spacing)))
    return max(MIN_ZOOM, min(zmax, z))


def _tiles_around(lat, lon, z, inner, outer):
    """Tiles at zoom z that touch the ring between inner and outer metres from (lat, lon)."""
    cx, cy = world_px(lat, lon, z)
    m = metres_per_px(lat, z)
    lo, hi = max(0.0, inner - SLACK_M) / m, (outer + SLACK_M) / m
    out = []
    for tx in range(int((cx - hi) // TILE), int((cx + hi) // TILE) + 1):
        for ty in range(max(0, int((cy - hi) // TILE)), min(2 ** z - 1, int((cy + hi) // TILE)) + 1):
            x0, x1, y0, y1 = tx * TILE - cx, (tx + 1) * TILE - cx, ty * TILE - cy, (ty + 1) * TILE - cy
            nx = 0.0 if x0 <= 0 <= x1 else min(abs(x0), abs(x1))
            ny = 0.0 if y0 <= 0 <= y1 else min(abs(y0), abs(y1))
            if math.hypot(nx, ny) <= hi and math.hypot(max(abs(x0), abs(x1)), max(abs(y0), abs(y1))) >= lo:
                out.append((z, tx, ty))
    return out


def plan(lat, lon, ppd, pw, top):
    """(photo tiles, light tiles) as (z, x, y). Worked out from the rounded location only."""
    la, lo = rounded(lat, lon)
    zmax = max_zoom(la)
    _, deps = rows(ppd, top)
    rings = {}
    for dep in deps:
        d = min(distance(dep), REACH_M)
        z = zoom_for(d, la, pw, zmax)
        near, far = min(d, distance(dep + 0.5 / ppd)), min(REACH_M, distance(max(0.0, dep - 0.5 / ppd)))
        r = rings.setdefault(z, [near, far])
        r[0], r[1] = min(r[0], near), max(r[1], far)
    photos = []
    for z, (inner, outer) in sorted(rings.items()):
        photos += _tiles_around(la, lo, z, inner, outer)
    lights = _tiles_around(la, lo, LIGHTS_ZOOM, 0.0, LIGHTS_REACH_M)
    return photos, lights


# ------------------------------------------------------------------ tiles ---
def read_tile(data):
    """Tile bytes (downloaded, or read back from the cache) -> a 256 x 256 surface, or None.
    The header is checked before the decoder sees anything."""
    import pygame
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_TILE:
        return None
    head = feeds.image_header(data)
    if not head or head[1:] != (TILE, TILE):
        return None
    try:
        img = pygame.image.load(io.BytesIO(data), "tile." + ("png" if head[0] == "png" else "jpg"))
    except Exception:
        return None
    if img.get_size() != (TILE, TILE):
        return None
    out = pygame.Surface((TILE, TILE), 0, 32)
    out.blit(img, (0, 0))
    return out


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def get_tile(cache, src, z, x, y, online=True, fetch=feeds.http_bytes):
    """A tile from the cache, else from the network (then cached). None where the source has
    no picture. Network trouble other than 'no such tile' raises, so a build gives up and
    is tried again later instead of keeping a half-empty ground."""
    url, ext, _ = SOURCES[src]
    x %= 2 ** z
    path = os.path.join(cache, "tiles", src, "%d-%d-%d.%s" % (z, x, y, ext))
    try:
        if os.path.getsize(path) <= MAX_TILE:
            with open(path, "rb") as f:
                img = read_tile(f.read())
            if img is not None:
                return img
    except OSError:
        pass
    if not online or os.path.exists(path + ".none"):           # the source said it has no such tile
        return None
    try:
        data = fetch(url.format(z=z, x=x, y=y), limit=MAX_TILE)
    except urllib.error.HTTPError as e:
        if e.code in (404, 204):
            _write(path + ".none", b"")
            return None
        raise
    img = read_tile(data)
    if img is not None:
        _write(path, data)
    if fetch is feeds.http_bytes:
        time.sleep(0.05)                               # be gentle with free services
    return img


def mosaic(tiles, get):
    """Join tiles of one zoom into one picture. Returns (RGB bytes, width, height, world x
    and y of its top-left corner, how many tiles were missing, which sources were used)."""
    import pygame
    xs, ys = [t[1] for t in tiles], [t[2] for t in tiles]
    x0, y0 = min(xs), min(ys)
    w, h = (max(xs) - x0 + 1) * TILE, (max(ys) - y0 + 1) * TILE
    pic = pygame.Surface((w, h), 0, 32)
    pic.fill(GAP)
    missing, used = 0, set()
    for z, x, y in tiles:
        img, src = get(z, x, y)
        if img is None:
            missing += 1
        else:
            pic.blit(img, ((x - x0) * TILE, (y - y0) * TILE))
            used.add(src)
    return pygame.image.tobytes(pic, "RGB"), w, h, x0 * TILE, y0 * TILE, missing, used


# ------------------------------------------------------------------- warp ---
def warp(lat, lon, deps, ppd, pw, photos, lights, seed=7):
    """Bend the mosaics into panorama rows. photos: {zoom: (rgb, w, h, left, top)};
    lights: (grey, w, h, left, top) at LIGHTS_ZOOM or None. Returns (day rgb, lights rgb),
    each pw by len(deps) pixels.

    Each pixel looks along its azimuth to the ground at its row's distance and takes the
    colour there from the zoom whose pixels best match the gap between columns. Far rows
    cover far more ground from top to bottom than from side to side, so they average a few
    samples along the line of sight, which keeps distant country from turning to noise.
    """
    zooms = sorted(photos)
    zmax = zooms[-1] if zooms else MIN_ZOOM
    sin_t = [math.sin(math.radians((c + 0.5) / ppd)) for c in range(pw)]
    cos_t = [math.cos(math.radians((c + 0.5) / ppd)) for c in range(pw)]
    day = bytearray(pw * len(deps) * 3)
    glow = bytearray(pw * len(deps) * 3)
    rnd = random.Random(seed).random
    gr, gg, gb = GAP
    if lights:
        lgrey, lw, lh, lleft, ltop = lights
        lcx, lcy = world_px(lat, lon, LIGHTS_ZOOM)
        lcx, lcy = lcx - lleft, lcy - ltop
        lm = metres_per_px(lat, LIGHTS_ZOOM)
    for r, dep in enumerate(deps):
        d = min(distance(dep), REACH_M)
        z = min(zooms, key=lambda q: abs(q - zoom_for(d, lat, pw, zmax))) if zooms else None
        if z is None:
            continue
        rgb, w, h, left, top = photos[z]
        cx, cy = world_px(lat, lon, z)
        cx, cy = cx - left, cy - top
        m = metres_per_px(lat, z)
        k = 1 if d < 3 * EYE_M else 2 if d < 8 * EYE_M else 3
        reach = [min(distance(dep + (i + 0.5) / k / ppd - 0.5 / ppd), REACH_M) / m for i in range(k)]
        base = r * pw * 3
        lamp = lights and d < LIGHTS_REACH_M
        if lamp:
            ld = d / lm
        for c in range(pw):
            s, co = sin_t[c], cos_t[c]
            sr = sg = sb = 0
            for q in reach:
                x, y = int(cx + q * s), int(cy - q * co)
                if 0 <= x < w and 0 <= y < h:
                    i = (y * w + x) * 3
                    sr += rgb[i]
                    sg += rgb[i + 1]
                    sb += rgb[i + 2]
                else:
                    sr += gr
                    sg += gg
                    sb += gb
            o = base + c * 3
            if k > 1:
                sr //= k
                sg //= k
                sb //= k
            day[o], day[o + 1], day[o + 2] = sr, sg, sb
            if not lamp:
                continue
            # how much light the satellite saw here, interpolated between its big pixels
            fx, fy = lcx + ld * s - 0.5, lcy - ld * co - 0.5
            x, y = int(fx), int(fy)
            if not (0 <= x < lw - 1 and 0 <= y < lh - 1):
                continue
            ax, ay = fx - x, fy - y
            i = y * lw + x
            v = ((lgrey[i] * (1 - ax) + lgrey[i + 1] * ax) * (1 - ay)
                 + (lgrey[i + lw] * (1 - ax) + lgrey[i + lw + 1] * ax) * ay)
            if v < 40:
                continue
            # lights sit on grey things (roofs, roads, car parks), not on fields, trees or water
            hi_, lo_ = max(sr, sg, sb), min(sr, sg, sb)
            grey = 1.0 - min(1.0, 2.4 * (hi_ - lo_) / (hi_ + 1))
            built = min(1.0, max(0.0, (sr + sg + sb - 150) / 240.0)) * grey
            a = rnd()
            v = (v - 40) / 215.0 * (0.12 + 0.88 * built) * (0.35 + 0.65 * a) * (2.2 if a > 0.93 else 1.0)
            if v > 0.03:
                warm = rnd() < 0.6
                glow[o] = min(255, int(255 * v))
                glow[o + 1] = min(255, int((175 if warm else 232) * v))
                glow[o + 2] = min(255, int((90 if warm else 205) * v))
    return bytes(day), bytes(glow)


# ------------------------------------------------------------------ build ---
def names(cache, lat, lon, ppd, pw, top):
    key = hashlib.sha1(("%d %.5f %.5f %.1f %.4f %d %.2f" % (VERSION, lat, lon, EYE_M, ppd, pw, top)).encode()).hexdigest()[:12]
    base = os.path.join(cache, "view-" + key)
    return base + "-day.png", base + "-lights.png", base + "-meta.json"


def build(lat, lon, ppd, pw, top, cache, online=True, fetch=feeds.http_bytes, log=print):
    """Fetch what is missing, bend it into the panorama and write the pictures. True when done."""
    import pygame
    t0 = time.time()
    la, lo = rounded(lat, lon)
    photo_tiles, light_tiles = plan(lat, lon, ppd, pw, top)
    first = "usgs" if in_us(la, lo) else "eox"

    def photo(z, x, y):
        img = get_tile(cache, first, z, x, y, online, fetch)
        if img is None and first != "eox":
            return get_tile(cache, "eox", z, x, y, online, fetch), "eox"
        return img, first

    photos, used, missing = {}, set(), 0
    for z in sorted({t[0] for t in photo_tiles}):
        rgb, w, h, left, top_, miss, src = mosaic([t for t in photo_tiles if t[0] == z], photo)
        photos[z] = (rgb, w, h, left, top_)
        missing += miss
        used |= src
    if not used or missing > len(photo_tiles) // 4:
        log("ground: %d of %d tiles missing, not making the picture" % (missing, len(photo_tiles)))
        return False
    lights = None
    grey, w, h, left, top_, miss, src = mosaic(light_tiles, lambda z, x, y: (get_tile(cache, "lights", z, x, y, online, fetch), "lights"))
    if src:
        lights = (grey[0::3], w, h, left, top_)                          # the red channel is enough: the lights are white to yellow
        used |= src
    t1 = time.time()
    y0, deps = rows(ppd, top)
    day, glow = warp(lat, lon, deps, ppd, pw, photos, lights)
    day_path, lights_path, meta_path = names(cache, lat, lon, ppd, pw, top)
    for path, data in ((day_path, day), (lights_path, glow)):
        tmp = path[:-4] + ".tmp.png"
        pygame.image.save(pygame.image.frombytes(data, (pw, len(deps)), "RGB"), tmp)
        os.replace(tmp, path)
    credit = "   ".join(SOURCES[s][2] for s in ("usgs", "eox") if s in used)
    meta = {"version": VERSION, "rows": len(deps), "width": pw, "first_row": y0, "credit": credit}
    _write(meta_path, json.dumps(meta).encode())
    log("ground: %d photo tiles, %d light tiles, fetched and joined in %.1f s, bent in %.1f s"
        % (len(photo_tiles), len(light_tiles), t1 - t0, time.time() - t1))
    return True


def load(cache, lat, lon, ppd, pw, top):
    """The pictures made by build(), checked, as (day surface, lights surface, credit, first
    row), or None if they aren't there or don't fit this panorama."""
    import pygame
    day_path, lights_path, meta_path = names(cache, lat, lon, ppd, pw, top)
    try:
        if os.path.getsize(meta_path) > 4096:
            return None
        with open(meta_path, "rb") as f:
            meta = json.loads(f.read().decode("utf-8"))
        n, w, y0 = meta.get("rows"), meta.get("width"), meta.get("first_row")
        if meta.get("version") != VERSION or w != pw or not isinstance(n, int) or not isinstance(y0, int):
            return None
        credit = feeds._text(meta.get("credit"), 80)
        out = []
        for path in (day_path, lights_path):
            if os.path.getsize(path) > 8_000_000:
                return None
            with open(path, "rb") as f:
                data = f.read()
            head = feeds.image_header(data)
            if not head or head != ("png", pw, n):
                return None
            img = pygame.image.load(io.BytesIO(data), "ground.png")
            surf = pygame.Surface((pw, n), 0, 32)
            surf.blit(img, (0, 0))
            out.append(surf)
        return out[0], out[1], credit, y0
    except (OSError, ValueError, TypeError, AttributeError, pygame.error):
        return None


class Ground:
    """The app's handle on the real ground for one location. Starts the build process when
    the pictures aren't there yet, and hands them to the sky painter once they are."""
    RETRY_S = 900.0

    def __init__(self, lat, lon, cache, ppd, pw, top, online=True):
        self.args = (cache, lat, lon, ppd, pw, top)
        self.online = online
        self.proc = None
        self._ready = False
        self._next = 0.0
        self._pictures = None

    def ready(self):
        """True once the pictures exist. Cheap: looks at the disk at most every few seconds,
        and starts (or restarts, after a failure and a wait) the build process."""
        if self._ready:
            return True
        now = time.monotonic()
        if now < self._next:
            return False
        self._next = now + 4.0
        if all(os.path.exists(p) for p in names(*self.args)):
            self._ready = True
            return True
        if self.proc is not None and self.proc.poll() is not None:
            self.proc = None
            self._next = now + self.RETRY_S                  # it failed: offline, or a source is down
        elif self.proc is None and self.online:
            self.proc = self._spawn()
        return False

    def pictures(self):
        """(day, lights, credit, first row) or None. Called on the sky painter's thread."""
        if self._ready and self._pictures is None:
            self._pictures = load(*self.args)
            if self._pictures is None:
                self._ready = False                          # unreadable: make them again
                self._next = time.monotonic() + self.RETRY_S
                for p in names(*self.args):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        return self._pictures

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def _spawn(self):
        cache, lat, lon, ppd, pw, top = self.args
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [sys.executable, "-m", "overhead.ground", "--cache", cache, "--lat", repr(lat), "--lon", repr(lon),
               "--ppd", repr(ppd), "--width", str(pw), "--top", repr(top)]
        kw = {}
        if hasattr(os, "nice"):
            kw["preexec_fn"] = lambda: os.nice(19)          # only spare CPU: the board comes first
        elif sys.platform == "win32":
            kw["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", PYGAME_HIDE_SUPPORT_PROMPT="1")
        try:
            os.makedirs(cache, exist_ok=True)
            log = open(os.path.join(cache, "build.log"), "w")
            return subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, **kw)
        except OSError as e:
            print("ground: could not start the build:", e)
            self._next = time.monotonic() + self.RETRY_S
            return None


def main():
    ap = argparse.ArgumentParser(description="Make the plane view's ground pictures for one location")
    ap.add_argument("--cache", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--ppd", type=float, required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--top", type=float, required=True)
    ap.add_argument("--plan", action="store_true", help="only list how many tiles it would fetch")
    a = ap.parse_args()
    if a.plan:
        photos, lights = plan(a.lat, a.lon, a.ppd, a.width, a.top)
        zooms = sorted({t[0] for t in photos})
        print("photo tiles %d (%s), light tiles %d" % (len(photos), ", ".join(
            "z%d: %d" % (z, sum(1 for t in photos if t[0] == z)) for z in zooms), len(lights)))
        return
    sys.exit(0 if build(a.lat, a.lon, a.ppd, a.width, a.top, a.cache, log=lambda m: print(m, flush=True)) else 1)


if __name__ == "__main__":
    main()
