"""Capa 1280x720 dos vídeos horizontais: ilustração do assunto central + texto curto em destaque."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .formats import THUMBNAIL_SIZE

FONT_CANDIDATES = ("ariblk.ttf", "arialbd.ttf", "segoeuib.ttf", "impact.ttf")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in FONT_CANDIDATES:
        path = folder / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def compose_thumbnail(base_image: Path, text: str, destination: Path) -> Path:
    """Ajusta a ilustração para 16:9, escurece a faixa da esquerda e escreve o texto (até 3 linhas)."""
    width, height = THUMBNAIL_SIZE
    with Image.open(base_image) as opened:
        canvas = ImageOps.fit(opened.convert("RGB"), (width, height))
    shade = Image.new("L", (width, height), 0)
    shade_draw = ImageDraw.Draw(shade)
    for x in range(width // 2 + 120):
        # Gradiente: escuro à esquerda, transparente perto do centro, para o texto ler bem.
        shade_draw.line([(x, 0), (x, height)], fill=int(190 * max(0.0, 1 - x / (width / 2 + 120))))
    canvas = Image.composite(Image.new("RGB", (width, height), (10, 20, 40)), canvas, shade)
    draw = ImageDraw.Draw(canvas)
    text = " ".join((text or "").split()).upper()
    size = 96
    while size > 40:
        font = _font(size)
        lines = _wrap(draw, text, font, int(width * 0.52))
        if len(lines) <= 3:
            break
        size -= 8
    lines = lines[:3]
    line_height = int(size * 1.12)
    top = (height - line_height * len(lines)) // 2
    for index, line in enumerate(lines):
        draw.text((60, top + index * line_height), line, font=font, fill=(255, 214, 64),
                  stroke_width=max(3, size // 16), stroke_fill=(0, 0, 0))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="JPEG", quality=92)
    return destination
