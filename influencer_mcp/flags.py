"""Flags de `- chave: valor` do fluxo_aprovacao.md: leitura única para idiomas, biblioteca de exercícios e limites.

Lê o arquivo a cada chamada (o usuário edita à mão com o painel aberto) e usa `media.PROJECT_ROOT` na hora, para os
testes poderem trocar a raiz do projeto.
"""

from __future__ import annotations

import re

OFF_VALUES = ("nao", "não", "no", "desligada", "desligado", "false", "0")


def flag(name: str) -> str | None:
    """Valor da linha "- <name>: valor" (sem espaços nas pontas), ou None se o arquivo ou a linha não existirem."""
    from . import media

    path = media.PROJECT_ROOT / "fluxo_aprovacao.md"
    if not path.is_file():
        return None
    match = re.search(rf"^-\s*{re.escape(name)}:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def flag_enabled(name: str, default: bool = True) -> bool:
    """Flag sim/não: desligada com "não", "desligada" etc.; sem a linha vale `default`."""
    value = flag(name)
    return default if value is None else value.strip().lower() not in OFF_VALUES
