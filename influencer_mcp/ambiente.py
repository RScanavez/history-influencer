"""Verificação do ambiente onde o app roda. Na abertura do painel conferimos a GPU local (Apple Silicon via Metal/MPS
ou NVIDIA via nvidia-smi) e se o ComfyUI está instalado. Se não, a fila de vídeos fica indisponível e o painel explica.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .winproc import quiet, venv_python

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def apple_chip_name() -> str:
    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or "Apple Silicon"
    except (OSError, subprocess.SubprocessError):
        return "Apple Silicon"


def _nvidia_gpus() -> tuple[list[str], str | None]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return [], "nvidia-smi não encontrado (sem placa NVIDIA ou sem o driver da NVIDIA instalado)."
    try:
        result = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=15, **quiet())
    except (OSError, subprocess.SubprocessError) as error:
        return [], f"nvidia-smi falhou: {error}"
    gpus = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not gpus:
        return [], f"nvidia-smi não listou nenhuma GPU ({(result.stderr or result.stdout).strip()[:200]})."
    return gpus, None


def local_gpu() -> dict[str, Any]:
    """GPU local utilizável: Apple Silicon (Metal/MPS) ou placa NVIDIA, e o ComfyUI instalado com ambiente Python."""
    from .comfyui import comfyui_dir

    if apple_silicon():
        gpus, problems = [f"{apple_chip_name()} (GPU integrada, memória unificada)"], []
    else:
        gpus, problem = _nvidia_gpus()
        problems = [problem] if problem else []
    folder = comfyui_dir()
    has_python = any(venv_python(folder / name).is_file() for name in ("venv-v037", "venv"))
    if not (folder / "main.py").is_file() or not has_python:
        problems.append(f"ComfyUI não encontrado em {folder} (defina COMFYUI_DIR ou instale com ./install.sh --comfyui).")
    return {"available": not problems, "gpus": gpus, "problems": problems}


@lru_cache(maxsize=1)
def local_gpu_cached() -> dict[str, Any]:
    """Uma verificação por processo."""
    return local_gpu()


def check_all() -> dict[str, Any]:
    return {"checkedAt": datetime.now(UTC).isoformat(timespec="seconds"), "local": local_gpu()}


def system_usage() -> dict[str, Any]:
    """Uso de CPU, memória e GPU para os medidores do painel (pedido de 2026-10-06). Leve: roda a cada poucos segundos.

    GPU NVIDIA via nvidia-smi; no Apple Silicon a GPU divide a memória do sistema (sem medidor próprio), então "gpu" vem vazio.
    """
    import psutil

    memory = psutil.virtual_memory()
    # Janela curta: com interval=None o psutil compara com a chamada anterior da mesma thread, e cada requisição
    # do painel chega numa thread nova (sempre 0%).
    usage: dict[str, Any] = {"cpu": psutil.cpu_percent(interval=0.5), "memory": memory.percent,
                             "memoryUsedGb": round(memory.used / 2**30, 1), "memoryTotalGb": round(memory.total / 2**30, 1),
                             "gpu": None}
    exe = shutil.which("nvidia-smi")
    if not exe:
        return usage
    try:
        result = subprocess.run([exe, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                                **quiet())
        name, util, temp, used, total = [field.strip() for field in result.stdout.splitlines()[0].split(",")]
        usage["gpu"] = {"name": name, "utilization": float(util), "temperature": float(temp),
                        "vramUsedGb": round(float(used) / 1024, 1), "vramTotalGb": round(float(total) / 1024, 1)}
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        pass
    return usage
