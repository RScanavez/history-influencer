"""Remonta um vídeo pronto (PT e FR/EN) só com os trechos já animados, sem GPU.

Uso: python scripts/remontar_video.py <pasta-do-video>
Chamado pelo painel depois de remover uma cena na revisão.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_job  # noqa: E402,F401  (carrega o ambiente do .mcp.json e do registro)

from influencer_mcp.motion import remount_from_segments  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    result = remount_from_segments(Path(sys.argv[1]).resolve(), progress=lambda message: print(message, flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
