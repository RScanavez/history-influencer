"""GPU remota (RunPod, H100): liga a máquina sob demanda, abre um túnel SSH e desliga quando fica ociosa.

Decisão do usuário (2026-10-03): os vídeos que já existiam continuam na GPU local; cada lote novo escolhe
"local" ou "remota" no painel. O ComfyUI remoto nunca fica exposto na internet: o painel e os jobs falam com ele
por um túnel SSH em 127.0.0.1:<porta-local> (padrão 8189), com a chave em secrets/runpod_ssh.

Configuração em gpu_remota.md ("- chave: valor"); a chave da API fica em secrets/runpod.txt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .winproc import detached, quiet

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "gpu_remota.md"
API_KEY_PATH = PROJECT_ROOT / "secrets" / "runpod.txt"
SSH_KEY_PATH = PROJECT_ROOT / "secrets" / "runpod_ssh"
# Fora de generated/jobs: lá todo .json é lido como job (o arquivo ali derrubou a fila em 2026-10-03).
STATE_PATH = PROJECT_ROOT / "generated" / "gpu-remota.json"
API_URL = "https://api.runpod.io/graphql"
LOCAL = "local"
REMOTE = "remota"

Log = Callable[[str], None] | None


def settings(text: str | None = None) -> dict[str, Any]:
    if text is None:
        text = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.is_file() else ""
    values = {key.lower(): value.strip() for key, value in re.findall(r"^-\s*([\w-]+):[ \t]*(.*?)\s*$", text, re.M)}
    return {
        "pod_id": values.get("pod-id") or None,
        "local_port": int(values.get("porta-local") or 8189),
        "idle_minutes": float(values.get("desligar-apos-min") or 10),
        # Decisão do usuário (2026-10-03): a máquina só desliga quando ele mandar (botão no painel).
        "auto_stop": (values.get("desligar-automatico") or "nao").strip().lower() in ("sim", "s", "yes", "true"),
        "gpu_type": values.get("gpu") or "NVIDIA H100 80GB HBM3",
        "volume_id": values.get("volume-id") or None,
        "datacenter": values.get("datacenter") or None,
        "wan_high": values.get("wan-alto") or "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "wan_low": values.get("wan-baixo") or "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        # Wan 2.2 Fun Control (cenas de exercício conduzidas por esqueleto). Vazio: a máquina não tem o modelo e
        # as cenas de exercício usam quadro inicial e final (FLF2V).
        "fun_high": values.get("fun-alto") or None,
        "fun_low": values.get("fun-baixo") or None,
    }


def configured() -> bool:
    """Pronta para receber vídeos: máquina criada (pod-id), chave da API e chave SSH presentes."""
    return bool(settings()["pod_id"]) and API_KEY_PATH.is_file() and SSH_KEY_PATH.is_file()


def base_url() -> str:
    return f"http://127.0.0.1:{settings()['local_port']}"


# ---------- API do RunPod ----------

def api(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    key = API_KEY_PATH.read_text(encoding="utf-8-sig").strip()
    request = urllib.request.Request(
        API_URL, data=json.dumps({"query": query, "variables": variables or {}}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "influencer-painel"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read())
    if data.get("errors"):
        raise RuntimeError(f"RunPod: {data['errors'][0].get('message', data['errors'])}")
    return data["data"]


def pod(pod_id: str) -> dict[str, Any] | None:
    query = """query($id: String!) { pod(input: {podId: $id}) { id desiredStatus costPerHr
        runtime { uptimeInSeconds ports { ip isIpPublic privatePort publicPort type } } } }"""
    return api(query, {"id": pod_id}).get("pod")


def resume(pod_id: str) -> None:
    api("mutation($id: String!) { podResume(input: {podId: $id, gpuCount: 1}) { id desiredStatus } }", {"id": pod_id})


def stop(pod_id: str) -> None:
    api("mutation($id: String!) { podStop(input: {podId: $id}) { id desiredStatus } }", {"id": pod_id})


def ssh_endpoint(info: dict[str, Any]) -> tuple[str, int] | None:
    for port in ((info.get("runtime") or {}).get("ports") or []):
        if port.get("privatePort") == 22 and port.get("isIpPublic"):
            return port["ip"], int(port["publicPort"])
    return None


# ---------- estado local (túnel e última atividade) ----------

def _state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(**fields: Any) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({**_state(), **fields}, ensure_ascii=False) + "\n", encoding="utf-8")


def touch(now: datetime | None = None) -> None:
    """Registra uso da GPU remota (o desligamento automático conta a ociosidade a partir daqui)."""
    _save_state(lastActivity=(now or datetime.now(UTC)).isoformat(timespec="seconds"))


def _tunnel_alive() -> bool:
    from .motion_jobs import _pid_alive

    return _pid_alive(int(_state().get("tunnelPid") or 0))


def open_tunnel(host: str, port: int) -> int:
    """Túnel SSH 127.0.0.1:<porta-local> → ComfyUI da máquina (127.0.0.1:8188 lá dentro), destacado do job."""
    local = settings()["local_port"]
    args = ["ssh", "-N", "-L", f"127.0.0.1:{local}:127.0.0.1:8188", "-p", str(port), "-i", str(SSH_KEY_PATH),
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={PROJECT_ROOT / 'secrets' / 'runpod_known_hosts'}",
            "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes", f"root@{host}"]
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   **detached(breakaway=True))
    except OSError:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   **detached())
    _save_state(tunnelPid=process.pid, tunnelTo=f"{host}:{port}")
    return process.pid


def close_tunnel() -> None:
    pid = int(_state().get("tunnelPid") or 0)
    if pid and _tunnel_alive():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **quiet())
    _save_state(tunnelPid=0)


GPU_WAIT_SECONDS = 6 * 3600  # sem H100 livre no servidor: espera (máquina desligada, sem custo) até 6 h
GPU_RETRY_SECONDS = 300


# A máquina do RunPod fica presa a um servidor físico; com ele cheio, ela não liga. Desde 2026-10-04 (decisão do
# usuário) o painel cria outra máquina igual, ligada ao MESMO disco (nada a reinstalar), num servidor com H100
# livre do mesmo datacenter, troca o pod-id no gpu_remota.md e apaga a antiga.
_POD_FIELDS = "name imageName dockerArgs ports env containerDiskInGb volumeMountPath networkVolumeId machine { dataCenterId }"


def migrate(old_id: str, log: Log = None) -> str:
    """Nova máquina com a mesma configuração e o mesmo disco; devolve o id novo (já ligada)."""
    old = api("query($id: String!) { pod(input: {podId: $id}) { %s } }" % _POD_FIELDS, {"id": old_id})["pod"]
    # Custo (pedido do usuário: evitar cobranças extras): nunca duas máquinas no mesmo disco.
    others = [item["id"] for item in api("{ myself { pods { id networkVolumeId } } }")["myself"]["pods"]
              if item["id"] != old_id and item.get("networkVolumeId") == old.get("networkVolumeId")]
    if others:
        raise RuntimeError(f"já existe outra máquina no mesmo disco ({', '.join(others)}); confira no RunPod")
    env = [dict(zip(("key", "value"), item.split("=", 1))) for item in old.get("env") or [] if "=" in item]
    data_center = (old.get("machine") or {}).get("dataCenterId") or settings()["datacenter"]
    created = api("""mutation($input: PodFindAndDeployOnDemandInput) {
        podFindAndDeployOnDemand(input: $input) { id desiredStatus } }""", {"input": {
        "cloudType": "SECURE", "gpuCount": 1, "gpuTypeId": settings()["gpu_type"], "name": old["name"],
        "imageName": old["imageName"], "dockerArgs": old["dockerArgs"], "ports": old["ports"], "env": env,
        "containerDiskInGb": old["containerDiskInGb"], "volumeInGb": 0, "volumeMountPath": old["volumeMountPath"],
        "networkVolumeId": old["networkVolumeId"], "dataCenterId": data_center,
    }})["podFindAndDeployOnDemand"]
    new_id = created["id"]
    _save_pod_id(new_id)
    try:
        api("mutation($id: String!) { podTerminate(input: {podId: $id}) }", {"id": old_id})
    except RuntimeError as error:  # a nova já está configurada; a antiga (desligada, sem custo de GPU) fica para depois
        if log:
            log(f"GPU remota: não consegui apagar a máquina antiga {old_id}: {error}")
    _save_state(migratedFrom=old_id, migratedAt=datetime.now(UTC).isoformat(timespec="seconds"))
    if log:
        log(f"GPU remota: servidor sem H100 livre; criada máquina nova {new_id} no mesmo disco ({data_center}).")
    return new_id


# Máquina nova do zero (a configurada foi apagada pelo RunPod, ex.: saldo negativo em 2026-10-05): mesma receita
# do passo 4 de gpu_remota.md. A imagem traz o Python 3.11 com que o venv de /workspace foi criado.
POD_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
POD_START = "bash -c 'bash /workspace/start_comfyui.sh >> /workspace/comfyui-start.log 2>&1 & /start.sh'"


def _save_pod_id(pod_id: str) -> None:
    # Bytes, para manter as quebras de linha do arquivo (CRLF no Windows).
    text = CONFIG_PATH.read_bytes().decode("utf-8")
    text = re.sub(r"^(-\s*pod-id:[ \t]*)[^\r\n]*", lambda m: m.group(1) + pod_id, text, count=1, flags=re.M)
    CONFIG_PATH.write_bytes(text.encode("utf-8"))


def create_pod(log: Log = None) -> str:
    """Cria a máquina no disco do projeto; sem GPU livre no datacenter, tenta a cada 5 min (até 6 h)."""
    cfg = settings()
    others = [item["id"] for item in api("{ myself { pods { id networkVolumeId } } }")["myself"]["pods"]
              if item.get("networkVolumeId") == cfg["volume_id"]]
    if others:  # custo: nunca duas máquinas no mesmo disco
        _save_pod_id(others[0])
        return others[0]
    public_key = SSH_KEY_PATH.with_suffix(".pub").read_text(encoding="utf-8").strip()
    request = {"cloudType": "SECURE", "gpuCount": 1, "gpuTypeId": cfg["gpu_type"], "name": "influencer-comfyui",
               "imageName": POD_IMAGE, "dockerArgs": POD_START, "ports": "22/tcp",
               "env": [{"key": "PUBLIC_KEY", "value": public_key}], "containerDiskInGb": 40, "volumeInGb": 0,
               "volumeMountPath": "/workspace", "networkVolumeId": cfg["volume_id"], "dataCenterId": cfg["datacenter"]}
    deadline, warned = time.monotonic() + GPU_WAIT_SECONDS, False
    while True:
        try:
            created = api("""mutation($input: PodFindAndDeployOnDemandInput) {
                podFindAndDeployOnDemand(input: $input) { id desiredStatus } }""", {"input": request})
            break
        except RuntimeError as error:
            if "no longer any instances" not in str(error) or time.monotonic() > deadline:
                raise
            if log and not warned:
                log(f"GPU remota: nenhuma {cfg['gpu_type']} livre em {cfg['datacenter']}; nova tentativa a cada 5 min "
                    "(até 6 h), sem custo enquanto espera.")
                warned = True
        time.sleep(GPU_RETRY_SECONDS)
    pod_id = created["podFindAndDeployOnDemand"]["id"]
    _save_pod_id(pod_id)
    if log:
        log(f"GPU remota: máquina nova {pod_id} criada no disco do projeto ({cfg['datacenter']}).")
    return pod_id


def _resume_when_available(pod_id: str, log: Log) -> str:
    """Religa a máquina. Servidor sem H100 livre: cria outra no mesmo disco; sem H100 no datacenter, tenta de novo
    a cada 5 min (até 6 h). Devolve o id da máquina ligada."""
    deadline = time.monotonic() + GPU_WAIT_SECONDS
    warned = False
    while True:
        try:
            resume(pod_id)
            return pod_id
        except RuntimeError as error:
            if "not enough free GPUs" not in str(error) or time.monotonic() > deadline:
                raise
        try:
            return migrate(pod_id, log)
        except RuntimeError as error:
            if time.monotonic() > deadline:
                raise
            if log and not warned:
                log(f"GPU remota: nenhuma H100 livre no datacenter ({error}); nova tentativa a cada 5 min, máquina "
                    "desligada, sem custo.")
                warned = True
        time.sleep(GPU_RETRY_SECONDS)
        pod_id = settings()["pod_id"] or pod_id  # outro processo pode ter trocado a máquina


# Ajustes aplicados na máquina a cada vez que ela liga, antes do primeiro uso: script de inicialização atual
# (saídas no disco temporário) e limpeza de saídas antigas no disco persistente (encheu em 2026-10-04).
# "[m]ain.py": o padrão não casa com o texto deste próprio script (antes o pkill matava o script, 2026-10-04).
_PREPARE = r"""set -e
cd /workspace
if ! grep -q output-directory /workspace/start_comfyui.sh 2>/dev/null; then
  sed -i 's/\r$//' /workspace/instalar_comfyui.sh
  awk '/cat > "\$W\/start_comfyui.sh" <</{f=1;next} f&&/^EOF$/{exit} f' /workspace/instalar_comfyui.sh > /workspace/start_comfyui.sh
  chmod +x /workspace/start_comfyui.sh
  pkill -f "[m]ain.py --listen" || true
  sleep 3
fi
rm -rf /workspace/ComfyUI/output/* /workspace/ComfyUI/input/* /workspace/ComfyUI/temp/* 2>/dev/null || true
if ! pgrep -f "[m]ain.py --listen" >/dev/null; then nohup bash /workspace/start_comfyui.sh > /workspace/comfyui.log 2>&1 & fi
echo PREPARADO
"""


def prepare_machine(host: str, port: int, *, log: Log = None) -> None:
    known = PROJECT_ROOT / "secrets" / "runpod_known_hosts"
    base = ["-i", str(SSH_KEY_PATH), "-o", f"UserKnownHostsFile={known}", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
    installer = PROJECT_ROOT / "scripts" / "remoto" / "instalar_comfyui.sh"
    # Primeiro libera espaço: com o disco cheio nem a cópia do script cabe (falhou assim em 2026-10-04).
    # Renomeia as pastas (instantâneo) e apaga em segundo plano: apagar milhares de quadros no disco de rede passava
    # dos 5 min do SSH e o job falhava (2026-10-04, convite-caminhada-companhia).
    cleanup = ("cd /workspace/ComfyUI && for d in output temp; do if [ -d $d ]; then mv $d .apagar-$d-$(date +%s%N); fi; "
               "mkdir -p $d; done; (nohup rm -rf /workspace/ComfyUI/.apagar-* >/dev/null 2>&1 &); echo LIMPO")
    for attempt in range(12):  # o SSH pode demorar alguns segundos a mais que a porta aparecer
        try:
            cleaned = subprocess.run(["ssh", *base, "-p", str(port), f"root@{host}", cleanup], capture_output=True,
                                     text=True, timeout=120, **quiet())
        except subprocess.TimeoutExpired:
            continue
        if "LIMPO" in cleaned.stdout:
            break
        time.sleep(10)
    copied = None
    for attempt in range(12):
        # Com prazo: um scp pendurado prendeu a fila remota a noite inteira (2026-10-04, 23:56 → 07:50).
        try:
            copied = subprocess.run(["scp", *base, "-P", str(port), str(installer),
                                     f"root@{host}:/workspace/instalar_comfyui.sh"], capture_output=True, text=True, timeout=120,
                                    **quiet())
        except subprocess.TimeoutExpired:
            continue
        if copied.returncode == 0:
            break
        time.sleep(10)
    else:
        detail = copied.stderr[-300:] if copied is not None else "sem resposta (tempo esgotado)"
        raise RuntimeError(f"Não consegui copiar o script para a máquina remota: {detail}")
    result = subprocess.run(["ssh", *base, "-p", str(port), f"root@{host}", _PREPARE], capture_output=True, text=True,
                            timeout=300, **quiet())
    if "PREPARADO" not in result.stdout:
        raise RuntimeError(f"Falha ao preparar a máquina remota: {(result.stderr or result.stdout)[-500:]}")
    if log:
        log("GPU remota: máquina preparada (saídas no disco temporário, disco persistente limpo).")


def ensure_ready(*, timeout: int = 900, log: Log = None) -> str:
    """Liga a máquina (se preciso), abre o túnel e espera o ComfyUI remoto responder. Devolve a URL local."""
    from .comfyui import is_up

    if not configured():
        raise RuntimeError("GPU remota ainda não configurada (gpu_remota.md sem pod-id, ou faltam as chaves em secrets/).")
    pod_id = settings()["pod_id"]
    url = base_url()
    touch()
    if _tunnel_alive() and is_up(url):
        return url
    try:
        info = pod(pod_id)
    except RuntimeError as error:
        if "does not exist" not in str(error):
            raise
        info = None
    if not info:
        # A máquina configurada não existe mais (o RunPod apaga pods com saldo negativo; a consulta volta vazia):
        # cria outra no mesmo disco.
        pod_id, info = create_pod(log), {"desiredStatus": "RUNNING"}
    if info.get("desiredStatus") != "RUNNING":
        pod_id = _resume_when_available(pod_id, log)
        if log:
            log("GPU remota: ligando a máquina H100.")
    deadline = time.monotonic() + timeout
    endpoint = None
    while time.monotonic() < deadline:
        info = pod(pod_id) or {}
        endpoint = ssh_endpoint(info)
        if endpoint:
            break
        time.sleep(10)
    if not endpoint:
        raise RuntimeError(f"A máquina remota não ficou pronta em {timeout // 60} min.")
    prepare_machine(*endpoint, log=log)
    close_tunnel()
    open_tunnel(*endpoint)
    while time.monotonic() < deadline:
        time.sleep(5)
        if is_up(url):
            if log:
                log("GPU remota: ComfyUI respondendo.")
            touch()
            return url
    raise RuntimeError("O ComfyUI remoto não respondeu pelo túnel; confira a máquina no RunPod.")


def status() -> dict[str, Any]:
    """Situação da máquina para o painel: ligada?, custo por hora e minutos sem uso."""
    if not configured():
        return {"configured": False}
    info = pod(settings()["pod_id"]) or {}
    last = _state().get("lastActivity")
    idle = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() / 60 if last else None
    return {"configured": True, "running": info.get("desiredStatus") == "RUNNING", "costPerHr": info.get("costPerHr"),
            "idleMinutes": round(idle) if idle is not None else None, "autoStop": settings()["auto_stop"]}


def shutdown() -> None:
    """Desliga a máquina a pedido do usuário (botão do painel)."""
    close_tunnel()
    stop(settings()["pod_id"])
    _save_state(stoppedAt=datetime.now(UTC).isoformat(timespec="seconds"))


def stop_if_idle(remote_jobs_active: bool, now: datetime | None = None) -> bool:
    """Desliga a máquina quando não há job remoto há `desligar-apos-min` minutos. Devolve True se desligou."""
    if not configured() or not settings()["auto_stop"]:
        if remote_jobs_active and configured():
            touch(now)
        return False
    now = now or datetime.now(UTC)
    if remote_jobs_active:
        touch(now)
        return False
    last = _state().get("lastActivity")
    if not last or (now - datetime.fromisoformat(last)).total_seconds() < settings()["idle_minutes"] * 60:
        return False
    info = pod(settings()["pod_id"]) or {}
    if info.get("desiredStatus") == "RUNNING":
        close_tunnel()
        stop(settings()["pod_id"])
        _save_state(stoppedAt=now.isoformat(timespec="seconds"))
        return True
    return False
