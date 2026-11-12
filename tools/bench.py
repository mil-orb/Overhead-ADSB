"""How long the heavy paths take on this machine. Run on the Pi with the app stopped:

    sudo systemctl stop overhead && python3 tools/bench.py; sudo systemctl start overhead

It uses the same code as the app with a file standing in for the screen, so the numbers
are CPU cost only. What the SPI link adds on top is listed at the end.
"""
import os
import sys
import tempfile
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead import device  # noqa: E402
from overhead.board import Board  # noqa: E402
from overhead.feeds import DemoFeed, Routes  # noqa: E402
from overhead.sky import Observer, Sky  # noqa: E402
from overhead.tracker import Tracker  # noqa: E402

W, H = 480, 320
pygame.display.init()
pygame.font.init()
pygame.display.set_mode((W, H))


def ms(seconds):
    return "%7.2f ms" % (seconds * 1000)


shm = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
handle, path = tempfile.mkstemp(prefix="overhead-bench-", dir=shm)
with os.fdopen(handle, "wb") as f:
    f.write(bytes(W * 2 * H))
fb = device.FbOut(path, W, H, 16, W * 2)
screen = fb.surface()               # what the app draws on when it runs on the Pi

t0 = time.perf_counter()
board = Board(screen, {"warm": False})
print("build the board            %s   (%d modules, flap %dx%d)" % (ms(time.perf_counter() - t0), len(board.modules), board.U, board._ch(board.U)))
fb.push(screen)


def flip_everything(fps, label):
    """Every flap on the card moving at once, the way a change of aircraft looks."""
    for m in board.modules:
        m.set((m.tgt + m.fs.n // 2) % m.fs.n)
    draw = push = 0.0
    worst = 0.0
    n = lines = 0
    while board.active and n < 400:
        a = time.perf_counter()
        rects = board.update(1.0 / fps)
        b = time.perf_counter()
        fb.push(screen, rects)
        c = time.perf_counter()
        draw += b - a
        push += c - b
        worst = max(worst, c - a)
        lines += max(r.bottom for r in rects) - min(r.top for r in rects) if rects else 0
        n += 1
    per = (draw + push) / n
    print("%-26s %s draw + %s push = %s a frame, worst %s -> %4.1f frames/s possible, %d lines a frame"
          % (label, ms(draw / n), ms(push / n), ms(per), ms(worst), 1 / per, lines / n))
    return lines / n


flip_everything(30, "full card flip, first run")      # includes rendering each squashed flap once
band = flip_everything(30, "full card flip, cached")

# one distance digit ticking over, the common case
mods, _ = board.cells["dist"]
t = time.perf_counter()
n = 0
for k in range(20):
    mods[-1].set((mods[-1].tgt + 1) % mods[-1].fs.n)
    while board.active:
        fb.push(screen, board.update(1 / 30))
        n += 1
print("one digit ticking          %s a frame" % ms((time.perf_counter() - t) / n))

a = time.perf_counter()
for _ in range(20):
    fb.push(screen)
print("push a whole frame         %s" % ms((time.perf_counter() - a) / 20))

# tracker: 60 aircraft, as on a busy evening
obs = Observer(40.0, -100.0, 300.0, range_m=15 * 1609.344)
feed = DemoFeed(40.0, -100.0)
planes = []
for i in range(6):
    for p in feed.fetch(1000.0 + i * 37):
        p.hex = "%s%d" % (p.hex, i)
        planes.append(p)
tr = Tracker(obs, Routes(None, online=False), "view")
sky = Sky(known=True)
a = time.perf_counter()
for i in range(5):
    tr.compute(planes, sky, 1000.0 + i)
print("tracker, %d aircraft        %s per update (runs every 5 s on its own thread)" % (len(planes), ms((time.perf_counter() - a) / 5)))

# the plane view: the aircraft model, and the sky behind it
import math  # noqa: E402

from overhead import hangar, models  # noqa: E402
from overhead.scene import Scene, tone  # noqa: E402

lw, lh = 464, 216                                # the plane view's window on a 480 x 320 screen
a = time.perf_counter()
model, _ = models.model_for("B738")
normals = model.lit
print("load one model             %s   (%d triangles, read once per type)" % (ms(time.perf_counter() - a), len(model.faces)))
day = Sky(known=True, cloud_low=0.3, cloud_high=0.3, cloud_total=0.4, sun_elev=30.0, sun_az=200.0)
scene = Scene(lw, lh)
a = time.perf_counter()
scene.pano = scene.paint(day)
print("paint the sky panorama     %s   (%d x %d, on its own thread, when the light changes)" % (ms(time.perf_counter() - a), scene.pw, scene.ph))
win = pygame.Surface((lw, lh), 0, 32)
big = pygame.Surface((lw * 2, lh * 2), 0, 32)
a = time.perf_counter()
for i in range(20):
    scene.window(win, 37.0 * i, 20.0)
print("cut a window from the sky  %s" % ms((time.perf_counter() - a) / 20))
amb, direct, _, key = tone(day)
paint = hangar.livery("UAL1", False, model.kind)
shades = {}
scale = 0.46 * min(lw, lh * 1.55) / model.radius
pose = hangar.pose(78, 2, 0, 150, 60)
hangar.render(win, model, pose, paint, (0.3, 0.6, -0.7), lw // 2, lh // 2, scale, shades, (amb, direct, key))
a = time.perf_counter()
for i in range(5):
    scene.window(win, 150.0 + i, 60.0)
    hangar.render(win, model, hangar.pose(78, 2, 0, 150 + i, 60), paint, (0.3, 0.6, -0.7), lw // 2, lh // 2, scale, shades, (amb, direct, key))
quick = (time.perf_counter() - a) / 5
a = time.perf_counter()
for i in range(5):
    scene.window(win, 150.0 + i, 60.0)
    pygame.transform.scale(win, big.get_size(), big)
    hangar.render(big, model, hangar.pose(78, 2, 0, 150 + i, 60), paint, (0.3, 0.6, -0.7), lw, lh, scale * 2, shades, (amb, direct, key))
    pygame.transform.smoothscale(big, (lw, lh))
fine = (time.perf_counter() - a) / 5
print("plane view while dragging  %s   (%.1f a second)" % (ms(quick), 1 / quick))
print("plane view, smoothed       %s   (when the aircraft's attitude changes, twice a second at most)" % ms(fine))

fb.close()
os.remove(path)
print()
print("SPI at 16 MHz carries about 2080 lines a second. A full card flip repaints %d lines a frame," % band)
print("so the panel shows about %.1f of those frames a second however fast they are drawn." % (2080 / max(1, band)))
