#!/usr/bin/env python3
"""Generate region view cover images for Jellyfin/途播 (movie-app card style)."""
import os
import math
import random
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = "output/covers"
FONT_BOLD = "C:/Windows/Fonts/SOURCEHANSANSCN-HEAVY.OTF"
FONT_REG = "C:/Windows/Fonts/SOURCEHANSANSCN-REGULAR.OTF"
FONT_EN = "C:/Windows/Fonts/ARIAL.TTF"
WIDTH, HEIGHT = 800, 450

REGIONS = [
    ("中国大陆", "#C4502E", "#E07A3E", False),
    ("香港", "#1B4B6B", "#2E86AB", False),
    ("台湾", "#2E8B57", "#5FAD65", False),
    ("美国", "#1E3A5F", "#4A69BD", False),
    ("日本", "#E8A1B8", "#F4C2C2", True),
    ("韩国", "#2E4A62", "#5D8AA8", False),
    ("英国", "#2F4F4F", "#557B7A", False),
    ("法国", "#3A5F8A", "#6A8FC5", False),
    ("泰国", "#8E44AD", "#BB8FCE", False),
    ("印度", "#E67E22", "#F0B27A", False),
    ("欧美", "#24344B", "#4B5D77", False),
    ("其他", "#5D6D7E", "#8FA1B3", False),
]


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def rgba(color_hex, alpha):
    return hex_to_rgb(color_hex) + (alpha,)


def overlay():
    return Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))


def linear_gradient(draw, w, h, c1, c2, angle=135):
    c1 = hex_to_rgb(c1)
    c2 = hex_to_rgb(c2)
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)
    for y in range(h):
        for x in range(0, w, 2):
            t = ((x * dx + y * dy) + (w + h) * 0.3) / ((w + h) * 0.9)
            t = max(0, min(1, t))
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            draw.line([(x, y), (x + 1, y)], fill=(r, g, b))


def draw_film_strip(draw, y, side="top", color="#FFFFFF", alpha=18):
    c = rgba(color, alpha)
    hole_w, hole_h = WIDTH * 0.022, HEIGHT * 0.014
    gap = WIDTH * 0.045
    x = gap
    while x < WIDTH:
        if side == "top":
            draw.rectangle([x, y, x + hole_w, y + hole_h], fill=c)
        else:
            draw.rectangle([x, y - hole_h, x + hole_w, y], fill=c)
        x += gap


def draw_soft_spot(draw, cx, cy, radius, color, alpha_peak):
    spot = overlay()
    sd = ImageDraw.Draw(spot)
    for r in range(int(radius), 0, -4):
        a = int(alpha_peak * (r / radius) ** 1.2)
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgba(color, a))
    return spot


def draw_noise_texture(draw, w, h, color, alpha, seed=None):
    rng = random.Random(seed)
    c = rgba(color, alpha)
    for _ in range(120):
        x = rng.uniform(0, w)
        y = rng.uniform(0, h)
        r = rng.uniform(1, 3)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=c)


# ---------- icon drawers (outline watermark style) ----------

def icon_mainland(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy, r = WIDTH * 0.78, HEIGHT * 0.52, size * 0.30
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=lw)
    draw.ellipse([cx - r * 0.35, cy - r * 0.35, cx + r * 0.35, cy + r * 0.35], outline=c, width=lw)
    for ang in [45, 135, 225, 315]:
        hr = r * 0.72
        hx = cx + math.cos(math.radians(ang)) * hr
        hy = cy + math.sin(math.radians(ang)) * hr
        draw.ellipse([hx - size * 0.04, hy - size * 0.04, hx + size * 0.04, hy + size * 0.04], fill=c)


def icon_hongkong(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.52
    r = size * 0.24
    for i in range(5):
        ang = math.radians(i * 72 - 90)
        px = cx + math.cos(ang) * r
        py = cy + math.sin(ang) * r
        draw.ellipse([px - r * 0.35, py - r * 0.35, px + r * 0.35, py + r * 0.35], outline=c, width=max(1, lw // 2))
    draw.ellipse([cx - r * 0.12, cy - r * 0.12, cx + r * 0.12, cy + r * 0.12], fill=c)


def icon_taiwan(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.70
    w, h = size * 0.24, size * 0.55
    segments = 8
    seg_h = h / segments
    for i in range(segments):
        seg_w = w * (1 - i * 0.08)
        x1 = cx - seg_w / 2
        y1 = cy - h + i * seg_h
        x2 = cx + seg_w / 2
        y2 = y1 + seg_h * 0.85
        draw.rectangle([x1, y1, x2, y2], outline=c, width=lw)


def icon_usa(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.52
    draw.polygon([
        (cx, cy - size * 0.35),
        (cx + size * 0.18, cy + size * 0.28),
        (cx - size * 0.18, cy + size * 0.28),
    ], outline=c, width=lw)
    draw.ellipse([cx - size * 0.06, cy - size * 0.42, cx + size * 0.06, cy - size * 0.30], outline=c, width=lw)
    draw.line([(cx, cy - size * 0.30), (cx + size * 0.22, cy - size * 0.45)], fill=c, width=lw)
    draw.ellipse([cx + size * 0.20, cy - size * 0.50, cx + size * 0.26, cy - size * 0.42], outline=c, width=lw)
    draw.rectangle([cx - size * 0.16, cy - size * 0.05, cx - size * 0.02, cy + size * 0.12], outline=c, width=lw)


def icon_japan(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.68
    draw.polygon([
        (cx - size * 0.35, cy),
        (cx, cy - size * 0.50),
        (cx + size * 0.35, cy),
    ], outline=c, width=lw)
    snow = rgba(color, alpha)
    draw.polygon([
        (cx - size * 0.12, cy - size * 0.30),
        (cx, cy - size * 0.50),
        (cx + size * 0.12, cy - size * 0.30),
    ], fill=snow)


def icon_korea(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.58
    draw.polygon([
        (cx - size * 0.32, cy),
        (cx, cy - size * 0.18),
        (cx + size * 0.32, cy),
    ], outline=c, width=lw)
    for ox in [-size * 0.18, 0, size * 0.18]:
        draw.rectangle([cx + ox - size * 0.025, cy, cx + ox + size * 0.025, cy + size * 0.22], outline=c, width=lw)
    draw.rectangle([cx - size * 0.30, cy + size * 0.22, cx + size * 0.30, cy + size * 0.26], outline=c, width=lw)


def icon_uk(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.52
    w, h = size * 0.18, size * 0.55
    draw.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], outline=c, width=lw)
    draw.polygon([
        (cx - w / 2, cy - h / 2), (cx, cy - h / 2 - size * 0.10), (cx + w / 2, cy - h / 2)
    ], outline=c, width=lw)
    draw.ellipse([cx - size * 0.07, cy - h / 2 + size * 0.05, cx + size * 0.07, cy - h / 2 + size * 0.19], outline=c, width=lw)


def icon_france(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.60
    draw.polygon([
        (cx - size * 0.08, cy - size * 0.25), (cx + size * 0.08, cy - size * 0.25),
        (cx + size * 0.18, cy + size * 0.05), (cx - size * 0.18, cy + size * 0.05)
    ], outline=c, width=lw)
    draw.polygon([
        (cx - size * 0.12, cy), (cx + size * 0.12, cy),
        (cx + size * 0.22, cy + size * 0.28), (cx - size * 0.22, cy + size * 0.28)
    ], outline=c, width=lw)
    draw.polygon([
        (cx - size * 0.22, cy + size * 0.28), (cx - size * 0.16, cy + size * 0.42),
        (cx, cy + size * 0.30), (cx + size * 0.16, cy + size * 0.42),
        (cx + size * 0.22, cy + size * 0.28)
    ], outline=c, width=lw)


def icon_thailand(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.64
    draw.rectangle([cx - size * 0.18, cy + size * 0.18, cx + size * 0.18, cy + size * 0.25], outline=c, width=lw)
    tiers = 4
    for i in range(tiers):
        y = cy + size * 0.18 - i * size * 0.10
        w = size * 0.16 + (tiers - i) * size * 0.04
        draw.polygon([(cx - w, y), (cx, y - size * 0.06), (cx + w, y)], outline=c, width=lw)
    draw.polygon([
        (cx - size * 0.02, cy - size * 0.26), (cx, cy - size * 0.45), (cx + size * 0.02, cy - size * 0.26)
    ], outline=c, width=lw)


def icon_india(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.60
    draw.rectangle([cx - size * 0.22, cy - size * 0.08, cx + size * 0.22, cy + size * 0.22], outline=c, width=lw)
    draw.ellipse([cx - size * 0.12, cy - size * 0.22, cx + size * 0.12, cy + size * 0.02], outline=c, width=lw)
    for ox in [-size * 0.26, size * 0.26]:
        draw.rectangle([cx + ox - size * 0.03, cy - size * 0.05, cx + ox + size * 0.03, cy + size * 0.22], outline=c, width=lw)
        draw.polygon([
            (cx + ox - size * 0.05, cy - size * 0.05), (cx + ox, cy - size * 0.12), (cx + ox + size * 0.05, cy - size * 0.05)
        ], outline=c, width=lw)


def icon_west(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.52
    r = size * 0.25
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=lw)
    draw.arc([cx - r, cy - r * 0.6, cx + r, cy + r * 0.6], 0, 360, fill=c, width=max(1, lw // 2))
    draw.line([(cx, cy - r), (cx, cy + r)], fill=c, width=max(1, lw // 2))
    draw.line([(cx - r * 0.5, cy - r * 0.85), (cx - r * 0.5, cy + r * 0.85)], fill=c, width=max(1, lw // 2))
    draw.line([(cx + r * 0.5, cy - r * 0.85), (cx + r * 0.5, cy + r * 0.85)], fill=c, width=max(1, lw // 2))


def icon_other(draw, size, color, alpha, lw):
    c = rgba(color, alpha)
    cy = HEIGHT * 0.52
    for i, x in enumerate([WIDTH * 0.70, WIDTH * 0.78, WIDTH * 0.86]):
        draw.ellipse([x - size * 0.06, cy - size * 0.06, x + size * 0.06, cy + size * 0.06], outline=c, width=lw)


ICON_MAP = {
    "中国大陆": icon_mainland,
    "香港": icon_hongkong,
    "台湾": icon_taiwan,
    "美国": icon_usa,
    "日本": icon_japan,
    "韩国": icon_korea,
    "英国": icon_uk,
    "法国": icon_france,
    "泰国": icon_thailand,
    "印度": icon_india,
    "欧美": icon_west,
    "其他": icon_other,
}


class FloatDraw:
    def __init__(self, draw):
        self.draw = draw

    def _intxy(self, xy):
        return [int(v) for v in xy]

    def ellipse(self, xy, *args, **kwargs):
        return self.draw.ellipse(self._intxy(xy), *args, **kwargs)

    def rectangle(self, xy, *args, **kwargs):
        return self.draw.rectangle(self._intxy(xy), *args, **kwargs)

    def polygon(self, xy, *args, **kwargs):
        return self.draw.polygon([self._intxy(p) for p in xy], *args, **kwargs)

    def line(self, xy, *args, **kwargs):
        if isinstance(xy[0], (tuple, list)):
            return self.draw.line([self._intxy(p) for p in xy], *args, **kwargs)
        return self.draw.line(self._intxy(xy), *args, **kwargs)

    def pieslice(self, xy, start, end, *args, **kwargs):
        return self.draw.pieslice(self._intxy(xy), start, end, *args, **kwargs)

    def arc(self, xy, start, end, *args, **kwargs):
        return self.draw.arc(self._intxy(xy), start, end, *args, **kwargs)

    def text(self, xy, *args, **kwargs):
        return self.draw.text((int(xy[0]), int(xy[1])), *args, **kwargs)

    def textbbox(self, xy, *args, **kwargs):
        return self.draw.textbbox((int(xy[0]), int(xy[1])), *args, **kwargs)

    def rounded_rectangle(self, xy, *args, **kwargs):
        return self.draw.rounded_rectangle(self._intxy(xy), *args, **kwargs)


def generate(region, c1, c2, text_dark):
    img = Image.new("RGB", (WIDTH, HEIGHT), hex_to_rgb(c1))
    draw = ImageDraw.Draw(img)

    # base diagonal gradient
    linear_gradient(draw, WIDTH, HEIGHT, c1, c2, angle=145)

    accent = "#FFFFFF" if not text_dark else "#000000"

    # subtle grain / noise texture (very faint)
    grain = overlay()
    gd = ImageDraw.Draw(grain)
    draw_noise_texture(gd, WIDTH, HEIGHT, accent, 8, seed=hash(region) % 100000)
    img = Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")
    draw = ImageDraw.Draw(img)
    fdraw = FloatDraw(draw)

    # soft radial glow behind right-side icon area
    glow = draw_soft_spot(draw, WIDTH * 0.80, HEIGHT * 0.50, WIDTH * 0.35, accent, 16)
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(img)
    fdraw = FloatDraw(draw)

    # film strips on edges
    draw_film_strip(draw, HEIGHT * 0.016, side="top", color=accent, alpha=14)
    draw_film_strip(draw, HEIGHT * 0.984, side="bottom", color=accent, alpha=14)

    # fine corner light leaks (very subtle arcs)
    arc_c = rgba(accent, 12)
    fdraw.arc([WIDTH * 0.55, -HEIGHT * 0.25, WIDTH * 1.25, HEIGHT * 0.55], 180, 270, fill=arc_c, width=HEIGHT // 20)
    fdraw.arc([-WIDTH * 0.10, HEIGHT * 0.45, WIDTH * 0.45, HEIGHT * 1.10], 0, 90, fill=arc_c, width=HEIGHT // 25)

    # right-side outline watermark icon
    icon_fn = ICON_MAP.get(region, icon_other)
    line_w = max(2, int(HEIGHT * 0.018))
    icon_fn(fdraw, int(min(WIDTH, HEIGHT) * 0.55), accent, 90, line_w)

    # text area with safe margins
    text_color = "#1A1A1A" if text_dark else "#FFFFFF"
    left_x = WIDTH * 0.12
    top_y = HEIGHT * 0.16

    # pill badge "电影"
    badge_h = 40
    badge_font = ImageFont.truetype(FONT_BOLD, 24)
    bbox_badge = fdraw.textbbox((0, 0), "电影", font=badge_font)
    badge_w = (bbox_badge[2] - bbox_badge[0]) + badge_h * 1.6
    badge_y = top_y
    fdraw.rounded_rectangle(
        [left_x, badge_y, left_x + badge_w, badge_y + badge_h],
        radius=badge_h // 2,
        fill=rgba("#FFFFFF" if not text_dark else "#000000", 140)
    )
    fdraw.text((left_x + badge_h * 0.8, badge_y + (badge_h - (bbox_badge[3] - bbox_badge[1])) // 2 - 2),
               "电影", fill=text_color, font=badge_font)

    # region name
    region_y = top_y + badge_h + HEIGHT * 0.07
    region_font = ImageFont.truetype(FONT_BOLD, 84)
    bbox_region = fdraw.textbbox((0, 0), region, font=region_font)
    max_w = WIDTH * 0.46
    if bbox_region[2] - bbox_region[0] > max_w:
        scale = max_w / (bbox_region[2] - bbox_region[0])
        new_size = max(52, int(84 * scale))
        region_font = ImageFont.truetype(FONT_BOLD, new_size)
        bbox_region = fdraw.textbbox((0, 0), region, font=region_font)
    fdraw.text((left_x, region_y), region, fill=text_color, font=region_font)

    # MOVIE label
    movie_font = ImageFont.truetype(FONT_EN, 26)
    movie_y = region_y + (bbox_region[3] - bbox_region[1]) + HEIGHT * 0.05
    spacing = 13
    cur_x = left_x
    for ch in "MOVIE":
        fdraw.text((cur_x, movie_y), ch, fill=text_color, font=movie_font)
        bw = fdraw.textbbox((0, 0), ch, font=movie_font)
        cur_x += (bw[2] - bw[0]) + spacing

    # tiny sparkle
    star_c = rgba(accent, 70)
    sx = cur_x + 12
    sy = movie_y + 8
    fdraw.polygon([(sx, sy - 6), (sx + 2, sy - 1), (sx + 7, sy), (sx + 2, sy + 1),
                   (sx, sy + 6), (sx - 2, sy + 1), (sx - 7, sy), (sx - 2, sy - 1)], fill=star_c)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"view_{region}.jpg")
    img.save(path, "JPEG", quality=92)
    print("saved", path)


if __name__ == "__main__":
    for r, c1, c2, dark in REGIONS:
        generate(r, c1, c2, dark)
