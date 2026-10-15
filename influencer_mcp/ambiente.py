"""Verificação do ambiente onde o app roda (pedido do usuário, 2026-10-04): o app vai para o git e deve rodar em
qualquer máquina. Na abertura do painel conferimos:

- GPU local: há GPU NVIDIA (nvidia-smi) e ComfyUI instalado? Se não, a fila local fica indisponível.
- GPU remota: chave da API do RunPod, chave SSH do túnel, chave pública cadastrada no RunPod e máquina configurada.
  A opção remota continua visível; o que faltar aparece com a explicação de onde e como criar.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .winproc import quiet

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    """GPU local utilizável: placa NVIDIA visível e ComfyUI instalado com ambiente Python."""
    from .comfyui import comfyui_dir

    gpus, problem = _nvidia_gpus()
    problems = [problem] if problem else []
    folder = comfyui_dir()
    has_python = any((folder / name / "Scripts" / "python.exe").is_file() or (folder / name / "bin" / "python").is_file()
                     for name in ("venv-v037", "venv"))
    if not (folder / "main.py").is_file() or not has_python:
        problems.append(f"ComfyUI local não encontrado em {folder} (defina COMFYUI_DIR ou instale com install.ps1).")
    return {"available": not problems, "gpus": gpus, "problems": problems}


@lru_cache(maxsize=1)
def local_gpu_cached() -> dict[str, Any]:
    """Uma verificação por processo (jobs e servidor MCP decidem a GPU padrão sem rodar nvidia-smi toda hora)."""
    return local_gpu()


def remote_checks(check_api: bool = True) -> list[dict[str, Any]]:
    """Itens que a GPU remota precisa, cada um com ok e, se faltar, como resolver."""
    from . import gpu_remota as gr

    key_ok = gr.API_KEY_PATH.is_file() and bool(gr.API_KEY_PATH.read_text(encoding="utf-8-sig").strip())
    ssh_ok = gr.SSH_KEY_PATH.is_file() and gr.SSH_KEY_PATH.with_suffix(".pub").is_file()
    items = [
        {"id": "ssh-cliente", "label": "Cliente SSH do Windows", "ok": bool(shutil.which("ssh") and shutil.which("scp")),
         "help": "O túnel até a máquina remota usa os comandos ssh e scp. Ative em Configurações → Sistema → Recursos "
                 "opcionais → Adicionar um recurso → Cliente OpenSSH, e abra o painel de novo."},
        {"id": "api", "label": "Chave da API do RunPod", "ok": key_ok,
         "help": "Em runpod.io → Settings → API Keys → Create API Key (nome influencer-painel, permissão All). "
                 "Copie a chave e salve só ela, numa linha, em secrets/runpod.txt (pasta secrets na raiz do projeto, "
                 "fora do git). Nunca cole a chave em chat ou e-mail."},
        {"id": "ssh", "label": "Chave SSH do túnel", "ok": ssh_ok,
         "help": "No terminal, na pasta do projeto: ssh-keygen -t ed25519 -f secrets/runpod_ssh -N \"\" "
                 "(cria secrets/runpod_ssh e secrets/runpod_ssh.pub). A chave privada nunca sai deste computador."},
    ]
    registered = None
    balance = None
    if key_ok and ssh_ok and check_api:
        try:
            me = gr.api("{ myself { pubKey clientBalance } }")["myself"]
            public = gr.SSH_KEY_PATH.with_suffix(".pub").read_text(encoding="utf-8").split()
            registered = len(public) > 1 and public[1] in (me.get("pubKey") or "")
            balance = me.get("clientBalance")
        except Exception as error:  # noqa: BLE001 - sem internet ou chave inválida: vira item pendente
            items.append({"id": "api-ok", "label": "Acesso à API do RunPod", "ok": False,
                          "help": f"A chave em secrets/runpod.txt não funcionou ({str(error)[:150]}). Confira se foi "
                                  "copiada inteira e se não foi apagada no RunPod; se preciso, crie outra."})
    # Sem consultar a API (verificação rápida ao iniciar um lote) não dá para saber se a chave está cadastrada:
    # "não verificado" não é "faltando" (antes isso recusava o lote remoto com tudo configurado; 2026-10-04).
    if ssh_ok and (registered is not None or not key_ok):
        items.append({"id": "ssh-registrada", "label": "Chave pública cadastrada no RunPod", "ok": bool(registered),
                      "help": "Abra secrets/runpod_ssh.pub, copie a linha inteira (começa com ssh-ed25519) e cole em "
                              "runpod.io → Settings → SSH Public Keys → Save."
                              + ("" if registered is not None else " (não deu para conferir agora: falta a chave da API.)")})
    if balance is not None:
        items.append({"id": "saldo", "label": f"Saldo do RunPod: US$ {balance:.2f}", "ok": balance >= 3,
                      "help": "Saldo baixo: um vídeo custa uns US$ 2 na H100. Recarregue em runpod.io → Billing."})
    items.append({"id": "maquina", "label": "Máquina remota configurada (gpu_remota.md)", "ok": bool(gr.settings()["pod_id"]),
                  "help": "Falta criar o disco e a máquina com GPU no RunPod e instalar o ComfyUI nela "
                          "(scripts/remoto/instalar_comfyui.sh). O passo a passo está em gpu_remota.md, seção "
                          "\"Como configurar numa máquina nova\"; o Claude também faz isso por você, com a chave da API salva."})
    return items


def remote_ready(items: list[dict[str, Any]] | None = None) -> bool:
    items = items if items is not None else remote_checks(check_api=False)
    # Saldo baixo é aviso, não impede o uso.
    return all(item["ok"] for item in items if item["id"] != "saldo")


def check_all() -> dict[str, Any]:
    items = remote_checks()
    return {"checkedAt": datetime.now(UTC).isoformat(timespec="seconds"), "local": local_gpu(),
            "remote": {"ready": remote_ready(items), "items": items}}


def default_target() -> str:
    """GPU padrão de um job novo sem escolha explícita: a local, se existir; senão a remota."""
    return "local" if local_gpu_cached()["available"] else "remota"


def system_usage() -> dict[str, Any]:
    """Uso de CPU, memória e GPU para os medidores do painel (pedido de 2026-10-06). Leve: roda a cada poucos segundos.

    Só a GPU NVIDIA conta (a integrada não roda o ComfyUI); sem nvidia-smi, "gpu" vem vazio.
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
