"""The real ground: which tiles, hostile images, and that north is north: python tests/test_ground.py

No network: tiles come from a stand-in fetcher that makes them up.
"""
import io
import math
import os
import struct
import sys
import tempfile
import urllib.error

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead import feeds, ground, scene  # noqa: E402
from overhead.sky import Sky  # noqa: E402

pygame.display.init()
pygame.display.set_mode((1, 1))
LAT, LON = 40.0, -100.0
PPD, PW = 4.5, 1620


def png(w, h, colour=(90, 120, 70)):
    s = pygame.Surface((w, h), 0, 32)
    s.fill(colour)
    buf = io.BytesIO()
    pygame.image.save(s, buf, "x.png")
    return buf.getvalue()


def jpeg(w, h):
    s = pygame.Surface((w, h), 0, 32)
    s.fill((100, 100, 100))
    buf = io.BytesIO()
    pygame.image.save(s, buf, "x.jpg")
    return buf.getvalue()


# ---- image headers: read before any decoder sees the bytes
assert feeds.image_header(png(256, 256)) == ("png", 256, 256)
assert feeds.image_header(jpeg(256, 256)) == ("jpeg", 256, 256)
assert feeds.image_header(jpeg(300, 200)) == ("jpeg", 300, 200)
huge = feeds.PNG_MAGIC + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 60000, 60000) + b"\x08\x02\x00\x00\x00" + b"\x00" * 20
assert feeds.image_header(huge) == ("png", 60000, 60000)
for bad in (b"", b"GIF89a" + b"\x00" * 40, b"<html>404 not found</html>" + b" " * 20, None, "text", 12,
            feeds.JPEG_MAGIC + b"\xe0\x00\x01" + b"\x00" * 30,           # a segment length under 2
            feeds.JPEG_MAGIC + b"\xe0\xff\xff" + b"\x00" * 30,           # a segment running past the end
            feeds.JPEG_MAGIC + b"\xda\x00\x08" + b"\x00" * 30,           # scan data before any frame header
            jpeg(256, 256)[:40],                                        # cut off before the frame header
            feeds.PNG_MAGIC + b"\x00" * 30):                            # no IHDR
    assert feeds.image_header(bad) is None, bad[:12] if isinstance(bad, bytes) else bad
# hostile tiles are refused before decoding: wrong size, too many bytes, lies about the size, not an image
assert ground.read_tile(png(256, 256)).get_size() == (256, 256)
assert ground.read_tile(jpeg(256, 256)).get_size() == (256, 256)
for bad in (png(512, 512), png(256, 255), huge, b"\x00" * 1000, png(256, 256) + b"\x00" * ground.MAX_TILE, None,
            feeds.PNG_MAGIC + png(256, 256)[8:40]):                     # right header, body cut off
    assert ground.read_tile(bad) is None

# ---- which tiles: decided by the rounded location only, never narrower than 1.2 km
photos, lamps = ground.plan(LAT, LON, PPD, PW, scene.TOP)
assert ground.plan(LAT + 0.004, LON - 0.0049, PPD, PW, scene.TOP) == (photos, lamps), "same 0.01 degree cell, same requests"
assert ground.plan(LAT + 0.01, LON, PPD, PW, scene.TOP) != (photos, lamps)
assert 40 < len(photos) < 160 and 1 <= len(lamps) <= 4, (len(photos), len(lamps))
for z, x, y in photos + lamps:
    assert ground.TILE * ground.metres_per_px(LAT, z) >= ground.NARROWEST_M, z
for lat in (0.0, 40.0, 60.0, 70.0):
    assert ground.TILE * ground.metres_per_px(lat, ground.max_zoom(lat)) >= ground.NARROWEST_M, lat
assert ground.in_us(40.0, -100.0) and ground.in_us(61.2, -149.9) and ground.in_us(21.3, -157.9)
assert not ground.in_us(51.5, -0.1) and not ground.in_us(-33.9, 151.2)

# ---- distances: straight down is the eye height away, the horizon is about 80 km off from 500 m
assert abs(ground.distance(90.0) - 0.0) < 1e-6
assert abs(ground.distance(45.0) - ground.EYE_M) < 1.0
assert 75000 < ground.distance(0.0) < 85000
assert ground.distance(1.0) < ground.distance(0.5) < ground.distance(0.0)

# ---- geometry: a made-up map with red to the east, green to the north and a white disc
# 5 km across. Looking east must show red, north green, and the disc's edge must land on the
# row whose distance is 5 km. Catches a mirrored or flipped map and a wrong scale.
z = 12
cx, cy = ground.world_px(LAT, LON, z)
w = h = 1024
left, top = int(cx) - w // 2, int(cy) - h // 2
m = ground.metres_per_px(LAT, z)
pic = pygame.Surface((w, h), 0, 32)
pic.fill((0, 0, 0))
pic.fill((200, 0, 0), (int(cx - left) + 1, 0, w, h))                      # east half red
north = pygame.Surface((w, int(cy - top)), 0, 32)
north.fill((0, 120, 0))
pic.blit(north, (0, 0), special_flags=pygame.BLEND_RGB_ADD)               # north half gets green
pygame.draw.circle(pic, (255, 255, 255), (round(cx - left), round(cy - top)), round(5000 / m))
y0, deps = ground.rows(PPD, scene.TOP)
day, _ = ground.warp(LAT, LON, deps, PPD, PW, {z: (pygame.image.tobytes(pic, "RGB"), w, h, left, top)}, None)


def px(row, az):
    i = (row * PW + int(az * PPD)) * 3
    return day[i], day[i + 1], day[i + 2]


far = next(r for r, d in enumerate(deps) if ground.distance(d) < 15000)  # a row well outside the disc
east, west, northward, south = px(far, 70.0), px(far, 250.0), px(far, 20.0), px(far, 160.0)
assert east[0] > 150 and west[0] < 50, (east, west)
assert northward[1] > 80 and south[1] < 40, (northward, south)
edge = next(r for r, d in enumerate(deps) if px(r, 45.0) == (255, 255, 255))
assert abs(ground.distance(deps[edge]) - 5000) < 5000 * 0.08, ground.distance(deps[edge])

# ---- building from a fetcher that makes tiles up; then load, and the scene uses them
calls = []


def fake(url, limit=0):
    calls.append(url)
    if "gibs" in url:
        return png(256, 256, (14, 14, 40))                             # dark country: no lights
    if "/14/" in url and len(calls) % 9 == 0:
        raise urllib.error.HTTPError(url, 404, "no tile", {}, None)     # a gap: filled from the other source
    return jpeg(256, 256)


with tempfile.TemporaryDirectory() as cache:
    assert ground.build(LAT, LON, PPD, PW, scene.TOP, cache, fetch=fake, log=lambda m: None)
    first = len(calls)
    assert first >= len(photos) + len(lamps)
    assert all("nationalmap.gov" in u or "eox.at" in u or "nasa.gov" in u for u in calls)
    assert any("eox.at" in u for u in calls), "a USGS gap is filled from EOX"
    pics = ground.load(cache, LAT, LON, PPD, PW, scene.TOP)
    assert pics and pics[0].get_size() == (PW, len(deps)) and pics[3] == y0, pics
    assert "USGS" in pics[2] and "EOX" in pics[2]
    # a second build reads the tiles kept on disk and fetches nothing
    assert ground.build(LAT, LON, PPD, PW, scene.TOP, cache, fetch=fake, log=lambda m: None) and len(calls) == first
    # a cached tile that has gone bad is fetched again
    some = os.path.join(cache, "tiles", "lights", sorted(os.listdir(os.path.join(cache, "tiles", "lights")))[0])
    with open(some, "wb") as f:
        f.write(b"garbage")
    assert ground.build(LAT, LON, PPD, PW, scene.TOP, cache, fetch=fake, log=lambda m: None) and len(calls) == first + 1
    # a picture file that doesn't match its description is refused
    day_path = ground.names(cache, LAT, LON, PPD, PW, scene.TOP)[0]
    with open(day_path, "wb") as f:
        f.write(png(PW, 10))
    assert ground.load(cache, LAT, LON, PPD, PW, scene.TOP) is None
    assert ground.build(LAT, LON, PPD, PW, scene.TOP, cache, fetch=fake, log=lambda m: None)

    # offline with an empty cache: no picture, and the build says so
    with tempfile.TemporaryDirectory() as empty:
        def down(url, limit=0):
            raise OSError("no network")
        try:
            ok = ground.build(LAT, LON, PPD, PW, scene.TOP, empty, fetch=down, log=lambda m: None)
        except OSError:
            ok = False
        assert not ok and ground.load(empty, LAT, LON, PPD, PW, scene.TOP) is None

    # the scene: drawn fields until the pictures are there, then the pictures, and a repaint key that changes
    W, H = 464, 216
    sc = scene.Scene(W, H)
    assert (sc.ppd, sc.pw) == (PPD, PW)
    sky = Sky(sun_elev=40.0, sun_az=150.0, known=True, vis_m=30000.0)
    g = ground.Ground(LAT, LON, cache, sc.ppd, sc.pw, scene.TOP, online=False)
    before = sc._key(sky)
    sc.use_ground(g)
    assert g.ready() and sc._key(sky) != before
    sc.pano = sc.paint(sky)
    assert sc._painted_credit == pics[2]
    win = pygame.Surface((W, H), 0, 32)
    sc.window(win, 90.0, -10.0)
    c = win.get_at((W // 2, H // 2))
    assert abs(c[0] - c[1]) < 25 and abs(c[1] - c[2]) < 25, "made-up grey tiles, so grey ground: %s" % (c,)
    night = Sky(sun_elev=-30.0, sun_az=0.0, known=True, vis_m=30000.0)
    sc.pano = sc.paint(night)
    sc.window(win, 90.0, -10.0)
    dark = win.get_at((W // 2, H // 2))
    assert sum(dark[:3]) < sum(c[:3]) // 3, (dark, c)

print("ok")
