"""Fila de jobs de vídeo com movimento: processos em segundo plano, um por vez na GPU."""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .media import GENERATED_ROOT, PROJECT_ROOT
from .winproc import detached, quiet

JOBS_DIR = GENERATED_ROOT / "jobs"
# Trava da GPU: INFLUENCER_GPU_LOCK aponta para um arquivo comum quando dois projetos dividem a mesma placa
# (2026-10-15: Influencer e Historiador); sem a variável, cada projeto usa a sua.
LOCK_PATH = Path(os.environ["INFLUENCER_GPU_LOCK"]) if os.environ.get("INFLUENCER_GPU_LOCK") else JOBS_DIR / "gpu.lock"
KINDS = ("keyframes", "animation")
# GPU de cada job (pedido do usuário, 2026-10-03): "local" (este PC) ou "remota" (RunPod). Cada uma tem fila e trava
# próprias e as duas rodam ao mesmo tempo. Vídeos sem a marcação (todos os anteriores) ficam na local.
TARGETS = ("local", "remota")
_TARGET = "local"  # GPU do job deste processo (um processo = um job)


def job_target(job: dict[str, Any]) -> str:
    params = job.get("params", {})
    return params.get("gpu") if params.get("gpu") in TARGETS else "local"


def resolve_target(params: dict[str, Any]) -> str:
    """GPU de um job novo: a pedida, senão a do roteiro/vídeo, senão a escolhida no lote; padrão local."""
    if params.get("gpu") in TARGETS:
        return params["gpu"]
    script = params.get("script") or {}
    if script.get("gpu") in TARGETS:
        return script["gpu"]
    batch = params.get("batch_id")
    slug = params.get("video_slug") or script.get("slug")
    if batch and slug:
        for path in GENERATED_ROOT.glob(f"*/{batch}/{slug}/motion-script.json"):
            try:
                saved = json.loads(path.read_text(encoding="utf-8")).get("gpu")
            except (OSError, json.JSONDecodeError):
                continue
            if saved in TARGETS:
                return saved
    lotes = GENERATED_ROOT / "lotes"
    if batch and lotes.is_dir():
        for path in lotes.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("batchId") == batch and record.get("gpu") in TARGETS:
                return record["gpu"]
    # Sem escolha registrada: a local, se este computador tiver GPU; senão a remota (app instalado em outra máquina).
    from .ambiente import default_target

    return default_target()


def _lock_path() -> Path:
    return LOCK_PATH if _TARGET == "local" else LOCK_PATH.with_name("gpu-remota.lock")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def job_files() -> list[Path]:
    """Só arquivos de job (id alfanumérico, sem hífen): outros .json na pasta não podem derrubar a fila."""
    if not JOBS_DIR.is_dir():
        return []
    return [path for path in JOBS_DIR.glob("*.json") if re.fullmatch(r"[0-9A-Za-z]+", path.stem)]


def _job_path(job_id: str) -> Path:
    if not job_id or any(character not in "0123456789abcdef-" for character in job_id):
        raise ValueError("Identificador de job inválido.")
    return JOBS_DIR / f"{job_id}.json"


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "state" not in data or "kind" not in data:
        raise json.JSONDecodeError("não é um job", "", 0)  # quem lê já ignora JSON inválido
    return data


# A animação grava progresso de mais de uma thread (montagem das cenas em paralelo com a GPU, 2026-10-09); o .tmp é
# um só por job, então as gravações do mesmo processo precisam ser uma de cada vez.
_WRITE_LOCK = threading.RLock()


def _write(path: Path, job: dict[str, Any]) -> None:
    with _WRITE_LOCK:
        _write_unlocked(path, job)


def _write_unlocked(path: Path, job: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # No Windows a troca falha enquanto outro processo lê o arquivo; tenta de novo por alguns segundos.
    for attempt in range(50):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_started_at(pid: int) -> datetime | None:
    """Horário de criação do processo (Windows), para reconhecer um pid reaproveitado por outro programa."""
    if os.name != "nt" or pid <= 0:
        return None
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                                      ctypes.byref(kernel), ctypes.byref(user)):
            return None
        # FILETIME: intervalos de 100 ns desde 1601-01-01 (UTC).
        return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=created.value // 10)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def pid_alive_since(pid: int, recorded_at: str | None, slack_seconds: int = 120) -> bool:
    """O processo existe E já existia quando o pid foi registrado (senão o Windows reaproveitou o número)."""
    if not _pid_alive(pid):
        return False
    if not recorded_at:
        return True
    started = _process_started_at(pid)
    if started is None:
        return True
    return started <= datetime.fromisoformat(recorded_at) + timedelta(seconds=slack_seconds)


def _job_alive(job: dict[str, Any]) -> bool:
    return pid_alive_since(int(job.get("pid") or 0), job.get("pidAt"))


def _next_queue_position() -> int:
    """Carimbo estritamente crescente: o relógio do Windows avança em saltos de milissegundos."""
    latest = 0
    for existing in job_files():
        try:
            latest = max(latest, int(_read(existing).get("queuedAtNs", 0)))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return max(time.time_ns(), latest + 1)


def start_job(kind: str, params: dict[str, Any], *, spawn: bool = True) -> dict[str, Any]:
    """Registra o job e dispara um processo destacado que espera a vez na GPU e o executa."""
    if kind not in KINDS:
        raise ValueError(f"Tipo de job desconhecido: {kind!r}")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    path = _job_path(job_id)
    params = {**params, "gpu": resolve_target(params)}
    job = {
        "id": job_id, "kind": kind, "params": params, "state": "queued",
        "createdAt": _now(), "queuedAtNs": _next_queue_position(), "progress": [], "result": None, "error": None,
    }
    _write(path, job)
    if spawn:
        job["pid"], job["pidAt"] = _launch(path), _now()
        _write(path, job)
    return job


def _project_python() -> str:
    """Python do .venv do projeto (sys.executable pode ser o Python base quando o servidor MCP roda pelo venv)."""
    venv = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.is_file() else sys.executable


def _launch(path: Path) -> int:
    """Dispara o processo que espera a vez na GPU e executa o job; devolve o pid.

    No Windows, um lote iniciado pelo painel roda dentro do Claude Code sem janela: quando ele termina, o Windows
    encerra todos os processos filhos (Job Object). Por isso o job tenta sair dessa árvore (BREAKAWAY) e, se o
    Windows não deixar, é criado pelo serviço WMI, fora da árvore de processos de quem o disparou.
    """
    log_path = JOBS_DIR / f"{path.stem}.log"
    if os.name != "nt":
        log = open(log_path, "ab")  # noqa: SIM115 - herdado pelo processo filho
        return subprocess.Popen(
            [_project_python(), "-m", "influencer_mcp.motion_jobs", str(path)], cwd=str(PROJECT_ROOT),
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(PROJECT_ROOT)}, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, close_fds=True, **detached(),
        ).pid
    log = open(log_path, "ab")  # noqa: SIM115 - herdado pelo processo filho
    try:
        return subprocess.Popen(
            [_project_python(), "-m", "influencer_mcp.motion_jobs", str(path)], cwd=str(PROJECT_ROOT),
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(PROJECT_ROOT)}, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, close_fds=True, **detached(breakaway=True),
        ).pid
    except OSError:
        log.close()
        return _launch_outside_job(path, log_path)


def _launch_outside_job(path: Path, log_path: Path) -> int:
    """Cria o processo pelo WMI (Win32_Process.Create): ele não pertence ao Job Object de quem chamou.

    O processo novo não herda o ambiente; scripts/motion_job.py carrega as variáveis (registro do usuário e
    .mcp.json) antes de executar o job.
    """
    runner = PROJECT_ROOT / "scripts" / "motion_job.py"
    command = f'cmd.exe /c ""{_project_python()}" "{runner}" run "{path}" >> "{log_path}" 2>&1"'
    script = (
        # ShowWindow=0 (SW_HIDE): o cmd.exe do job nasce oculto.
        "$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine={_ps_quote(command)}; CurrentDirectory={_ps_quote(str(PROJECT_ROOT))}; ProcessStartupInformation=$si}}; "
        "if ($r.ReturnValue -ne 0) { exit 1 }; $r.ProcessId"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, **quiet(),
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        raise RuntimeError(f"Não foi possível iniciar o job pelo WMI: {(result.stderr or result.stdout)[-500:]}")
    return int(result.stdout.strip())


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


DIED = "O processo do job terminou inesperadamente"
MAX_RESTARTS = 3


def revive_dead_jobs() -> list[str]:
    """Rede de proteção (roda no painel): job na fila ou rodando cujo processo sumiu sem erro é disparado de novo.

    A animação e as imagens-chave retomam de onde pararam. Cada job é reiniciado no máximo MAX_RESTARTS vezes.
    """
    revived = []
    for path in sorted(job_files()):
        try:
            job = _read(path)
        except (OSError, json.JSONDecodeError):
            continue
        died = job["state"] in ("queued", "running") and not _job_alive(job)
        marked = job["state"] == "failed" and (job.get("error") or "").startswith(DIED) and not job.get("finishedAt")
        if not (died or marked) or job.get("restarts", 0) >= MAX_RESTARTS:
            continue
        job.update(state="queued", error=None, restarts=job.get("restarts", 0) + 1)
        job["progress"].append(f"{_now()} processo perdido; job disparado de novo ({job['restarts']}/{MAX_RESTARTS})")
        _write(path, job)
        job["pid"], job["pidAt"] = _launch(path), _now()
        _write(path, job)
        revived.append(job["id"])
    return revived


def job_status(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.is_file():
        raise FileNotFoundError(f"Job não encontrado: {job_id}")
    job = _read(path)
    if job["state"] in ("queued", "running") and not _job_alive(job):
        job["state"] = "failed"
        job["error"] = job.get("error") or "O processo do job terminou inesperadamente; inicie-o de novo (retoma de onde parou)."
        _write(path, job)
    job["progress"] = job["progress"][-15:]
    return job


def list_jobs() -> list[dict[str, Any]]:
    if not JOBS_DIR.is_dir():
        return []
    jobs = [job_status(path.stem) for path in sorted(job_files())]
    return sorted(jobs, key=lambda job: job["createdAt"])


def _live_jobs() -> list[dict[str, Any]]:
    jobs = []
    for path in job_files():
        try:
            job = _read(path)
        except (OSError, json.JSONDecodeError):
            continue
        if job["state"] in ("queued", "running") and _job_alive(job) and job_target(job) == _TARGET:
            jobs.append(job)
    return jobs  # só os da mesma GPU: fila, prioridade e pausas valem por GPU


def _active_languages() -> list[str]:
    from .localize import enabled_languages

    return enabled_languages()


def _rank(job: dict[str, Any]) -> int:
    """Prioridade na GPU (menor = primeiro): TODA imagem-chave vem antes de qualquer animação, urgente ou não
    (pedido do usuário, 2026-10-14); dentro de cada tipo, o urgente (pedido do usuário no painel) vem primeiro."""
    return (0 if job["kind"] == "keyframes" else 2) + (0 if job["params"].get("urgent") else 1)


def _oldest_queued(job_id: str) -> bool:
    # Imagens-chave passam na frente de animações (pedido do usuário, 2026-10-03); dentro do mesmo tipo,
    # ordem de chegada com precisão de nanossegundos (jobs criados no mesmo segundo mantêm a sequência).
    queued = [
        (_rank(job), job.get("queuedAtNs", 0), job["id"])
        for job in _live_jobs() if job["state"] == "queued" and not is_paused(job["id"])
    ]
    return bool(queued) and min(queued)[2] == job_id


def pause_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.pause"


def is_paused(job_id: str) -> bool:
    """Pausado pelo usuário no painel (pedido de 2026-10-03)."""
    return pause_path(job_id).is_file()


def keyframes_waiting() -> bool:
    """Há job de imagens-chave na fila ou rodando (vivo)."""
    return any(job["kind"] == "keyframes" for job in _live_jobs())


def _must_yield(job: dict[str, Any]) -> bool:
    """Há outro job de prioridade maior esperando ou rodando: urgente vale para qualquer tipo; imagens-chave
    comuns só passam na frente de animações comuns."""
    mine = _rank(job)
    return any(other["id"] != job["id"] and not is_paused(other["id"]) and _rank(other) < mine
               and (other["params"].get("urgent") or job["kind"] != "keyframes")
               for other in _live_jobs())


def _paused_animation(exclude: str) -> bool:
    # Pausa do usuário não segura a fila: só a animação que cedeu a vez às imagens-chave tem preferência.
    return any(job.get("pausedForKeyframes") and job["id"] != exclude and not is_paused(job["id"])
               for job in _live_jobs())


def _try_lock() -> bool:
    lock = _lock_path()
    holder = int(lock.read_text() or 0) if lock.is_file() else 0
    if holder and _pid_alive(holder):
        return False
    lock.write_text(str(os.getpid()))
    return int(lock.read_text()) == os.getpid()


def _release_lock() -> None:
    lock = _lock_path()
    if lock.is_file() and lock.read_text().strip() == str(os.getpid()):
        lock.unlink(missing_ok=True)


def _acquire_gpu(job_id: str, kind: str = "animation", urgent: bool = False) -> None:
    while True:
        # Animação pausada pelas imagens-chave retoma antes de qualquer animação nova da fila (menos a urgente).
        blocked = kind != "keyframes" and not urgent and _paused_animation(job_id)
        if not blocked and _oldest_queued(job_id) and _try_lock():
            return
        time.sleep(15)


def _checkpoint(path: Path, job: dict[str, Any], progress: Any) -> None:
    """Ponto seguro entre clipes/imagens: atende a pausa do usuário e, nas animações, a prioridade das imagens-chave."""
    if _TARGET == "remota":
        from .gpu_remota import touch

        touch()  # a máquina remota só desliga depois de ficar ociosa
    if is_paused(job["id"]):
        # Volta para a fila com a posição original: os outros seguem; ao retomar, espera a vez normalmente.
        job["state"] = "queued"
        progress("pausado pelo usuário: GPU liberada para o próximo da fila")
        _release_lock()
        while is_paused(job["id"]):
            time.sleep(10)
        progress("retomado pelo usuário: aguardando a vez na fila")
        _acquire_gpu(job["id"], job["kind"], bool(job["params"].get("urgent")))
        job["state"] = "running"
        progress("retomado")
    _pause_for_keyframes(path, job, progress)


def _pause_for_keyframes(path: Path, job: dict[str, Any], progress: Any) -> None:
    """Chamada entre clipes da animação: libera a GPU enquanto houver imagens-chave esperando."""
    if not _must_yield(job):
        return
    progress("pausado: há trabalho de prioridade maior na GPU")
    job["pausedForKeyframes"] = True
    _write(path, job)
    _release_lock()
    while True:
        time.sleep(15)
        if not _must_yield(job) and _try_lock():
            break
    job["pausedForKeyframes"] = False
    progress("retomado depois das imagens-chave")


def _gpu_free_now(job_id: str) -> bool:
    """A GPU está livre e este job é o próximo: narrar na CPU só atrasaria o começo."""
    lock = _lock_path()
    holder = int(lock.read_text() or 0) if lock.is_file() else 0
    return not (holder and _pid_alive(holder)) and _oldest_queued(job_id)


def _prenarrate(job: dict[str, Any], progress: Any) -> None:
    seconds = narrate_on_cpu(job["params"])
    progress(f"narração pronta na CPU ({seconds:.1f}s) enquanto aguardava a GPU")


def narrate_on_cpu(params: dict[str, Any]) -> float:
    """Narra o roteiro na CPU e confere a duração (ValueError se ficar fora da faixa). Os áudios ficam na pasta do
    vídeo e são reaproveitados pelo job enquanto o texto não mudar."""
    from . import motion

    cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""  # Kokoro na CPU: a GPU está com outro job
    try:
        seconds = motion.prenarrate_keyframes(influencer_name=params["influencer_name"], batch_id=params["batch_id"],
                                              script=params["script"])
    finally:
        if cuda is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = cuda
    return seconds


def _keep_awake() -> None:
    """Impede a suspensão por inatividade enquanto este processo vive (2026-10-07: o notebook dormiu 4 h no meio de
    uma imagem-chave). Vale para jobs rodando e na fila. A tela também fica acesa: em notebooks com Modern Standby,
    apagar a tela por inatividade já põe o sistema em espera e congela o ComfyUI."""
    if os.name == "nt":
        es_continuous, es_system_required, es_display_required = 0x80000000, 0x00000001, 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required | es_display_required)


def run_job(path: Path) -> None:
    from . import motion  # importado aqui para o processo de fila não depender do ComfyUI ao ser criado

    global _TARGET
    _keep_awake()
    job = _read(path)
    job["pid"], job["pidAt"] = os.getpid(), _now()
    _write(path, job)
    _TARGET = job_target(job)
    os.environ["INFLUENCER_GPU"] = _TARGET
    if _TARGET == "remota":
        from .gpu_remota import base_url

        os.environ["COMFYUI_URL"] = base_url()  # túnel SSH para o ComfyUI da máquina remota

    def progress(message: str) -> None:
        job["progress"].append(f"{_now()} {message}")
        _write(path, job)

    if job["kind"] == "keyframes" and not _gpu_free_now(job["id"]):
        # Enquanto espera a GPU: narração na CPU e conferência da duração (pedido de 2026-10-05). Um roteiro
        # longo ou curto demais falha em minutos, e a vez na GPU fica só para as imagens.
        try:
            _prenarrate(job, progress)
        except Exception as error:  # noqa: BLE001 - registrado para quem consulta o job
            job.update(state="failed", error=f"{error}\n{traceback.format_exc()[-3000:]}", finishedAt=_now())
            _write(path, job)
            return
    _acquire_gpu(job["id"], job["kind"], bool(job["params"].get("urgent")))
    job.update(state="running", startedAt=_now())
    _write(path, job)

    try:
        from .comfyui import ensure_running
        from .media import _comfyui_url

        # Verificação local antes de usar a GPU: sobe o ComfyUI se estiver fora, em vez de falhar minutos depois.
        ensure_running(_comfyui_url(require_avatar_checkpoint=False), log=progress)
        params = job["params"]
        if job["kind"] == "keyframes":
            result = motion.prepare_motion_keyframes(
                influencer_name=params["influencer_name"], batch_id=params["batch_id"],
                script=params["script"], scene_numbers=params.get("scene_numbers"), progress=progress,
                yield_gpu=lambda: _checkpoint(path, job, progress),
            )
        else:
            result = motion.animate_motion_short(
                influencer_name=params["influencer_name"], batch_id=params["batch_id"],
                video_slug=params["video_slug"], scene_numbers=params.get("scene_numbers"), progress=progress,
                yield_gpu=lambda: _checkpoint(path, job, progress),
                gpu_done=lambda: (_release_lock(), os.environ.__setitem__("CUDA_VISIBLE_DEVICES", ""),
                                  progress("animação pronta: GPU liberada; montando " + (
                                      "/".join(code.upper() for code in _active_languages()) + " na CPU"
                                      if _active_languages() else "o vídeo final na CPU"))),
            )
        job.update(state="done", result=result, finishedAt=_now())
        if job["kind"] == "keyframes" and params.get("thenAnimate"):
            # Cenas refeitas num vídeo pronto: anima só elas e remonta o vídeo (idiomas ativos com as traduções salvas).
            follow = start_job("animation", {"influencer_name": params["influencer_name"], "batch_id": params["batch_id"],
                                             "video_slug": params["script"]["slug"], "gpu": job_target(job),
                                             "scene_numbers": params.get("scene_numbers"),
                                             "urgent": bool(params.get("urgent"))})
            progress(f"imagens prontas; animação das cenas e remontagem na fila ({follow['id']})")
    except Exception as error:  # noqa: BLE001 - o erro é registrado para quem consulta o job
        job.update(state="failed", error=f"{error}\n{traceback.format_exc()[-3000:]}", finishedAt=_now())
    finally:
        job.pop("pausedForKeyframes", None)
        if path.is_file():  # cancelado no painel: o arquivo já foi apagado, não recria
            _write(path, job)
        pause_path(job["id"]).unlink(missing_ok=True)
        if job["kind"] == "keyframes":
            _free_comfyui_models()
        _release_lock()


def _free_comfyui_models() -> None:
    """Descarrega o SDXL e o ControlNet das imagens-chave antes de a animação retomar (2026-10-07: somados ao Wan,
    levaram a RAM a 98% com 32 GB). Melhor esforço: o ComfyUI aplica entre um pedido e outro."""
    import urllib.request

    from .media import _comfyui_url

    try:
        request = urllib.request.Request(_comfyui_url(require_avatar_checkpoint=False).rstrip("/") + "/free",
                                         data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                         headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=10).close()
    except Exception:  # noqa: BLE001 - sem o ComfyUI no ar não há o que liberar
        pass


if __name__ == "__main__":
    run_job(Path(sys.argv[1]))
