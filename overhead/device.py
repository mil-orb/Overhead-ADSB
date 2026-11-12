"""Running on a Pi with no desktop: draw to the Linux framebuffer, read the touch panel from evdev.

pygame renders into an ordinary off-screen surface. FbOut copies the rectangles that
changed into /dev/fbN in the panel's own pixel format, and the kernel driver sends
those lines over SPI. Touch turns raw resistive samples into the same mouse events
the rest of the app already handles, using a three-point calibration.

Nothing here needs X, Wayland, or SDL's KMS backend.
"""
from __future__ import annotations

import glob
import mmap
import os
import queue
import struct
import threading
import time

import pygame

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y = 0, 1
BTN_TOUCH = 330
EVENT = struct.Struct("llHHi")       # struct input_event; long is 4 bytes on 32-bit, 8 on 64-bit


def _read(path):
    with open(path) as f:
        return f.read().strip()


def find_framebuffer(prefer=None):
    """(device, width, height, bits per pixel, stride) for the panel, or None.

    With no preference it picks the first small framebuffer, so an attached HDMI
    screen on fb0 does not win over the 480x320 panel on fb1.
    """
    found = []
    for d in sorted(glob.glob("/sys/class/graphics/fb[0-9]*")):
        try:
            w, h = (int(v) for v in _read(d + "/virtual_size").split(","))
            info = ("/dev/" + os.path.basename(d), w, h, int(_read(d + "/bits_per_pixel")), int(_read(d + "/stride")))
        except Exception:
            continue
        if prefer and info[0] == prefer:
            return info
        found.append(info)
    if prefer:
        return None
    small = [f for f in found if f[1] * f[2] <= 800 * 480]
    return (small or found or [None])[0]


def find_touch(prefer=None):
    """Path of the touch panel's event device, or None."""
    if prefer:
        return prefer if os.path.exists(prefer) else None
    for d in sorted(glob.glob("/sys/class/input/event*")):
        try:
            name = _read(d + "/device/name").lower()
        except Exception:
            continue
        if "ads7846" in name or "touch" in name or "xpt2046" in name:
            return "/dev/input/" + os.path.basename(d)
    return None


def spi_lines_per_second(dev, width, bpp):
    """How many screen lines a second an SPI panel can receive, from its bus speed. None if the
    framebuffer isn't on SPI (HDMI, a test file), where there is no such limit."""
    try:
        node = "/sys/class/graphics/%s/device/of_node/spi-max-frequency" % os.path.basename(dev)
        with open(node, "rb") as f:
            hz = struct.unpack(">I", f.read(4))[0]
        return hz / float(width * bpp) if hz else None
    except Exception:
        return None


class FbOut:
    """Copies changed rectangles of a pygame surface into a memory-mapped framebuffer.

    The fast path is to draw on `surface()`: a pygame surface already in the panel's pixel
    format. Pushing a band of it is then one memory copy. Any other surface still works
    and is converted on the way through.
    """

    def __init__(self, dev, w, h, bpp, stride):
        if bpp not in (16, 32):
            raise ValueError("unsupported framebuffer depth: %d" % bpp)
        self.size, self.stride, self.bytes = (w, h), stride, bpp // 8
        self.fd = os.open(dev, os.O_RDWR)
        self.mm = mmap.mmap(self.fd, stride * h)
        self.masks = (0xF800, 0x07E0, 0x001F, 0) if bpp == 16 else (0xFF0000, 0x00FF00, 0x0000FF, 0)
        self.bpp = bpp
        self.shadow = None
        self.full = pygame.Rect(0, 0, w, h)
        self._synced = False
        self.lines_per_second = spi_lines_per_second(dev, w, bpp)

    def surface(self):
        """A surface in the panel's own format. Draw on this and push() never has to convert."""
        return pygame.Surface(self.size, 0, self.bpp, self.masks)

    def _native(self, s):
        return s.get_bitsize() == self.bpp and tuple(s.get_masks())[:3] == self.masks[:3] and s.get_size() == self.size

    def push(self, surface, rects=None):
        """Send the given rectangles (or everything) to the panel."""
        if not self._synced:       # the first push must be the whole frame: band merging below copies
            rects = None           # the gaps between rectangles too, and those must already match
            self._synced = True
        rects = [self.full] if not rects else [r.clip(self.full) for r in rects]
        rects = [r for r in rects if r.w > 0 and r.h > 0]
        if not rects:
            return
        # Merge into horizontal bands. The panel driver sends whole lines anyway.
        bands = []
        for r in sorted(rects, key=lambda r: r.top):
            if bands and r.top <= bands[-1][1]:
                b = bands[-1]
                b[1], b[2], b[3] = max(b[1], r.bottom), min(b[2], r.left), max(b[3], r.right)
            else:
                bands.append([r.top, r.bottom, r.left, r.right])
        if self._native(surface):
            src = surface
        else:
            if self.shadow is None:
                self.shadow = self.surface()
            for r in rects:
                self.shadow.blit(surface, r, r)                 # format conversion happens here, in C
            src = self.shadow
        pitch, n, mm, stride = src.get_pitch(), self.bytes, self.mm, self.stride
        buf = src.get_buffer()                                  # locks the surface until released below
        view = memoryview(buf)
        try:
            for top, bottom, left, right in bands:
                if pitch == stride:                             # whole lines in one copy
                    mm[top * stride:bottom * stride] = view[top * pitch:bottom * pitch]
                else:
                    a, b = left * n, right * n
                    for y in range(top, bottom):
                        mm[y * stride + a:y * stride + b] = view[y * pitch + a:y * pitch + b]
        finally:
            view.release()
            del buf

    def close(self):
        try:
            self.mm.close()
            os.close(self.fd)
        except Exception:
            pass


def calibration_targets(w, h):
    """Three points to tap: top left, top right, bottom right."""
    return [(round(w * 0.12), round(h * 0.15)), (round(w * 0.88), round(h * 0.15)), (round(w * 0.88), round(h * 0.85))]


def solve_calibration(targets, raws):
    """Work out the raw-to-screen mapping from three taps. Returns a dict, or None if the taps make no sense.

    Going from the first target to the second only screen x changes, so whichever raw axis
    moved more is the one wired to x. That also covers panels mounted rotated or mirrored.
    """
    (p1, p2, p3), (r1, r2, r3) = targets, raws
    swap = abs(r2[1] - r1[1]) > abs(r2[0] - r1[0])
    u = [r[1] if swap else r[0] for r in raws]
    v = [r[0] if swap else r[1] for r in raws]
    if abs(u[1] - u[0]) < 200 or abs(v[2] - v[1]) < 200:
        return None
    ax = (p2[0] - p1[0]) / (u[1] - u[0])
    ay = (p3[1] - p2[1]) / (v[2] - v[1])
    return {"swap": swap, "ax": ax, "bx": p1[0] - ax * u[0], "ay": ay, "by": p2[1] - ay * v[1]}


class Touch:
    """Resistive touch panel reader.

    emit(kind, (x, y)) is called with kind "down", "move" or "up". In raw mode it
    collects untranslated taps into `raw_taps` instead, for calibration.
    """

    def __init__(self, size, cal=None, emit=None):
        self.w, self.h = size
        self.cal = cal
        self.emit = emit or (lambda kind, pos: None)
        self.raw_mode = False
        self.raw_taps: queue.Queue = queue.Queue()
        self._buf = b""
        self._rx = self._ry = None
        self._touching = False
        self._n = 0
        self._pos = None
        self._samples = []

    def map(self, rx, ry):
        c = self.cal or {"swap": False, "ax": self.w / 4096.0, "bx": 0.0, "ay": self.h / 4096.0, "by": 0.0}
        u, v = (ry, rx) if c["swap"] else (rx, ry)
        x, y = c["ax"] * u + c["bx"], c["ay"] * v + c["by"]
        return max(0.0, min(self.w - 1.0, x)), max(0.0, min(self.h - 1.0, y))

    def feed(self, data: bytes):
        self._buf += data
        size = EVENT.size
        while len(self._buf) >= size:
            _, _, kind, code, value = EVENT.unpack(self._buf[:size])
            self._buf = self._buf[size:]
            if kind == EV_ABS:
                if code == ABS_X:
                    self._rx = value
                elif code == ABS_Y:
                    self._ry = value
            elif kind == EV_KEY and code == BTN_TOUCH:
                self._touching = bool(value)
                if not value:
                    self._release()
            elif kind == EV_SYN and self._touching and self._rx is not None and self._ry is not None:
                self._sample()

    def _sample(self):
        self._n += 1
        if self.raw_mode:
            self._samples.append((self._rx, self._ry))
            return
        x, y = self.map(self._rx, self._ry)
        if self._n == 1:
            self._pos = (x, y)                 # the first reading of a press is often off: wait for a second
        elif self._n == 2:
            self._pos = (x, y)
            self.emit("down", self._ipos())
        else:
            px, py = self._pos
            nx, ny = px * 0.5 + x * 0.5, py * 0.5 + y * 0.5      # smooth resistive jitter
            if abs(nx - px) + abs(ny - py) >= 1.5:
                self._pos = (nx, ny)
                self.emit("move", self._ipos())

    def _release(self):
        n, self._n = self._n, 0
        if self.raw_mode:
            s, self._samples = self._samples, []
            if len(s) >= 3:
                s = s[1:-1] if len(s) > 4 else s
                xs, ys = sorted(p[0] for p in s), sorted(p[1] for p in s)
                self.raw_taps.put((xs[len(xs) // 2], ys[len(ys) // 2]))
            return
        if n == 1:                               # a tap so quick it gave one reading
            self.emit("down", self._ipos())
        if n >= 1:
            self.emit("up", self._ipos())

    def _ipos(self):
        if self._pos is None:           # released before any position arrived
            return 0, 0
        return int(round(self._pos[0])), int(round(self._pos[1]))

    def start(self, path):
        def loop():
            while True:
                try:
                    with open(path, "rb", buffering=0) as f:
                        while True:
                            data = f.read(EVENT.size * 32)
                            if not data:
                                break
                            try:
                                self.feed(data)
                            except Exception as e:      # one odd packet: drop it and keep reading
                                self._n, self._samples = 0, []
                                print("touch event dropped:", type(e).__name__)
                except Exception as e:
                    print("touch reader stopped:", type(e).__name__)
                time.sleep(2.0)                         # the panel went away: try to open it again
        threading.Thread(target=loop, daemon=True).start()
