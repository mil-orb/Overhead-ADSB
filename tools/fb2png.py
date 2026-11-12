"""Turn a raw dump of the Pi's 16-bit framebuffer into a PNG.

    ssh pi@overhead.local 'cat /dev/fb1' > screen.raw
    python tools/fb2png.py screen.raw screen.png

tools/pi.py screen does both steps.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402


def convert(raw: bytes, out: str, size=(480, 320)):
    w, h = size
    if len(raw) < w * h * 2:
        raise SystemExit("expected %d bytes for a %dx%d 16-bit screen, got %d" % (w * h * 2, w, h, len(raw)))
    if not pygame.display.get_init():
        pygame.display.init()
        pygame.display.set_mode((1, 1))
    s = pygame.Surface((w, h), 0, 16, (0xF800, 0x07E0, 0x001F, 0))
    s.get_buffer().write(raw[:w * h * 2])
    pygame.image.save(s.convert(24), out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with open(sys.argv[1], "rb") as f:
        convert(f.read(), sys.argv[2])
    print("saved", sys.argv[2])
