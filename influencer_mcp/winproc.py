"""Opções de subprocess para nenhum processo do projeto abrir janela de console no Windows.

CREATE_NO_WINDOW cria o console oculto (os filhos herdam). Não usar DETACHED_PROCESS: sem console para herdar, cada
filho de console (o python.exe de um venv é só um launcher que dispara o Python real) abre uma janela nova.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

NO_WINDOW = 0x08000000
NEW_PROCESS_GROUP = 0x00000200
BREAKAWAY_FROM_JOB = 0x01000000


def quiet() -> dict[str, Any]:
    """Para subprocess.run/check_output: roda sem abrir janela."""
    return {"creationflags": NO_WINDOW} if os.name == "nt" else {}


def detached(*, breakaway: bool = False) -> dict[str, Any]:
    """Para subprocess.Popen de processo que sobrevive ao chamador, sem janela."""
    if os.name != "nt":
        return {"start_new_session": True}
    hidden = subprocess.STARTUPINFO()
    hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    hidden.wShowWindow = subprocess.SW_HIDE
    flags = NO_WINDOW | NEW_PROCESS_GROUP | (BREAKAWAY_FROM_JOB if breakaway else 0)
    return {"creationflags": flags, "startupinfo": hidden}
