"""Verificação do ambiente onde o app roda. Na abertura do painel conferimos a GPU local (Apple Silicon via Metal/MPS
ou NVIDIA via nvidia-smi) e se o ComfyUI está instalado. Se não, a fila de vídeos fica indisponível e o painel explica.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .sensores_mac import chip_temperature
from .winproc import quiet, venv_python

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# Alerta de temperatura do chip da Apple: sob carga contínua (Wan) o die passa dos 80 °C sem problema e o sistema
# só reduz a velocidade perto de 100-105 °C; 85 °C (limite da NVIDIA) alertaria o tempo todo.
APPLE_HOT_CELSIUS = 95


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


def memory_pressure() -> str:
    """Pressão de memória do macOS (a mesma do Activity Monitor): normal, atenção ou crítica."""
    try:
        level = int(subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True,
                                   text=True, timeout=5).stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return "normal"
    return {1: "normal", 2: "atenção", 4: "crítica"}.get(level, "normal")


def apple_gpu_usage(total_memory_gb: float) -> dict[str, Any] | None:
    """Uso da GPU do Apple Silicon lido do IOAccelerator (ioreg não pede sudo). A GPU usa a memória unificada, então
    "vram" é a memória do sistema alocada para ela. A temperatura é a do die do chip (sensores_mac, sem sudo); o
    estado térmico (`pmset -g therm`) diz se o sistema está reduzindo a velocidade ("limitada")."""
    try:
        text = subprocess.run(["ioreg", "-r", "-d", "1", "-w0", "-c", "IOAccelerator"], capture_output=True, text=True,
                              timeout=5).stdout
        stats = dict(re.findall(r'"([^"]+)"=(\d+)', re.search(r'"PerformanceStatistics" = \{([^}]*)\}', text).group(1)))
        thermal = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError, AttributeError):
        return None
    limited = any(int(value) < 100 for value in re.findall(r"(?:CPU_Speed_Limit|GPU_Speed_Limit)\s*=\s*(\d+)", thermal))
    return {"name": apple_chip_name(), "utilization": float(stats.get("Device Utilization %", 0)),
            "temperature": chip_temperature(),
            "hotAt": APPLE_HOT_CELSIUS,
            "thermal": "limitada" if limited else "ok",
            "vramUsedGb": round(int(stats.get("Alloc system memory", 0)) / 2**30, 1), "vramTotalGb": total_memory_gb}


def system_usage() -> dict[str, Any]:
    """Uso de CPU, memória e GPU para os medidores do painel (pedido de 2026-10-06). Leve: roda a cada poucos segundos.

    GPU NVIDIA via nvidia-smi; Apple Silicon via ioreg (sem sudo).
    """
    import psutil

    memory = psutil.virtual_memory()
    # Janela curta: com interval=None o psutil compara com a chamada anterior da mesma thread, e cada requisição
    # do painel chega numa thread nova (sempre 0%).
    usage: dict[str, Any] = {"cpu": psutil.cpu_percent(interval=0.5), "memory": memory.percent,
                             "memoryUsedGb": round(memory.used / 2**30, 1), "memoryTotalGb": round(memory.total / 2**30, 1),
                             "gpu": None}
    if sys.platform == "darwin":
        usage["memoryPressure"] = memory_pressure()
        usage["swapUsedGb"] = round(psutil.swap_memory().used / 2**30, 1)
    if apple_silicon():
        usage["gpu"] = apple_gpu_usage(usage["memoryTotalGb"])
        return usage
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
