"""Garante que o ComfyUI local esteja no ar: verifica a porta e, se estiver fora, sobe o servidor e espera responder."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .media import _request_json
from .winproc import detached, quiet

STARTUP_TIMEOUT = 300  # carregar os nós personalizados leva ~35 s; folga para disco frio após reiniciar o PC


def comfyui_dir() -> Path:
    return Path(os.environ.get("COMFYUI_DIR") or r"D:\Influencer\tools\ComfyUI")


def is_up(base: str) -> bool:
    try:
        _request_json(f"{base}/system_stats", timeout=10)
        return True
    except RuntimeError:
        return False


GPU_PROFILES = Path(__file__).resolve().parent.parent / "perfis_gpu.json"


def gpu_name() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        result = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                                timeout=10, **quiet())
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def gpu_profile(name: str | None = None) -> tuple[str, dict]:
    """Perfil de perfis_gpu.json para esta placa (2026-10-08: a 3070 e a 5070 precisam de flags diferentes).
    INFLUENCER_GPU_PROFILE força um perfil; sem perfil que combine com o nome da GPU, vale "padrao"."""
    profiles = {key: value for key, value in json.loads(GPU_PROFILES.read_text(encoding="utf-8")).items()
                if not key.startswith("_")}
    forced = os.environ.get("INFLUENCER_GPU_PROFILE", "").strip()
    if forced:
        if forced not in profiles:
            raise RuntimeError(f"INFLUENCER_GPU_PROFILE={forced} não existe em {GPU_PROFILES.name}: {', '.join(profiles)}.")
        return forced, profiles[forced]
    name = (gpu_name() if name is None else name).lower()
    # Todas as placas, não só a primeira linha: computador com duas GPUs lista as duas.
    for key, profile in profiles.items():
        if profile.get("gpu") and profile["gpu"].lower() in name:
            return key, profile
    return "padrao", profiles["padrao"]


def speed_flags(python: Path) -> list[str]:
    """Aceleração da GPU conforme o perfil da placa (perfis_gpu.json). O SageAttention só entra se o perfil pedir e
    estiver instalado no ambiente do ComfyUI (pip install "triton-windows<3.8" sageattention); COMFYUI_SPEED=0
    desliga tudo, para comparar qualidade ou contornar um problema."""
    if os.environ.get("COMFYUI_SPEED", "1").strip() == "0":
        return []
    _, profile = gpu_profile()
    flags = ["--fast", *profile["comfyuiFast"]] if profile.get("comfyuiFast") else []
    if profile.get("sageAttention"):
        probe = subprocess.run([str(python), "-c", "import sageattention, triton"], capture_output=True, timeout=60,
                               **quiet())
        if probe.returncode == 0:
            flags.append("--use-sage-attention")
    return flags


LOCAL_URLS = ("http://127.0.0.1:8188", "http://localhost:8188")


def discard_outputs(base: str, items: list[dict]) -> None:
    """Apaga, no ComfyUI local, as imagens que o job já baixou para a pasta do vídeo. Ficavam para sempre em
    tools/ComfyUI/output: ~160 PNG em HD por clipe encheram o disco D: com 372 GB (2026-10-08). Na GPU remota
    (túnel em outra porta) não faz nada."""
    if base.rstrip("/") not in LOCAL_URLS:
        return
    root = comfyui_dir()
    for item in items:
        path = root / item.get("type", "output") / item.get("subfolder", "") / item["filename"]
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # arquivo preso por outro processo: fica para a próxima limpeza manual


def launch() -> int:
    """Inicia o ComfyUI destacado do processo atual (sobrevive ao fim do job ou do terminal) e devolve o pid."""
    folder = comfyui_dir()
    # venv-v037 é o ambiente da instalação original; o install.ps1 cria "venv".
    python = next(
        (folder / name / "Scripts" / "python.exe" for name in ("venv-v037", "venv")
         if (folder / name / "Scripts" / "python.exe").is_file()),
        None,
    )
    if python is None:
        raise RuntimeError(f"Ambiente Python do ComfyUI não encontrado em {folder}. Execute install.ps1 ou defina COMFYUI_DIR.")
    log = open(folder / "comfyui.log", "ab")  # noqa: SIM115 - herdado pelo processo filho
    command = [str(python), "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch"]
    device = os.environ.get("COMFYUI_CUDA_DEVICE", "").strip()
    if device.isdigit():  # computador com mais de uma GPU: a escolhida pelo instalador (a de mais memória)
        command += ["--cuda-device", device]
    command += speed_flags(python)
    args = dict(
        args=command,
        cwd=str(folder), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, close_fds=True,
    )
    try:
        process = subprocess.Popen(**args, **detached(breakaway=True))
    except OSError:
        process = subprocess.Popen(**args, **detached())
    return process.pid


PORT = 8188


def _listening_pid(port: int = PORT) -> int:
    """Pid que escuta a porta local do ComfyUI (0 se ninguém escuta)."""
    result = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, **quiet())
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}") and parts[4].isdigit():
            return int(parts[4])
    return 0


def stop() -> bool:
    """Desliga o ComfyUI local (e os processos filhos). Devolve False se ele não estava rodando."""
    from .motion_jobs import _pid_alive  # evita import circular

    pid_file = comfyui_dir() / "comfyui.pid"
    pid = int(pid_file.read_text() or 0) if pid_file.is_file() else 0
    if not _pid_alive(pid):
        pid = _listening_pid()  # subido por outro meio (ou pid velho): quem escuta a porta é o ComfyUI
    if not pid:
        return False
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **quiet())
    pid_file.unlink(missing_ok=True)
    return True


def ensure_running(base: str, *, timeout: int = STARTUP_TIMEOUT, log=None) -> bool:
    """Devolve True se o ComfyUI já estava no ar; se não, inicia e espera. Falha com erro claro se não subir."""
    if os.environ.get("INFLUENCER_GPU") == "remota":
        # Job da GPU remota: nunca sobe o ComfyUI local; liga a máquina e o túnel, se preciso.
        from .gpu_remota import ensure_ready

        was_up = is_up(base)
        ensure_ready(log=log)
        return was_up
    if is_up(base):
        return True
    from .motion_jobs import _pid_alive  # evita import circular

    pid_file = comfyui_dir() / "comfyui.pid"
    previous = int(pid_file.read_text() or 0) if pid_file.is_file() else 0
    # Outro job pode ter acabado de subir o ComfyUI; nesse caso só espera, sem abrir um segundo servidor.
    if not _pid_alive(previous):
        pid = launch()
        pid_file.write_text(str(pid))
        if log:
            log(f"ComfyUI estava fora do ar; iniciado (pid {pid}).")
    else:
        pid = previous
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(5)
        if is_up(base):
            if log:
                log("ComfyUI respondendo.")
            return False
        if not _pid_alive(pid):
            raise RuntimeError(
                f"O ComfyUI encerrou logo após iniciar; veja {comfyui_dir() / 'comfyui.log'}."
            )
    raise RuntimeError(f"O ComfyUI não respondeu em {timeout} s após iniciar; veja {comfyui_dir() / 'comfyui.log'}.")
