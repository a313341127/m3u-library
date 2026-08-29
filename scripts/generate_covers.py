#!/usr/bin/env python3
"""Generate region view cover images for Jellyfin/途播 (movie-app card style)."""
import os
import sys
import math
import random
import shutil
from PIL import Image, ImageDraw, ImageFont

# 允许以 `python scripts/generate_covers.py` 方式运行时导入项目根包（collector/config）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUTPUT_DIR = "output/covers"
WIDTH, HEIGHT = 800, 450


def find_font(candidates):
    """跨平台字体查找：返回第一个存在的字体路径。"""
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise FileNotFoundError("找不到可用字体，尝试以下路径均不存在：" + "; ".join(candidates))


# 仓库内置中文字体（scripts/fonts/），优先于系统字体，保证 CI（ubuntu-latest 无中文字体）
# 与本地 Windows（SourceHan）都能稳定渲染中文，避免环境差异导致封面缺字或步骤失败。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_FONT = os.path.join(_SCRIPT_DIR, "fonts", "wqy-microhei.ttc")

# Windows 优先 SourceHan；CI/其他平台回退到仓库内置 wqy-microhei；最后尝试系统字体
FONT_BOLD = find_font([
    "C:/Windows/Fonts/SOURCEHANSANSCN-HEAVY.OTF",
    "C:/Windows/Fonts/SOURCEHANSANSCN-BOLD.OTF",
    "C:/Windows/Fonts/msyhbd.ttc",
    LOCAL_FONT,
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
])
FONT_REG = find_font([
    "C:/Windows/Fonts/SOURCEHANSANSCN-REGULAR.OTF",
    "C:/Windows/Fonts/msyh.ttc",
    LOCAL_FONT,
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
])
FONT_EN = find_font([
    "C:/Windows/Fonts/ARIALBD.TTF",
    "C:/Windows/Fonts/ARIAL.TTF",
    LOCAL_FONT,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
])

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


def icon_play(draw, size, color, alpha, lw):
    """通用播放三角（分类视图封面水印）"""
    c = rgba(color, alpha)
    cx, cy = WIDTH * 0.78, HEIGHT * 0.52
    r = size * 0.32
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=lw)
    tri = [
        (cx - r * 0.34, cy - r * 0.42),
        (cx - r * 0.34, cy + r * 0.42),
        (cx + r * 0.46, cy),
    ]
    draw.polygon(tri, fill=c)


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
    "cat": icon_play,
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


def generate(badge_text, big_text, c1, c2, text_dark, label_en, icon_key, file_key, prefix="view"):
    img = Image.new("RGB", (WIDTH, HEIGHT), hex_to_rgb(c1))
    draw = ImageDraw.Draw(img)

    # base diagonal gradient
    linear_gradient(draw, WIDTH, HEIGHT, c1, c2, angle=145)

    accent = "#FFFFFF" if not text_dark else "#000000"

    # subtle grain / noise texture (very faint)
    grain = overlay()
    gd = ImageDraw.Draw(grain)
    draw_noise_texture(gd, WIDTH, HEIGHT, accent, 8, seed=hash(badge_text + big_text) % 100000)
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
    icon_fn = ICON_MAP.get(icon_key, icon_other)
    line_w = max(2, int(HEIGHT * 0.018))
    icon_fn(fdraw, int(min(WIDTH, HEIGHT) * 0.55), accent, 90, line_w)

    # text area with safe margins
    text_color = "#1A1A1A" if text_dark else "#FFFFFF"
    left_x = WIDTH * 0.12
    top_y = HEIGHT * 0.16

    # pill badge
    badge_h = 40
    badge_font = ImageFont.truetype(FONT_BOLD, 24)
    bbox_badge = fdraw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = (bbox_badge[2] - bbox_badge[0]) + badge_h * 1.6
    badge_y = top_y
    fdraw.rounded_rectangle(
        [left_x, badge_y, left_x + badge_w, badge_y + badge_h],
        radius=badge_h // 2,
        fill=rgba("#FFFFFF" if not text_dark else "#000000", 140)
    )
    fdraw.text((left_x + badge_h * 0.8, badge_y + (badge_h - (bbox_badge[3] - bbox_badge[1])) // 2 - 2),
               badge_text, fill=text_color, font=badge_font)

    # big text
    big_y = top_y + badge_h + HEIGHT * 0.07
    big_font = ImageFont.truetype(FONT_BOLD, 84)
    bbox_big = fdraw.textbbox((0, 0), big_text, font=big_font)
    max_w = WIDTH * 0.46
    if bbox_big[2] - bbox_big[0] > max_w:
        scale = max_w / (bbox_big[2] - bbox_big[0])
        new_size = max(52, int(84 * scale))
        big_font = ImageFont.truetype(FONT_BOLD, new_size)
        bbox_big = fdraw.textbbox((0, 0), big_text, font=big_font)
    fdraw.text((left_x, big_y), big_text, fill=text_color, font=big_font)

    # EN label
    en_font = ImageFont.truetype(FONT_EN, 26)
    en_y = big_y + (bbox_big[3] - bbox_big[1]) + HEIGHT * 0.05
    spacing = 13
    cur_x = left_x
    for ch in label_en:
        fdraw.text((cur_x, en_y), ch, fill=text_color, font=en_font)
        bw = fdraw.textbbox((0, 0), ch, font=en_font)
        cur_x += (bw[2] - bw[0]) + spacing

    # tiny sparkle
    star_c = rgba(accent, 70)
    sx = cur_x + 12
    sy = en_y + 8
    fdraw.polygon([(sx, sy - 6), (sx + 2, sy - 1), (sx + 7, sy), (sx + 2, sy + 1),
                   (sx, sy + 6), (sx - 2, sy + 1), (sx - 7, sy), (sx - 2, sy - 1)], fill=star_c)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{prefix}_{file_key}.jpg")
    img.save(path, "JPEG", quality=92)
    print("saved", path)


# 直播分类配色（按频道大类）
LIVE_COLORS = {
    "cctv": ("#8E2323", "#C0392B", "央视频道"),
    "satellite": ("#1B4B6B", "#2E86AB", "卫视频道"),
    "local": ("#1E6F5C", "#2ECC9B", "地方频道"),
    "hmt": ("#5B2C6F", "#8E44AD", "港澳台"),
}


def sync_live_logos():
    """把固定的本地台标（data/live_logos/*.png，已进 git）同步到 output/covers/live/，
    随 Pages 部署后被网页与途播引用（/covers/live/{ch_id}.png）。

    这样直播台标彻底摆脱易失效的外链 CDN——即便源站 logo 挂了，本地副本仍在。
    """
    src = os.path.join(ROOT, "data", "live_logos")
    if not os.path.isdir(src):
        return
    dst = os.path.join(OUTPUT_DIR, "live")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for fn in os.listdir(src):
        if not fn.endswith(".png"):
            continue
        s = os.path.join(src, fn)
        d = os.path.join(dst, fn)
        if not os.path.exists(d) or os.path.getsize(d) != os.path.getsize(s):
            shutil.copy2(s, d)
            n += 1
    if n:
        print("台标同步: %d 个新增/更新 -> %s" % (n, dst))


def generate_live_covers():
    """为每个去重后的直播频道生成台标风格封面（guovin 外链 logo 常截断/失效）。

    封面文件名 live_{ch_id}.jpg 必须与 generate_movies_json.build_live 中的
    ch_id 一致：ch_id = "l_" + md5("cat|name")[:14]，且封面路径写入 /covers/live_{ch_id}.jpg。

    注意：优先使用的是 fetch_live_logos.py 下载到 data/live_logos/ 的真实台标
    （已进 git、部署时由 sync_live_logos 同步到 output/covers/live/），本函数生成的
    渐变封面仅作为「未采集到台标」频道的兜底。
    """
    import hashlib
    from collector.live import list_live

    # 先把固定的本地台标同步到部署目录（供网页/途播引用）
    sync_live_logos()

    rows = list_live()
    seen = set()
    count = 0
    for r in rows:
        cat = r.get("category", "")
        name = r.get("name", "")
        display = r.get("display") or name
        key = (cat, name)
        if key in seen:
            continue
        seen.add(key)
        ch_id = "l_" + hashlib.md5(("%s|%s" % (cat, name)).encode("utf-8")).hexdigest()[:14]
        c1, c2, label = LIVE_COLORS.get(cat, ("#5D6D7E", "#8FA1B3", "直播"))
        # 文件名 = prefix + "_" + file_key = "live_" + ch_id = "live_l_<hex>.jpg"
        # 与 generate_movies_json.build_live 的封面路径 /covers/live_{ch_id}.jpg 对应。
        generate("直播", display, c1, c2, False, "LIVE", "cat", ch_id, prefix="live")
        count += 1
    print("直播封面生成完成: %d 个频道" % count)

    # 同时生成带真实台标的卡片版封面，供网格视图使用
    generate_live_card_covers()


def generate_live_card_covers():
    """为有真实台标的直播频道生成卡片版封面（800x450）。

    途播网格视图会请求较大尺寸封面并把原图拉伸铺满，真实台标（尤其是 CCTV
    等细长条 logo）会被放得巨大。卡片版把真实 logo 等比居中、留渐变背景，
    比例与网格卡片匹配，避免拉伸变形。

    输出：output/covers/live/{ch_id}_card.jpg
    """
    import hashlib
    from collector.live import list_live

    logo_dir = os.path.join(ROOT, "data", "live_logos")
    if not os.path.isdir(logo_dir):
        return

    out_dir = os.path.join(OUTPUT_DIR, "live")
    os.makedirs(out_dir, exist_ok=True)

    rows = list_live()
    seen = set()
    count = 0
    for r in rows:
        cat = r.get("category", "")
        name = r.get("name", "")
        display = r.get("display") or name
        key = (cat, name)
        if key in seen:
            continue
        seen.add(key)
        ch_id = "l_" + hashlib.md5(("%s|%s" % (cat, name)).encode("utf-8")).hexdigest()[:14]
        logo_path = os.path.join(logo_dir, ch_id + ".png")
        if not os.path.exists(logo_path):
            continue

        c1, c2, _ = LIVE_COLORS.get(cat, ("#5D6D7E", "#8FA1B3", "直播"))
        # 800x450 横向画布，与现有渐变封面一致
        img = Image.new("RGB", (WIDTH, HEIGHT), hex_to_rgb(c1))
        draw = ImageDraw.Draw(img)
        linear_gradient(draw, WIDTH, HEIGHT, c1, c2, angle=145)

        # 叠加极淡噪点纹理，与现有风格统一
        grain = overlay()
        gd = ImageDraw.Draw(grain)
        draw_noise_texture(gd, WIDTH, HEIGHT, "#FFFFFF", 6, seed=hash(ch_id) % 100000)
        img = Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")

        try:
            logo = Image.open(logo_path).convert("RGBA")
        except Exception as e:
            print("台标打开失败 %s: %s" % (logo_path, e))
            continue

        # 等比缩放，最大宽度 520、最大高度 300，保留透明通道用于居中粘贴
        logo.thumbnail((520, 300), Image.Resampling.LANCZOS)
        lw, lh = logo.size
        x = (WIDTH - lw) // 2
        y = (HEIGHT - lh) // 2
        img.paste(logo, (x, y), logo)

        # 底部居中加频道名，便于无文字渲染的视图也能识别
        try:
            name_font = ImageFont.truetype(FONT_REG, 28)
            bbox = draw.textbbox((0, 0), display, font=name_font)
            tw = bbox[2] - bbox[0]
            tx = (WIDTH - tw) // 2
            ty = HEIGHT - 64
            # 半透明底条提升可读性
            overlay_bar = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            bd = ImageDraw.Draw(overlay_bar)
            bd.rectangle([0, ty - 10, WIDTH, HEIGHT], fill=(0, 0, 0, 80))
            img = Image.alpha_composite(img.convert("RGBA"), overlay_bar).convert("RGB")
            draw = ImageDraw.Draw(img)
            draw.text((tx, ty), display, fill="#FFFFFF", font=name_font)
        except Exception:
            pass

        out_path = os.path.join(out_dir, ch_id + "_card.jpg")
        img.save(out_path, "JPEG", quality=92)
        count += 1

    print("直播卡片封面生成完成: %d 个频道" % count)


# 统一库分类视图封面（途播 Jellyfin 后端）：电影/直播/剧集/综艺/动漫
CATEGORY_COVERS = [
    ("秦哥影视", "电影", "#1B4B6B", "#2E86AB", False, "MOVIE", "movie", "cat_movie"),
    ("秦哥影视", "直播", "#8E2323", "#C0392B", False, "LIVE", "cat", "cat_live"),
    ("秦哥影视", "剧集", "#5B2C6F", "#8E44AD", False, "SERIES", "cat", "cat_tv"),
    ("秦哥影视", "综艺", "#B9770E", "#E67E22", False, "VARIETY", "cat", "cat_variety"),
    ("秦哥影视", "动漫", "#1E6F5C", "#2ECC9B", False, "ANIME", "cat", "cat_anime"),
]


if __name__ == "__main__":
    for r, c1, c2, dark in REGIONS:
        generate("电影", r, c1, c2, dark, "MOVIE", r, r)
    for badge, big, c1, c2, dark, en, icon, fkey in CATEGORY_COVERS:
        generate(badge, big, c1, c2, dark, en, icon, fkey)
    generate_live_covers()
