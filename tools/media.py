"""Regenerate the pictures in docs/images from the app itself, with simulated traffic.

    python tools/media.py

Run it after a visible change and commit the images with it. It needs no network and no
screen. The traffic is the built-in demo, so no real flight or location appears.
"""
import os
import struct
import sys
import time
import types
import zlib

os.environ["SDL_VIDEODRIVER"] = "dummy"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from overhead import feeds, ground, scene  # noqa: E402
from overhead import app as appmod  # noqa: E402
from overhead.tracker import Frame  # noqa: E402

OUT = os.path.join(ROOT, "docs", "images")
os.makedirs(OUT, exist_ok=True)
appmod.CONFIG = os.path.join(ROOT, "data", "media-config.json")      # never the real config
if os.path.exists(appmod.CONFIG):
    os.remove(appmod.CONFIG)

FPS = 60


def new_app():
    args = types.SimpleNamespace(demo=True, fullscreen=False, size="480x320", lat=40.0, lon=-100.0, screenshot="x",
                                 at="1", settings=None, fb=False, calibrate=False, stats=False, plane=False)
    a = appmod.App(args)
    a.cfg["range_mi"] = 40                # the demo's aircraft are spread over 40 miles
    a.start_engine()
    a.engine.step(a.sim)
    # The plane view's ground: aerial photographs of the made-up place, if they have been made
    # (python tools/sky_sheet.py out.png --ground). Never fetched from here: without them the
    # pictures show the drawn fields.
    sc = a.view.scene
    cache = os.path.join(ROOT, "data", "ground")
    a.ground = ground.Ground(40.0, -100.0, cache, sc.ppd, sc.pw, scene.TOP, online=False)
    if ground.load(cache, 40.0, -100.0, sc.ppd, sc.pw, scene.TOP) is None:
        print("no ground pictures in data/ground: the plane view shows drawn fields")
        a.ground = None
    sc.use_ground(a.ground)
    return a


def run(a, seconds, each=None):
    """Advance simulated time. `each` is called after every frame."""
    poll = 0.0
    for _ in range(int(seconds * FPS)):
        poll += 1 / FPS
        if poll >= a.engine.poll:
            poll = 0.0
            a.engine.step(a.sim)
        if a.settings.visible:
            if a.settings.update(1 / FPS):
                a.settings.draw(0.0)
        elif a.view.visible:
            a.tick_view(1 / FPS)
        else:
            a.step(1 / FPS)
        a.sim += 1 / FPS
        if each:
            each()


def save(a, name):
    pygame.image.save(a.scr, os.path.join(OUT, name))
    print("wrote docs/images/" + name)


def show(a, flight):
    """Put a given demo aircraft on the card and let the flaps settle."""
    for s in a.engine.latest().sightings:
        if s.plane.flight == flight:
            a.frame = a.engine.latest()
            a.cur, a.since, a.hold_until = s.hex, a.sim, a.sim + 600
            a._apply_at = 0
            run(a, 5)
            return s
    raise SystemExit("no demo aircraft called " + flight)


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def write_apng(path, frames, delay_ms, hold_ms=1800):
    """Animated PNG. Each frame after the first stores only the rectangle that changed."""
    w, h = frames[0].get_size()
    out = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)),
           chunk(b"acTL", struct.pack(">II", len(frames), 0))]
    seq, prev = 0, None
    for i, f in enumerate(frames):
        box = pygame.Rect(0, 0, w, h)
        if prev is not None:
            d = f.copy()
            d.blit(prev, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
            e = prev.copy()
            e.blit(f, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
            d.blit(e, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            d.set_colorkey((0, 0, 0))
            box = d.get_bounding_rect()
            if box.w == 0 or box.h == 0:
                box = pygame.Rect(0, 0, 1, 1)
        raw = pygame.image.tobytes(f.subsurface(box), "RGB")
        rows = b"".join(b"\x00" + raw[y * box.w * 3:(y + 1) * box.w * 3] for y in range(box.h))
        data = zlib.compress(rows, 9)
        wait = hold_ms if i == len(frames) - 1 else delay_ms            # pause on the last frame before it loops
        out.append(chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, box.w, box.h, box.x, box.y, wait, 1000, 0, 0)))
        seq += 1
        if i == 0:
            out.append(chunk(b"IDAT", data))
        else:
            out.append(chunk(b"fdAT", struct.pack(">I", seq) + data))
            seq += 1
        prev = f
    out.append(chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(b"".join(out))
    print("wrote docs/images/%s (%d frames, %d KB)" % (os.path.basename(path), len(frames), os.path.getsize(path) // 1024))


a = new_app()
run(a, 6)
show(a, "UAL328")
save(a, "card.png")

# the card changing from one aircraft to the next, as an animation
frames = []
target = next(s for s in a.engine.latest().sightings if s.plane.flight == "DAL1482")
a.cur, a.since, a.hold_until, a._apply_at = target.hex, a.sim, a.sim + 600, 0
step = [0]


def grab():
    step[0] += 1
    if step[0] % 5 == 0:                  # 12 frames a second
        frames.append(a.scr.copy())


frames.append(a.scr.copy())
run(a, 4.2, grab)
write_apng(os.path.join(OUT, "flip.png"), frames, 83)

show(a, "RCH871")
save(a, "military.png")

a.show_hints()
save(a, "tips.png")
a.close_hints()
run(a, 0.5)


def plane_view(name, flight="UAL328", tilt=0.0):
    show(a, flight)
    a.open_view()
    a.view.tilt = tilt                    # as if dragged: negative looks down
    a.view.scene.wait()                   # the sky is painted on a thread
    a.view.set(a.view.info)
    run(a, 1.5)
    save(a, name)
    a.view.close()
    run(a, 1.0)


plane_view("plane-view.png")
# The same view at sunset and at night. The lowest aircraft in the demo is used, and the
# demo sun is put behind it, so the picture has the horizon and the glow in it.
low = min((s for s in a.engine.latest().sightings if s.elev > 4), key=lambda s: s.elev)
for sky_name in ("dusk", "night"):
    feeds.DEMO_SKIES[sky_name] = dict(feeds.DEMO_SKIES[sky_name], sun_az=(low.az - 16) % 360)
    a.engine.demo_sky = sky_name
    run(a, 6)
    plane_view("plane-view-%s.png" % sky_name, low.plane.flight)
a.engine.demo_sky = "day"
run(a, 6)
# Looking down past the low aircraft, for the ground (aerial photographs, if data/ground has them)
plane_view("plane-view-ground.png", low.plane.flight, tilt=-(low.elev + 4.0))

for page in ("main", "show", "wifi", "key"):
    a.settings.open(a.scr.copy(), page)
    if page == "key":
        a.settings.join_net = types.SimpleNamespace(ssid="Workshop")
        a.settings.kb_text = "correct horse"
    run(a, 2.5)
    waited = time.time()
    while a.settings.wifi_busy and time.time() - waited < 6:      # the stand-in Wi-Fi scan takes real time
        a.settings.update(0.05)
        time.sleep(0.05)
    run(a, 0.2)
    save(a, "settings-%s.png" % page)
    a.settings.close()
    run(a, 1.0)
    a.after_settings()

a.engine.frame = Frame(sightings=[], sky=a.engine.latest().sky, stamp=a.sim, source="ADSB.LOL", online=True)
a.frame = None
run(a, 5)
save(a, "empty.png")
if os.path.exists(appmod.CONFIG):
    os.remove(appmod.CONFIG)
