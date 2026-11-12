"""Framebuffer output and touch input, checked against a fake framebuffer file and synthetic events.

Run: python tests/test_device.py
"""
import os
import struct
import sys
import tempfile

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead import device  # noqa: E402

pygame.display.init()
screen = pygame.display.set_mode((480, 320))


def rgb565(c):
    r, g, b = c
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


# ---- framebuffer: 16 bpp with a stride wider than the picture, as some drivers report ----
W, H, STRIDE = 480, 320, 480 * 2 + 16
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "fb")
with open(path, "wb") as f:
    f.write(b"\xAA" * (STRIDE * H))
fb = device.FbOut(path, W, H, 16, STRIDE)
screen.fill((10, 20, 30))
fb.push(screen, [pygame.Rect(0, 0, 4, 4)])                 # whatever is asked first, the whole frame goes out


def scribble():
    for y in range(H):
        fb.mm[y * STRIDE:(y + 1) * STRIDE] = b"\xAA" * STRIDE


assert struct.unpack("<H", fb.mm[(H - 1) * STRIDE + (W - 1) * 2:(H - 1) * STRIDE + W * 2])[0] == rgb565((10, 20, 30))
scribble()
red, green = (255, 0, 0), (0, 255, 0)
pygame.draw.rect(screen, red, (100, 50, 40, 62))
pygame.draw.rect(screen, green, (300, 200, 20, 30))
fb.push(screen, [pygame.Rect(100, 50, 40, 62), pygame.Rect(300, 200, 20, 30)])


def px(x, y):
    off = y * STRIDE + x * 2
    return struct.unpack("<H", fb.mm[off:off + 2])[0]


assert px(100, 50) == rgb565(red) and px(139, 111) == rgb565(red)
assert px(300, 200) == rgb565(green) and px(319, 229) == rgb565(green)
assert px(99, 50) == 0xAAAA and px(140, 50) == 0xAAAA, "pixels beside a pushed rectangle must be left alone"
assert px(100, 49) == 0xAAAA and px(100, 112) == 0xAAAA, "lines outside the band must be left alone"
assert fb.mm[50 * STRIDE + W * 2:50 * STRIDE + W * 2 + 16] == b"\xAA" * 16, "stride padding untouched"
# two rectangles on the same lines merge into one band covering the gap between them
pygame.draw.rect(screen, green, (10, 50, 5, 5))
fb.push(screen, [pygame.Rect(10, 50, 5, 5), pygame.Rect(200, 52, 5, 5)])
assert px(10, 50) == rgb565(green) and px(60, 51) == rgb565((10, 20, 30))
# full frame, and rectangles hanging off the edge are clipped
fb.push(screen)
assert px(0, 0) == rgb565((10, 20, 30)) and px(479, 319) == rgb565((10, 20, 30))
fb.push(screen, [pygame.Rect(470, 310, 50, 50), pygame.Rect(-20, -20, 10, 10)])
fb.close()

# 32 bpp
path32 = os.path.join(tmp, "fb32")
with open(path32, "wb") as f:
    f.write(b"\x00" * (W * 4 * H))
fb = device.FbOut(path32, W, H, 32, W * 4)
fb.push(screen, [pygame.Rect(100, 50, 2, 2)])
assert bytes(fb.mm[(50 * W + 100) * 4:(50 * W + 100) * 4 + 3]) == bytes([0, 0, 255]), "XRGB8888 is stored B, G, R"
fb.close()

# ---- calibration: a panel mounted rotated, with one axis reversed ----
targets = device.calibration_targets(W, H)


def panel(x, y):                 # screen x comes from raw y (reversed), screen y from raw x
    return round(300 + y / H * 3400), round(3900 - x / W * 3500)


cal = device.solve_calibration(targets, [panel(*t) for t in targets])
assert cal and cal["swap"] and cal["ax"] < 0 < cal["ay"], cal
t = device.Touch((W, H), cal)
for sx, sy in ((5, 5), (240, 160), (470, 300), (60, 290)):
    mx, my = t.map(*panel(sx, sy))
    assert abs(mx - sx) < 1.5 and abs(my - sy) < 1.5, (sx, sy, mx, my)
assert device.solve_calibration(targets, [(100, 100), (120, 110), (130, 140)]) is None, "taps too close together"

# ---- touch events ----
got = []
t = device.Touch((W, H), cal, lambda kind, pos: got.append((kind, pos)))


def ev(kind, code, value):
    return device.EVENT.pack(0, 0, kind, code, value)


def press(points, chunk=None):
    data = b""
    for i, (sx, sy) in enumerate(points):
        rx, ry = panel(sx, sy)
        data += ev(device.EV_ABS, device.ABS_X, rx) + ev(device.EV_ABS, device.ABS_Y, ry)
        if i == 0:
            data += ev(device.EV_KEY, device.BTN_TOUCH, 1)
        data += ev(device.EV_SYN, 0, 0)
    data += ev(device.EV_KEY, device.BTN_TOUCH, 0) + ev(device.EV_SYN, 0, 0)
    if chunk:                    # delivered in awkward pieces, as a real read() can
        for i in range(0, len(data), chunk):
            t.feed(data[i:i + chunk])
    else:
        t.feed(data)


press([(400, 160), (400, 160), (400, 161)], chunk=7)
assert [k for k, _ in got] == ["down", "up"], got
assert abs(got[0][1][0] - 400) <= 2 and abs(got[0][1][1] - 160) <= 2
got.clear()
press([(240, 300), (240, 280), (240, 240), (240, 200)])          # swipe up
kinds = [k for k, _ in got]
assert kinds[0] == "down" and kinds[-1] == "up" and "move" in kinds, kinds
assert got[-1][1][1] < got[0][1][1] - 30, "the swipe should end well above where it began"
got.clear()
press([(100, 100)])                                                # a tap that produced a single reading
assert [k for k, _ in got] == ["down", "up"], got

# raw mode collects one tap per press and emits nothing
got.clear()
t.raw_mode = True
press([(57, 48)] * 6)
assert not got and t.raw_taps.get_nowait() == panel(57, 48)
print("ok")
