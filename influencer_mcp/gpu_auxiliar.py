"""Segunda GPU (ex.: AMD Radeon 780M integrada ao lado da RTX) fazendo a interpolação RIFE 2x dos clipes.

Só entra em vigor quando há DUAS GPUs: o rife-ncnn-vulkan instalado (RIFE_NCNN_DIR, ou a pasta rife-ncnn-vulkan
ao lado do ComfyUI; o install.ps1 baixa só em máquinas com duas placas) e, entre as placas Vulkan que ele enxerga,
uma que não seja NVIDIA (a NVIDIA é a do ComfyUI). Com uma GPU só, na GPU remota, sem o programa ou se o teste de
abertura falhar, nada muda: o RIFE continua dentro do workflow do ComfyUI. INFLUENCER_RIFE_AUX=0 desliga.

Por que (2026-10-11, ASUS TUF A16): o RIFE saía da RTX a cada clipe; na 780M ele roda em segundo plano (~7 s por
clipe de 49 quadros 960x1664) enquanto a RTX já anima o clipe seguinte. Qualidade x rife49 da RTX: PSNR médio 40 dB.
O rife-ncnn-vulkan numera as placas na ordem do Vulkan (na 780M + RTX 5070: 0 = AMD, 1 = NVIDIA), por isso o
número sai da lista que ele mesmo imprime. Não use a opção -n: ela desalinha os quadros no meio do clipe; o modo
padrão (2x) mantém os originais e devolve 2N quadros, o último repetido.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .winproc import quiet

MODEL = "rife-v4.6"
SOFTWARE = ("llvmpipe", "swiftshader", "microsoft basic", "lavapipe")
PROBE_TIMEOUT = 120
CLIP_TIMEOUT = 15 * 60


@dataclass(frozen=True)
class Device:
    index: int
    name: str


@dataclass(frozen=True)
class Auxiliary:
    executable: Path
    model: Path
    device: Device
    fallback: Device | None  # a NVIDIA pelo Vulkan, se a placa auxiliar falhar no meio de um clipe


def rife_dir() -> Path | None:
    configured = os.environ.get("RIFE_NCNN_DIR", "").strip()
    if configured:
        return Path(configured)
    comfy = os.environ.get("COMFYUI_DIR", "").strip()
    return Path(comfy).parent / "rife-ncnn-vulkan" if comfy else None


def _executable(folder: Path) -> Path:
    return folder / ("rife-ncnn-vulkan.exe" if sys.platform == "win32" else "rife-ncnn-vulkan")


def parse_devices(log: str) -> list[Device]:
    """Placas que o ncnn lista ao abrir ("[0 AMD Radeon 780M Graphics]  queueC=..."), sem as de software."""
    found: dict[int, str] = {}
    for index, name in re.findall(r"^\[(\d+) (.+?)\]\s+queueC=", log, re.M):
        found.setdefault(int(index), name.strip())
    return [Device(index, name) for index, name in sorted(found.items())
            if not any(word in name.lower() for word in SOFTWARE)]


def choose(devices: list[Device]) -> tuple[Device, Device | None] | None:
    """Placa auxiliar (a primeira não NVIDIA) e a NVIDIA de reserva; None se não houver duas placas."""
    if len(devices) < 2:
        return None
    others = [device for device in devices if "nvidia" not in device.name.lower()]
    if not others:
        return None  # duas NVIDIA: o ComfyUI pode estar em qualquer uma; não arrisca
    fallback = next((device for device in devices if "nvidia" in device.name.lower()), None)
    return others[0], fallback


def _run(executable: Path, model: Path, source: Path, destination: Path, device: int, timeout: int):
    return subprocess.run(
        [str(executable), "-i", str(source), "-o", str(destination), "-m", str(model), "-g", str(device),
         "-f", "%08d.png"],
        capture_output=True, text=True, errors="replace", timeout=timeout, **quiet(),
    )


@functools.lru_cache(maxsize=1)
def _detect() -> Auxiliary | None:
    folder = rife_dir()
    if folder is None:
        return None
    executable, model = _executable(folder), folder / MODEL
    if not executable.is_file() or not model.is_dir():
        return None
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="rife-teste-") as temp:
        source, output = Path(temp) / "in", Path(temp) / "out"
        source.mkdir()
        output.mkdir()
        for number, color in ((1, (40, 40, 40)), (2, (200, 200, 200))):
            Image.new("RGB", (64, 64), color).save(source / f"{number:08d}.png")
        try:
            listing = _run(executable, model, source, output, 0, PROBE_TIMEOUT)
            chosen = choose(parse_devices(listing.stdout + listing.stderr))
            if chosen is None:
                return None
            device, fallback = chosen
            shutil.rmtree(output)
            output.mkdir()
            test = _run(executable, model, source, output, device.index, PROBE_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        if test.returncode != 0 or len(list(output.glob("*.png"))) < 3:
            return None
    return Auxiliary(executable, model, device, fallback)


def auxiliary() -> Auxiliary | None:
    """A segunda GPU pronta para o RIFE, ou None (uma GPU só, GPU remota, desligado ou teste falhou)."""
    if os.environ.get("INFLUENCER_RIFE_AUX") == "0" or os.environ.get("INFLUENCER_GPU") == "remota":
        return None
    return _detect()


def interpolate(aux: Auxiliary, source: Path, destination: Path, names: list[str], skip_first: bool) -> str:
    """RIFE 2x dos quadros de `source` (ordenados pelo nome) gravados em `destination` com `names`.

    N quadros viram 2N-1 (o último repetido pelo ncnn sai); skip_first tira o primeiro (clipe encadeado, que
    repete o último do anterior). Devolve o nome da placa que fez o trabalho."""
    frames = sorted(source.glob("*.png"))
    expected = 2 * len(frames) - 1 - (1 if skip_first else 0)
    if len(names) != expected:
        raise ValueError(f"Interpolação: {len(names)} nomes para {expected} quadros.")
    staging = source.with_name(source.name + "-numerado")
    output = source.with_name(source.name + "-rife")
    errors = []
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for number, frame in enumerate(frames, 1):
            frame.replace(staging / f"{number:08d}.png")
        for device in [aux.device] + ([aux.fallback] if aux.fallback else []):
            shutil.rmtree(output, ignore_errors=True)
            output.mkdir()
            try:
                result = _run(aux.executable, aux.model, staging, output, device.index, CLIP_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as error:
                errors.append(f"{device.name}: {error}")
                continue
            produced = sorted(output.glob("*.png"))
            if result.returncode == 0 and len(produced) >= 2 * len(frames) - 1:
                produced = produced[:2 * len(frames) - 1][1 if skip_first else 0:]
                for frame, name in zip(produced, names):
                    frame.replace(destination / name)
                return device.name
            errors.append(f"{device.name}: código {result.returncode}, {len(produced)} quadros; "
                          f"{(result.stderr or '').strip()[-300:]}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)
        shutil.rmtree(source, ignore_errors=True)
    raise RuntimeError("RIFE na segunda GPU falhou: " + " | ".join(errors))
