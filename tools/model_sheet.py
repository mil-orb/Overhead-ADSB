"""Draw aircraft models side by side, to check how they look. Three views each: from below
(how an overhead pass looks), from the side, and from above and behind.

    python tools/model_sheet.py out.png B738 A320 CRJ7 C172 C17
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead import hangar, models  # noqa: E402

pygame.display.init()
pygame.display.set_mode((1, 1))
out, keys = sys.argv[1], [k.upper() for k in sys.argv[2:] if k != "--small"] or sorted(models.TYPES)[:12]
Z = 1 if "--small" in sys.argv else 2          # --small: undoubled, for looking over many at once
W, H = 232, 107                         # the size the Pi renders at, before doubling
VIEWS = [(80, 0, 0, 150, 68), (90, 0, 0, 0, 4), (300, 4, 0, 160, -28)]      # track, climb, roll, bearing, elevation
sheet = pygame.Surface((len(VIEWS) * (W * Z + 4), len(keys) * (H * Z + 4)))
sheet.fill((60, 60, 66))
shades = {}
for row, key in enumerate(keys):
    model, exact = models.model_for(key)
    paint = hangar.livery("UAL1", False, model.kind)
    for col, (track, climb, roll, az, elev) in enumerate(VIEWS):
        low = pygame.Surface((W, H))
        low.fill((70, 110, 170) if elev > 0 else (120, 150, 190))
        m = hangar.pose(track, climb, roll, az, elev)
        n = hangar.render(low, model, m, paint, (-0.35, 0.75, -0.55), W // 2, H // 2,
                          0.46 * min(W, H * 1.55) / model.radius, shades)
        sheet.blit(pygame.transform.scale(low, (W * Z, H * Z)), (col * (W * Z + 4), row * (H * Z + 4)))
    print("%-5s %-30s %4d points %4d faces  exact=%s  %s" % (key, model.name[:30], len(model.points), len(model.faces), exact, model.credit[:40]))
pygame.image.save(sheet, out)
print("saved", out)
