"""The tips screen: what each part of the card does when you touch it.

Shown once, the first time the app runs (after the touch calibration on a Pi), and again
from Settings > Touch tips. Any tap or key puts it away.

It is drawn over a darkened copy of the card, so the zones line up with what they act on:
the left and right thirds step through aircraft, the middle opens the plane view, and a
swipe up from the lower part of the screen opens settings. It is a still picture, sent
to the panel once.
"""
from __future__ import annotations

import pygame

from .board import AMBER, INK

SWIPE_FROM = 0.6        # app.py opens settings for a swipe that starts below this fraction of the height


def draw(dst, board, card):
    """Paint the tips over `card` (a copy of the screen). Returns the rectangle to send."""
    W, H = dst.get_size()
    s = max(0.75, min(W / 480, H / 320))
    dst.blit(card, (0, 0))
    dst.fill((64, 64, 68), special_flags=pygame.BLEND_RGB_MULT)          # the card, dimmed to a quarter

    def text(t, cap, x, y, anchor="c", color=INK, alpha=255):
        # dark edges keep the letters readable over whatever flap is underneath
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            board.paint(dst, t, cap, x + dx, y + dy, anchor, color=(0, 0, 0), alpha=160)
        board.paint(dst, t, cap, x, y, anchor, color=color, alpha=alpha)

    big, mid, small = max(9, round(13 * s)), max(7, round(9 * s)), max(6, round(7 * s))
    lw = max(2, round(3 * s))
    band = int(H * SWIPE_FROM)

    text("TOUCH TIPS", mid, W // 2, round(10 * s), color=AMBER)

    # the three tap zones, split by dashed lines
    for x in (W // 3, 2 * W // 3):
        for y in range(round(30 * s), band - round(6 * s), round(10 * s)):
            pygame.draw.line(dst, (120, 112, 96), (x, y), (x, y + round(5 * s)), max(1, round(s)))
    cy = round(H * 0.25)
    a = round(14 * s)
    for cx, sign in ((W // 6, -1), (5 * W // 6, 1)):
        tip = cx + sign * a // 2
        pygame.draw.lines(dst, AMBER, False, [(tip - sign * a, cy - a), (tip, cy), (tip - sign * a, cy + a)], lw)
    text("PREVIOUS", big, W // 6, cy + round(24 * s))
    text("NEXT", big, 5 * W // 6, cy + round(24 * s))
    # the middle: an airliner seen from below
    m = W // 2
    half = [(0.0, -1.0), (0.11, -0.8), (0.12, -0.28), (1.15, 0.2), (1.15, 0.34), (0.12, 0.1), (0.09, 0.62),
            (0.46, 0.86), (0.46, 0.98), (0.0, 0.9)]
    outline = half + [(-x, y) for x, y in reversed(half[1:-1])]
    pygame.draw.polygon(dst, AMBER, [(m + x * a * 1.1, cy + y * a * 1.1) for x, y in outline])
    text("SEE THE PLANE", big, m, cy + round(24 * s))
    text("TAP AGAIN TO CLOSE", small, m, cy + round(42 * s), alpha=200)
    text("TAP A SIDE", small, W // 6, cy + round(42 * s), alpha=200)
    text("TAP A SIDE", small, 5 * W // 6, cy + round(42 * s), alpha=200)

    text("PRESS AND HOLD ANYWHERE TO STAY ON ONE PLANE", mid, W // 2, band - round(30 * s))

    # the swipe zone
    shade = pygame.Surface((W, H - band), 0, 32)
    shade.fill((22, 20, 15))
    dst.blit(shade, (0, band), special_flags=pygame.BLEND_RGB_ADD)
    pygame.draw.line(dst, (120, 112, 96), (0, band), (W, band), max(1, round(s)))
    ay = band + round(12 * s)
    for ax in (W // 2 - round(150 * s), W // 2 + round(150 * s)):
        pygame.draw.line(dst, AMBER, (ax, ay), (ax, ay + round(26 * s)), lw)
        pygame.draw.lines(dst, AMBER, False, [(ax - round(8 * s), ay + round(8 * s)), (ax, ay), (ax + round(8 * s), ay + round(8 * s))], lw)
    text("SWIPE UP FROM DOWN HERE", big, W // 2, ay + round(2 * s))
    text("FOR SETTINGS", mid, W // 2, ay + round(22 * s))
    text("TAP ANYWHERE TO START", mid, W // 2, H - round(18 * s), color=AMBER)
    return pygame.Rect(0, 0, W, H)
