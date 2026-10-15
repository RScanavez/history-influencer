"""Formatos de entrega: Short (vertical, publicado pelo painel) e Vídeo (horizontal 16:9, publicação manual).

Decisões do usuário (2026-10-02): o formato é escolhido por lote (seletor no painel e argumento do /gerar-lote);
mesma duração do Short (60 a 90 s); o vídeo leva capa 1280x720 baseada no assunto central; a publicação de
vídeos é manual no YouTube Studio, a de Shorts continua como antes. Entrega em output/shorts e output/videos.
"""

from __future__ import annotations

import json
from pathlib import Path

FORMATS = {
    "short": {
        "label": "Short", "frame": (1080, 1920), "keyframe": (768, 1344), "wan": (480, 832), "folder": "shorts",
        "composition": "vertical composition",
    },
    "video": {
        "label": "Vídeo", "frame": (1920, 1080), "keyframe": (1344, 768), "wan": (832, 480), "folder": "videos",
        "composition": "horizontal widescreen 16:9 composition, the man centered with room around him",
    },
}
DEFAULT_FORMAT = "short"
THUMBNAIL_SIZE = (1280, 720)


def normalize(value: str | None) -> str:
    name = (value or DEFAULT_FORMAT).strip().lower().replace("í", "i")
    if name in ("short", "shorts"):
        return "short"
    if name in ("video", "videos"):
        return "video"
    raise ValueError(f"Formato desconhecido: {value!r} (use short ou video).")


def of_script(script: dict) -> str:
    return normalize(script.get("format"))


def of_folder(folder: Path) -> str:
    try:
        return of_script(json.loads((folder / "motion-script.json").read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_FORMAT


def output_dir(fmt: str) -> Path:
    from .media import GENERATED_ROOT

    return GENERATED_ROOT.parent / "output" / FORMATS[normalize(fmt)]["folder"]
