"""Desenha a grade do Discover (3 colunas × 2 linhas) como uma imagem PNG."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont

COLS, ROWS = 3, 2
TILE = 360          # lado da capa
TEXT_H = 104        # área de texto abaixo da capa
PAD = 28
RADIUS = 22
BG = (17, 18, 20)
CARD = (30, 31, 34)
WHITE = (242, 243, 245)
MUTED = (163, 166, 172)
GREEN = (59, 165, 93)
AMBER = (240, 178, 50)
PLATFORM_COLORS = {"youtube": ((255, 0, 51), (120, 0, 30)), "tiktok": ((37, 244, 238), (254, 44, 85)),
                   "instagram": ((131, 58, 180), (253, 29, 29))}


@dataclass
class Tile:
    number: int
    title: str
    artist: str
    genre: str
    badge: str            # "Level 8", "New artist"…
    platform: str
    cover: bytes | None   # imagem da capa (qualquer formato) ou None
    opened: bool = False


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1 (fonte escalável)
    except TypeError:
        return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _placeholder(platform: str) -> Image.Image:
    a, b = PLATFORM_COLORS.get(platform, ((88, 101, 242), (40, 40, 60)))
    img = Image.new("RGB", (TILE, TILE))
    px = img.load()
    for y in range(TILE):
        for x in range(TILE):
            t = (x + y) / (2 * TILE)
            px[x, y] = tuple(int(a[i] * (1 - t) * 0.55 + b[i] * t * 0.55) for i in range(3))
    d = ImageDraw.Draw(img)
    f = _font(40)
    label = platform.capitalize()
    w = d.textlength(label, font=f)
    d.text(((TILE - w) / 2, TILE / 2 - 24), label, font=f, fill=(255, 255, 255))
    return img


def _cover(t: Tile) -> Image.Image:
    img = None
    if t.cover:
        try:
            img = Image.open(io.BytesIO(t.cover)).convert("RGB")
        except Exception:  # noqa: BLE001
            img = None
    if img is None:
        return _placeholder(t.platform)
    # recorte quadrado centralizado
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    return img.resize((TILE, TILE), Image.LANCZOS)


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img, (0, 0), mask)
    return out


def render(tiles: list[Tile]) -> bytes:
    width = COLS * TILE + (COLS + 1) * PAD
    height = ROWS * (TILE + TEXT_H) + (ROWS + 1) * PAD
    canvas = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(canvas)
    f_num, f_title, f_sub, f_badge = _font(26), _font(28), _font(21), _font(20)

    for i, t in enumerate(tiles[:COLS * ROWS]):
        col, row = i % COLS, i // COLS
        x = PAD + col * (TILE + PAD)
        y = PAD + row * (TILE + TEXT_H + PAD)
        cover = _cover(t)
        if t.opened:
            cover = cover.filter(ImageFilter.GaussianBlur(2))
            cover = Image.blend(cover, Image.new("RGB", cover.size, (0, 0, 0)), 0.45)
        canvas.paste(_rounded(cover, RADIUS), (x, y), _rounded(cover, RADIUS))

        # número no canto da capa
        d.rounded_rectangle((x + 14, y + 14, x + 62, y + 58), 12, fill=(0, 0, 0))
        n = str(t.number)
        d.text((x + 38 - d.textlength(n, font=f_num) / 2, y + 20), n, font=f_num, fill=WHITE)
        if t.opened:
            label = "OPENED"
            lw = d.textlength(label, font=f_badge)
            d.rounded_rectangle((x + TILE - lw - 44, y + 14, x + TILE - 14, y + 52), 12, fill=GREEN)
            d.text((x + TILE - lw - 29, y + 22), label, font=f_badge, fill=WHITE)

        ty = y + TILE + 14
        d.text((x + 2, ty), _fit(d, t.title, f_title, TILE - 4), font=f_title, fill=WHITE)
        d.text((x + 2, ty + 38), _fit(d, f"{t.artist} · {t.genre}", f_sub, TILE - 4), font=f_sub, fill=MUTED)
        color = AMBER if t.badge.lower().startswith("new") else GREEN
        d.ellipse((x + 3, ty + 74, x + 15, ty + 86), fill=color)
        d.text((x + 22, ty + 68), _fit(d, t.badge, f_badge, TILE - 30), font=f_badge, fill=color)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------- quadro único (capa em cima, texto embaixo) para a galeria do Discord ----------
SQ = 600          # quadrado: a galeria do Discord recorta em quadrado, então o texto vai dentro dele
COVER_H = 420


def render_tile(t: Tile) -> bytes:
    canvas = Image.new("RGB", (SQ, SQ), CARD)
    d = ImageDraw.Draw(canvas)
    cover = _cover(t).resize((SQ, SQ), Image.LANCZOS)
    top = (SQ - COVER_H) // 2
    canvas.paste(cover.crop((0, top, SQ, top + COVER_H)), (0, 0))
    f_num, f_title, f_sub, f_badge = _font(34), _font(38), _font(27), _font(26)
    d.rounded_rectangle((18, 18, 82, 76), 14, fill=(0, 0, 0))
    n = str(t.number)
    d.text((50 - d.textlength(n, font=f_num) / 2, 27), n, font=f_num, fill=WHITE)
    x, y = 26, COVER_H + 18
    d.text((x, y), _fit(d, t.title, f_title, SQ - 2 * x), font=f_title, fill=WHITE)
    d.text((x, y + 52), _fit(d, f"{t.artist} · {t.genre}", f_sub, SQ - 2 * x), font=f_sub, fill=MUTED)
    color = AMBER if t.badge.lower().startswith("new") else GREEN
    d.ellipse((x + 2, y + 104, x + 18, y + 120), fill=color)
    d.text((x + 28, y + 97), t.badge, font=f_badge, fill=color)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
