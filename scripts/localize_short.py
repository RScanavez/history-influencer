"""Gera a versão de um Short em outro idioma trocando só a narração do mesmo vídeo.

Uso: python scripts/localize_short.py <pasta-do-video> <traducao.json>

A tradução tem "language" (ex.: "fr"), "title", "description", "hashtags", "tags", "pinned_comment" e
"scenes": [{"scene": número da cena original, "narration": ...}], as mesmas cenas do short.mp4.
O vídeo é o mesmo; só a narração é trocada.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_job  # noqa: E402,F401  (carrega o ambiente do .mcp.json e do registro)

from influencer_mcp.localize import build_localized_short  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    video_dir, translation_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    translation = json.loads(translation_path.read_text(encoding="utf-8"))
    result = build_localized_short(video_dir, translation, progress=lambda message: print(message, flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
