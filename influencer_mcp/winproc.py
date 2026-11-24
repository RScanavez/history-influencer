"""Opções de subprocess para nenhum processo do projeto abrir janela de console no Windows (e utilidades de processo
que diferem entre Windows e macOS/Linux).

CREATE_NO_WINDOW cria o console oculto (os filhos herdam). Não usar DETACHED_PROCESS: sem console para herdar, cada
filho de console (o python.exe de um venv é só um launcher que dispara o Python real) abre uma janela nova.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
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


def tools_dir() -> Path:
    """Pasta das ferramentas pesadas (ComfyUI, Kokoro): INFLUENCER_TOOLS_DIR, senão ~/Influencer/tools no macOS/Linux
    e D:\\Influencer\\tools no Windows."""
    configured = os.environ.get("INFLUENCER_TOOLS_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(r"D:\Influencer\tools") if os.name == "nt" else Path.home() / "Influencer" / "tools"


USER_ENV_FILE = Path.home() / ".influencer.env"


def load_user_env() -> None:
    """macOS/Linux: equivalente às variáveis de ambiente do usuário do Windows. Lê ~/.influencer.env (linhas
    CHAVE=valor, gravadas pelo install.sh) sem sobrescrever o que já estiver definido."""
    if os.name == "nt" or not USER_ENV_FILE.is_file():
        return
    for line in USER_ENV_FILE.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and name and not name.startswith("#"):
            os.environ.setdefault(name, value.strip().strip('"'))


def venv_python(venv: Path) -> Path:
    """Python de um ambiente virtual (Scripts/python.exe no Windows, bin/python no macOS/Linux)."""
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def kill_tree(pid: int) -> None:
    """Encerra o processo e todos os filhos (taskkill /T /F no Windows; psutil nos demais)."""
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **quiet())
        return
    import psutil

    try:
        parent = psutil.Process(pid)
        victims = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return
    for victim in victims:
        try:
            victim.kill()
        except psutil.NoSuchProcess:
            pass
