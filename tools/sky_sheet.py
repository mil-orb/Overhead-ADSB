"""Draw the plane view's sky and ground under different light, to check how they look.

    python tools/sky_sheet.py out.png [TYPE]

One row per sky (day, dusk, night, overcast, and the real sky right now over a made-up
place). Columns look toward the sun just above the horizon, halfway up the sky, and
down at the ground from above. An aircraft is drawn in each, lit to match.
"""
import math
import os
import sys
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead import hangar, models, scene  # noqa: E402
from overhead.feeds import DEMO_SKIES, DEMO_SKY  # noqa: E402
from overhead.sky import Sky, moon_position, sun_position  # noqa: E402

pygame.display.init()
pygame.display.set_mode((1, 1))
out = sys.argv[1]
model, _ = models.model_for(sys.argv[2].upper() if len(sys.argv) > 2 else "B738")
W, H = 464, 216                                   # the plane view's window on a 480 x 320 screen
LAT, LON = 40.0, -100.0
now = time.time()
se, sa = sun_position(LAT, LON, now)
me, ma, lit, waxing = moon_position(LAT, LON, now)
skies = [(name, Sky(**{**DEMO_SKY, **extra})) for name, extra in DEMO_SKIES.items()]
skies.append(("now", Sky(sun_elev=se, sun_az=sa, moon_elev=me, moon_az=ma, moon_lit=lit, moon_waxing=waxing, known=False)))
sheet = pygame.Surface((3 * (W + 4), len(skies) * (H + 4)))
sheet.fill((60, 60, 66))
shades = {}
paint = hangar.livery("UAL1", False, model.kind)
for row, (name, sky) in enumerate(skies):
    sc = scene.Scene(W, H)
    t0 = time.perf_counter()
    sc.pano = sc.paint(sky)
    took = time.perf_counter() - t0
    amb, direct, (up, around), key = scene.tone(sky)
    toward = sky.sun_az if sky.sun_elev > -12 else sky.moon_az
    for col, (az, elev) in enumerate(((toward + 14, 9.0), (toward + 150, 42.0), (toward + 60, -38.0))):
        win = pygame.Surface((W, H), 0, 32)
        big = pygame.Surface((W * 2, H * 2), 0, 32)
        sc.window(win, az, elev)
        pygame.transform.scale(win, big.get_size(), big)
        m = hangar.pose(az + 80, 3, 12, az, elev)
        a, e = math.radians(az), math.radians(elev)
        f = (math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e))
        rt = (math.cos(a), -math.sin(a), 0.0)
        u = (rt[1] * f[2] - rt[2] * f[1], rt[2] * f[0] - rt[0] * f[2], rt[0] * f[1] - rt[1] * f[0])
        s1, s2 = math.radians(up), math.radians(around)
        sun = (math.sin(s2) * math.cos(s1), math.cos(s2) * math.cos(s1), math.sin(s1))
        light = tuple(sum(sun[i] * axis[i] for i in range(3)) for axis in (rt, u, f))
        scale = 0.40 * min(W, H * 1.55) / model.radius * 2
        hangar.render(big, model, m, paint, light, W, H, scale, shades, (amb, direct, key))
        if sky.sun_elev < 2:
            hangar.lamps(big, model, m, W, H, scale, 2, 1.0)
        sheet.blit(pygame.transform.smoothscale(big, (W, H)), (col * (W + 4), row * (H + 4)))
    print("%-9s sun %5.1f at %3.0f   panorama %d x %d painted in %.0f ms" % (name, sky.sun_elev, sky.sun_az, sc.pw, sc.ph, took * 1000))
pygame.image.save(sheet, out)
print("saved", out)
