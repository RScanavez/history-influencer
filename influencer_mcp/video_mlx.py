"""Motores de vídeo nativos do Apple Silicon (MLX), alternativos ao Wan 2.2 A14B do ComfyUI.

Benchmark de 2026-10-27 no Apple M6 (32 GB), mesma imagem e movimento, ~2,5 s de vídeo: Wan A14B no ComfyUI 922 s,
Wan 2.2 TI2V-5B q8 no MLX (20 passos) 353 s, FastWan 5B no MLX (3 passos) 167 s. O motor vem do roteiro
("videoEngine") ou da variável INFLUENCER_VIDEO_ENGINE; sem nenhum, segue o ComfyUI.

- mlx-fastwan / mlx-wan5b: scripts/mlx_wan_clip.py no ambiente do MLX (mlx-video).
- mlx-turbo: FastWan com o decodificador TAEHV e metade dos quadros preenchida pelo RIFE (técnicas do FastMetal);
  no mesmo clipe de 121 quadros, 37 s contra ~300 s do mlx-fastwan.
- ltx: LTX-2.3 destilado (q4) pelo ltx-2-mlx.
Cada clipe sai em MP4 a 24 fps; os quadros viram PNG na pasta do clipe, como os do ComfyUI, e a montagem estica os
quadros para cobrir a narração e amplia para o tamanho final.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .winproc import tools_dir

COMFYUI = "comfyui"
ENGINES = (COMFYUI, "mlx-fastwan", "mlx-turbo", "mlx-wan5b", "ltx")
FPS = 24
MAX_FRAMES = 121  # ~5 s a 24 fps; mais que isso estoura a memória de 32 GB
CLIP_TIMEOUT = 3 * 3600


def engine_of(script: dict[str, Any] | None = None) -> str:
    engine = (script or {}).get("videoEngine") or os.environ.get("INFLUENCER_VIDEO_ENGINE") or COMFYUI
    if engine not in ENGINES:
        raise ValueError(f"Motor de vídeo desconhecido: {engine!r}. Use um de: {', '.join(ENGINES)}.")
    return engine


def _mlx_dir() -> Path:
    return Path(os.environ.get("INFLUENCER_MLX_DIR") or tools_dir() / "mlx")


def _grid(engine: str) -> int:
    return 8 if engine == "ltx" else 4  # LTX: 8k+1 quadros; Wan: 4k+1


def clip_plan(engine: str, duration: float, motions: list[str], max_slowdown: float) -> list[tuple[str, int]]:
    """Clipes (movimento, quadros) que cobrem a narração; a montagem pode desacelerar até max_slowdown."""
    grid = _grid(engine)
    needed = duration * FPS / max_slowdown
    count = max(1, math.ceil(needed / (MAX_FRAMES - 1)))
    frames = min(MAX_FRAMES, grid * math.ceil(needed / count / grid) + 1)
    return [(motions[min(index, len(motions) - 1)], frames) for index in range(count)]


def size_for(fmt: str) -> tuple[int, int]:
    """(largura, altura) da geração: vertical 480x832 no Short, 832x480 no vídeo horizontal."""
    return (832, 480) if fmt == "video" else (480, 832)


def _command(engine: str, image: Path, prompt: str, negative: str, frames: int, seed: int, fmt: str,
             output: Path) -> list[str]:
    width, height = size_for(fmt)
    root = _mlx_dir()
    if engine == "ltx":
        cli = os.environ.get("LTX_MLX_CLI") or str(root / "ltx-2-mlx" / ".venv" / "bin" / "ltx-2-mlx")
        return [cli, "generate", "--distilled", "--model", str(root / "models" / "ltx-2.3-q4"),
                "--gemma", str(root / "models" / "gemma-3-12b-it-4bit"), "--image", str(image), "0", "1.0",
                "-p", prompt, "-H", str(height), "-W", str(width), "--frames", str(frames), "--frame-rate", str(FPS),
                "--no-audio", "--seed", str(seed), "-o", str(output)]
    python = os.environ.get("MLX_PYTHON") or str(root / "venv" / "bin" / "python")
    script = Path(__file__).resolve().parents[1] / "scripts" / "mlx_wan_clip.py"
    model = root / "models" / ("wan-5b-q8" if engine == "mlx-wan5b" else "fastwan-5b")
    command = [python, str(script), "--model-dir", str(model), "--image", str(image), "--prompt", prompt,
               "--width", str(width), "--height", str(height), "--frames", str(frames), "--seed", str(seed),
               "--output", str(output)]
    if engine == "mlx-turbo":
        command += ["--vendor", str(root / "vendor"), "--taehv", str(root / "models" / "taehv" / "taew2_2.pth"),
                    "--rife", str(root / "models" / "rife-4.25")]
    if engine in ("mlx-fastwan", "mlx-turbo"):
        command.append("--dmd")
    else:
        command += ["--steps", "20", "--negative", negative]
    return command


def free_comfyui_memory() -> None:
    """Descarrega os modelos do ComfyUI (SDXL das imagens-chave) antes do MLX: os dois na memória de 32 GB
    empurravam o sistema para o swap."""
    from .media import _comfyui_url, _request_json

    try:
        _request_json(f"{_comfyui_url(require_avatar_checkpoint=False)}/free",
                      {"unload_models": True, "free_memory": True}, timeout=30)
    except RuntimeError:
        pass  # ComfyUI desligado: nada a liberar


def generate_clip(engine: str, *, image: Path, prompt: str, negative: str, frames: int, seed: int, fmt: str,
                  clip_dir: Path, skip_first: bool) -> dict[str, Any]:
    """Gera um clipe e grava os quadros (hd-0001.png...) e o último (last-raw.png) em clip_dir.

    skip_first tira o primeiro quadro de um clipe encadeado (repete o último do clipe anterior)."""
    from .media import _get_executable

    clip_dir.mkdir(parents=True, exist_ok=True)
    video = clip_dir / "clip.mp4"
    log = clip_dir / "engine.log"
    ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
    # o ltx-2-mlx procura o ffmpeg no PATH; o do projeto pode estar fora dele
    path = os.pathsep.join([str(Path(ffmpeg).parent), os.environ.get("PATH", "")])
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(_command(engine, image, prompt, negative, frames, seed, fmt, video), stdout=handle,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=CLIP_TIMEOUT,
                                env={**os.environ, "PYTHONUNBUFFERED": "1", "PATH": path})
    if result.returncode != 0 or not video.is_file():
        tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"O motor {engine} falhou no clipe ({result.returncode}): {tail}")
    seconds = round(time.monotonic() - started, 1)
    frames_dir = clip_dir / "quadros"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(video),
                    str(frames_dir / "q-%04d.png")], check=True, timeout=600)
    extracted = sorted(frames_dir.glob("q-*.png"))
    if not extracted:
        raise RuntimeError(f"O motor {engine} gerou um vídeo sem quadros: {video}")
    shutil.copyfile(extracted[-1], clip_dir / "last-raw.png")
    names = []
    for index, frame in enumerate(extracted[1 if skip_first else 0:], 1):
        name = f"hd-{index:04d}.png"
        frame.replace(clip_dir / name)
        names.append(name)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return {"engine": engine, "frames": frames, "seconds": seconds, "hd": names, "last_raw": "last-raw.png"}
