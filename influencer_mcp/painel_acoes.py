"""Ações do painel local (fases 2 e 3): revisão, ajustes sem IA e início de lotes pelo Claude Code.

Tudo aqui roda localmente: as ações de vídeo disparam os mesmos jobs em segundo plano do pipeline
(motion_jobs.start_job) ou o script de versões em outros idiomas. Só "iniciar_lote" usa IA, chamando o
Claude Code sem janela com o comando /gerar-lote.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .localize import LANGUAGES, enabled_languages, require_enabled, translation_path
from .media import GENERATED_ROOT, MIN_SCENES, PROJECT_ROOT, _slug, save_publish_metadata
from .motion_jobs import JOBS_DIR, _job_alive, _pid_alive, pid_alive_since, start_job
from .winproc import detached, quiet

INFLUENCER = os.environ.get("INFLUENCER_NAME", "Historiador")
# Data + sufixo (hora "2311" ou nome como "teste3d"); só minúsculas, dígitos e hífens, sem "..".
BATCH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATE_FILE = "painel.json"
REVIEW_STAGES = ("Aprovado", "Ajustes", "Rejeitado")
LOTES_DIR = GENERATED_ROOT / "lotes"
# Ferramentas liberadas para o /gerar-lote sem janela: o conector local, leitura de arquivos do projeto e Python
# (contar palavras do roteiro). Sem agentes, sem pesquisa web e sem escrita (pedido de 2026-10-14: menos IA; o lote
# inteiro roda numa única sessão). Nada de publicação.
# Modelo dos Claude sem janela (lote e SEO). Pedido de 2026-10-07: gastar menos tokens. Troque com a variável
# INFLUENCER_CLAUDE_MODEL (ex.: "opus") se a qualidade dos roteiros cair.
LOTE_MODEL = os.environ.get("INFLUENCER_CLAUDE_MODEL", "sonnet")
LOTE_TOOLS = [
    "mcp__influencer-local", "Read", "Glob", "Grep", "Bash(python:*)", "Bash(.venv/Scripts/python.exe:*)",
]

def _job_files() -> list[Path]:
    """Só arquivos de job (id alfanumérico): outros .json em generated/jobs não são jobs."""
    if not JOBS_DIR.is_dir():
        return []
    return [path for path in JOBS_DIR.glob("*.json") if re.fullmatch(r"[0-9A-Za-z]+", path.stem)]



def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def video_folder(batch: str, slug: str) -> Path:
    if not BATCH_RE.match(batch or "") or not SLUG_RE.match(slug or ""):
        raise ValueError("Lote ou vídeo inválido.")
    folder = GENERATED_ROOT / _slug(INFLUENCER) / batch / slug
    if not (folder / "motion-script.json").is_file():
        raise FileNotFoundError(f"Vídeo não encontrado: {batch}/{slug} (pastas aprovadas com prefixo OK não são alteradas).")
    return folder


def _script(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "motion-script.json").read_text(encoding="utf-8"))


def _active_jobs(batch: str, slug: str) -> list[dict[str, Any]]:
    active = []
    for path in _job_files():
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        params = job.get("params", {})
        job_slug = params.get("video_slug") or params.get("script", {}).get("slug")
        if (job["state"] in ("queued", "running") and params.get("batch_id") == batch and job_slug == slug
                and _job_alive(job)):
            active.append(job)
    return active


def _require_idle(batch: str, slug: str) -> None:
    if _active_jobs(batch, slug) or remount_running(video_folder(batch, slug)):
        raise RuntimeError("Este vídeo tem um job rodando ou na fila; espere terminar.")


def read_state(folder: Path) -> dict[str, Any]:
    path = folder / STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(folder: Path, stage: str | None, note: str = "", action: str = "") -> dict[str, Any]:
    state = read_state(folder)
    state.setdefault("history", []).append({"at": _now(), "action": action or stage, "note": note})
    state["stage"], state["note"], state["updatedAt"] = stage, note, _now()
    (folder / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def _scene_numbers(folder: Path, scenes: list[int]) -> list[int]:
    total = len(_script(folder)["scenes"])
    numbers = sorted({int(number) for number in scenes})
    if not numbers or any(not 1 <= number <= total for number in numbers):
        raise ValueError(f"Escolha cenas entre 1 e {total}.")
    return numbers


def _job_params(batch: str, slug: str, **extra: Any) -> dict[str, Any]:
    return {"influencer_name": INFLUENCER, "batch_id": batch, "video_slug": slug, **extra}


def refazer_cenas(batch: str, slug: str, scenes: list[int], urgent: bool = False) -> dict[str, Any]:
    """Gera imagens-chave novas (outra semente) para as cenas, com o mesmo prompt."""
    folder = video_folder(batch, slug)
    _require_idle(batch, slug)
    numbers = _scene_numbers(folder, scenes)
    script = _script(folder)
    for number in numbers:
        # Semente fixa repetiria a mesma imagem; sem ela, o job sorteia outra.
        script["scenes"][number - 1].pop("seed", None)
    # Vídeo já pronto: depois das imagens, anima só essas cenas e remonta o PT (e os idiomas ativos) com os metadados existentes
    # (pedido do usuário, 2026-10-06: antes só gerava as imagens e o vídeo ficava desatualizado).
    finished = (folder / "short.mp4").is_file()
    job = start_job("keyframes", {"influencer_name": INFLUENCER, "batch_id": batch, "script": script,
                                  "scene_numbers": numbers, "thenAnimate": finished, "urgent": urgent})
    note = f"refazer cenas {numbers}: imagens, animação e vídeo" if finished else f"refazer imagens-chave das cenas {numbers}"
    write_state(folder, None, note, "refazer_cenas")
    message = (f"Cenas {numbers} na fila: imagens novas, depois animação dessas cenas e o vídeo remontado."
               if finished else f"Imagens-chave das cenas {numbers} na fila.")
    return {"jobId": job["id"], "message": message}


def reanimar_cenas(batch: str, slug: str, scenes: list[int]) -> dict[str, Any]:
    folder = video_folder(batch, slug)
    _require_idle(batch, slug)
    numbers = _scene_numbers(folder, scenes)
    job = start_job("animation", _job_params(batch, slug, scene_numbers=numbers))
    write_state(folder, None, f"reanimar cenas {numbers}", "reanimar_cenas")
    return {"jobId": job["id"], "message": f"Animação das cenas {numbers} na fila; o vídeo é remontado no fim."}


def aprovar_imagens(batch: str, slug: str) -> dict[str, Any]:
    folder = video_folder(batch, slug)
    _require_idle(batch, slug)
    if not (folder / "keyframes-sheet.png").is_file():
        raise RuntimeError("Ainda não há imagens-chave para aprovar.")
    job = start_job("animation", _job_params(batch, slug))
    write_state(folder, None, "imagens-chave aprovadas", "aprovar_imagens")
    return {"jobId": job["id"], "message": "Imagens-chave aprovadas; animação na fila."}


def waive_minimum_duration(script: dict[str, Any]) -> None:
    """Cena removida pelo usuário encurta o vídeo de propósito: a duração mínima deixa de valer (o máximo continua).
    Antes a animação recusava o vídeo abaixo de 52 s e ele nunca saía (2026-10-06)."""
    if script.get("targetSeconds"):
        script["targetSeconds"] = [0, script["targetSeconds"][1]]


def remover_cena(batch: str, slug: str, scene: int) -> dict[str, Any]:
    """Tira a cena do roteiro e renumera as pastas seguintes, preservando imagens, áudios e clipes já feitos."""
    folder = video_folder(batch, slug)
    _require_idle(batch, slug)
    script = _script(folder)
    total = len(script["scenes"])
    (number,) = _scene_numbers(folder, [scene])
    if total - 1 < MIN_SCENES:
        raise ValueError(f"O vídeo precisa de pelo menos {MIN_SCENES} cenas.")
    removed_dir = folder / "removidas"
    removed_dir.mkdir(exist_ok=True)
    source = folder / f"scene-{number:02d}"
    if source.is_dir():
        source.rename(removed_dir / f"scene-{number:02d}-{int(time.time())}")
    for index in range(number + 1, total + 1):
        current = folder / f"scene-{index:02d}"
        if current.is_dir():
            current.rename(folder / f"scene-{index - 1:02d}")
    script["scenes"].pop(number - 1)
    waive_minimum_duration(script)
    (folder / "motion-script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for language in LANGUAGES:
        path = translation_path(folder, language)
        if path.is_file():
            translation = json.loads(path.read_text(encoding="utf-8"))
            translation["scenes"] = [
                {**item, "scene": item["scene"] - (item["scene"] > number)}
                for item in translation["scenes"] if item["scene"] != number
            ]
            path.write_text(json.dumps(translation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    message = f"Cena {number} removida; {total - 1} cenas."
    if (folder / "keyframes-sheet.png").is_file():
        from .motion import keyframe_sheet

        keyframe_sheet(folder, script)
    if (folder / "short.mp4").is_file():
        # O vídeo pronto ficou desatualizado: remonta só com os trechos já animados, sem GPU e sem fila
        # (pedido do usuário, 2026-10-05: antes ia para a fila da GPU e esperava horas atrás das animações).
        remontar_video(folder)
        message += " Vídeo sendo remontado com os trechos já prontos (sem GPU)."
    write_state(folder, None, f"cena {number} removida", "remover_cena")
    return {"jobId": None, "message": message}


def remount_running(folder: Path) -> bool:
    pid_file = folder / "remount.pid"
    return pid_file.is_file() and _pid_alive(int(pid_file.read_text() or 0))


def remontar_video(folder: Path) -> None:
    """Junta os trechos já animados (PT, depois FR/EN) num processo só de CPU, fora da fila da GPU."""
    log = open(folder / "remount.log", "wb")  # noqa: SIM115 - herdado pelo processo filho
    process = _spawn([sys.executable, str(PROJECT_ROOT / "scripts" / "remontar_video.py"), str(folder)],
                     log, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    (folder / "remount.pid").write_text(str(process.pid), encoding="utf-8")


def remontar_idioma(batch: str, slug: str, language: str) -> dict[str, Any]:
    """Remonta a versão traduzida trocando só o áudio (voz gerada na CPU para não disputar a GPU)."""
    folder = video_folder(batch, slug)
    if language not in LANGUAGES:
        raise ValueError(f"Idioma sem suporte: {language!r}.")
    require_enabled(language)
    if not (folder / "short.mp4").is_file():
        raise RuntimeError("O vídeo em português ainda não está pronto.")
    if not translation_path(folder, language).is_file():
        raise RuntimeError(f"Não há tradução salva ({translation_path(folder, language).name}).")
    if language_build_running(folder, language):
        raise RuntimeError("Essa versão já está sendo montada.")
    work = folder / language
    work.mkdir(exist_ok=True)
    log = open(folder / f"{language}-build.log", "wb")  # noqa: SIM115 - herdado pelo processo filho
    process = _spawn(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "localize_short.py"), str(folder), str(translation_path(folder, language))],
        log, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
    )
    (work / "build.pid").write_text(str(process.pid), encoding="utf-8")
    return {"message": f"Versão {LANGUAGES[language]['suffix']} sendo montada (alguns minutos)."}


def language_build_running(folder: Path, language: str) -> bool:
    pid_file = folder / language / "build.pid"
    return pid_file.is_file() and _pid_alive(int(pid_file.read_text() or 0))


def salvar_metadados(batch: str, slug: str, fields: dict[str, Any]) -> dict[str, Any]:
    folder = video_folder(batch, slug)

    def as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in re.split(r"[,\n]", str(value or "")) if item.strip()]

    save_publish_metadata(
        influencer_name=INFLUENCER, video_slug=slug, batch_id=batch,
        title=str(fields.get("title", "")), description=str(fields.get("description", "")),
        hashtags=as_list(fields.get("hashtags")), tags=as_list(fields.get("tags")),
        pinned_comment=str(fields.get("pinned_comment", "")),
    )
    write_state(folder, read_state(folder).get("stage"), "metadados editados", "salvar_metadados")
    return {"message": "Metadados salvos (TXT e output/ atualizados)."}


def definir_estado(batch: str, slug: str, stage: str, note: str = "") -> dict[str, Any]:
    from .youtube_publish import read_publication

    folder = video_folder(batch, slug)
    publication = read_publication(folder) or {}
    if publication.get("status") in ("enviando", "agendado", "publicado"):
        raise RuntimeError("O vídeo já foi enviado ao YouTube; para tirá-lo da grade use \"Cancelar publicação\".")
    if stage not in REVIEW_STAGES:
        raise ValueError(f"Estado inválido: {stage!r}.")
    if stage == "Aprovado" and not (folder / "short.mp4").is_file():
        raise RuntimeError("Só dá para aprovar depois que o vídeo estiver pronto.")
    write_state(folder, stage, note.strip()[:2000], stage)
    return {"message": f"Vídeo marcado como {stage}."}


# Vídeo rejeitado sai da grade na hora e tem os arquivos apagados 5 segundos depois, no próximo ciclo do vigia
# (pedido do usuário: 5 minutos em 2026-10-03, 5 segundos em 2026-10-09).
REJECTED_GRACE_SECONDS = 5


def _rejected_at(state: dict[str, Any]) -> datetime | None:
    if state.get("stage") != "Rejeitado":
        return None
    for entry in reversed(state.get("history", [])):
        if entry.get("action") == "Rejeitado":
            return datetime.fromisoformat(entry["at"])
    return datetime.fromisoformat(state["updatedAt"]) if state.get("updatedAt") else None


def _stop_jobs(batch: str, slug: str) -> list[str]:
    """Apaga os jobs do vídeo e encerra os processos deles (o arquivo some antes, para o vigia não relançar)."""
    stopped = []
    for job in _active_jobs(batch, slug):
        path = JOBS_DIR / f"{job['id']}.json"
        for extension in (".json", ".tmp"):
            path.with_suffix(extension).unlink(missing_ok=True)
        pid = int(job.get("pid") or 0)
        if pid and _job_alive(job):
            holder = (JOBS_DIR / "gpu.lock").read_text().strip() if (JOBS_DIR / "gpu.lock").is_file() else ""
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **quiet())
            if holder == str(pid):
                # Era quem usava a GPU: interrompe o que ele deixou rodando no ComfyUI.
                from .media import _comfyui_url, _request_json

                try:
                    _request_json(f"{_comfyui_url(require_avatar_checkpoint=False)}/interrupt", {}, timeout=10)
                except Exception:  # noqa: BLE001 - ComfyUI fora do ar: nada a interromper
                    pass
        try:
            path.with_suffix(".log").unlink(missing_ok=True)
        except OSError:  # o processo encerrado ainda segura o log por instantes (Windows); fica para a limpeza
            pass
        stopped.append(job["id"])
    return stopped


def limpar_rejeitados(now: datetime | None = None) -> list[str]:
    """Exclui (pasta, jobs e arquivos em output/) os vídeos rejeitados há pelo menos 5 minutos."""
    from .youtube_publish import video_folders

    now = now or datetime.now(UTC)
    removed = []
    for folder in video_folders():
        rejected = _rejected_at(read_state(folder))
        if not rejected or (now - rejected).total_seconds() < REJECTED_GRACE_SECONDS:
            continue
        batch, slug = folder.parent.name, folder.name
        _stop_jobs(batch, slug)
        try:
            excluir_video(batch, slug)
        except RuntimeError as error:
            # Já está no YouTube ou marcado "OK - ": não apaga; registra uma vez para aparecer no painel.
            state = read_state(folder)
            if state.get("autoDeleteBlocked") != str(error):
                state["autoDeleteBlocked"] = str(error)
                (folder / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        removed.append(slug)
    return removed


def conectar_canal() -> dict[str, Any]:
    """Abre o login do Google no navegador; o painel segue respondendo enquanto isso."""
    import threading

    from . import youtube_publish

    if not youtube_publish.CLIENT_SECRET.is_file():
        raise RuntimeError(f"Antes, salve a credencial OAuth baixada do Google Cloud em {youtube_publish.CLIENT_SECRET}.")
    threading.Thread(target=_connect_quietly, daemon=True).start()
    return {"message": "Abrindo o login do Google no navegador. Autorize o canal e volte ao painel."}


CONNECT_ERROR: list[str] = []


def _connect_quietly() -> None:
    from . import youtube_publish

    try:
        youtube_publish.connect()
        CONNECT_ERROR.clear()
    except Exception as error:  # noqa: BLE001 - mostrado no painel
        CONNECT_ERROR[:] = [str(error)]


def cancelar_publicacao(batch: str, slug: str) -> dict[str, Any]:
    from . import youtube_publish

    folder = video_folder(batch, slug)
    youtube_publish.cancel(folder)
    write_state(folder, "Ajustes", "publicação cancelada (vídeo privado no YouTube)", "cancelar_publicacao")
    return {"message": "Publicação cancelada: o vídeo ficou privado no YouTube (não foi apagado)."}


def publicar_comentario(batch: str, slug: str) -> dict[str, Any]:
    from . import youtube_publish

    youtube_publish.post_comment(video_folder(batch, slug))
    return {"message": "Comentário publicado. Para fixá-lo, use o YouTube Studio (a API não permite fixar)."}


def excluir_video(batch: str, slug: str) -> dict[str, Any]:
    """Apaga o vídeo por completo (regra "limpar ao cancelar"): pasta, jobs, arquivos em output/ e o lote vazio.

    Recusa se houver job ativo ou se o vídeo já existir no YouTube ou estiver marcado como publicado ("OK - "),
    para o painel não perder o controle de algo que está fora deste computador.
    """
    from .output_ok import output_base, output_root, published_in_output
    from .youtube_publish import read_publication

    folder = video_folder(batch, slug)
    _require_idle(batch, slug)
    record = read_publication(folder) or {}
    if record.get("videoId") or record.get("status") in ("enviando", "agendado", "publicado"):
        raise RuntimeError("Este vídeo já está no YouTube (enviado, agendado ou privado). Apague-o no YouTube Studio "
                           "antes, se quiser; o painel não exclui vídeos enviados.")
    if published_in_output(folder) or any(published_in_output(folder, language) for language in LANGUAGES):
        raise RuntimeError("Este vídeo está marcado como publicado (\"OK - \" em output/) e não pode ser excluído aqui.")
    removed_outputs = []
    for language in (None, *LANGUAGES):
        base = output_base(folder, language)
        if not base:
            continue
        for folder_out in (output_root(), output_root() / "shorts", output_root() / "videos",
                           output_root() / "archive" / "shorts", output_root() / "archive" / "videos"):
            for suffix in (".mp4", ".txt", ".jpg"):
                path = folder_out / f"{base}{suffix}"
                if path.is_file():
                    path.unlink()
                    removed_outputs.append(path.name)
    removed_jobs = []
    for path in _job_files():
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        params = job.get("params", {})
        if params.get("batch_id") == batch and (params.get("video_slug") or params.get("script", {}).get("slug")) == slug:
            for extension in (".json", ".log", ".tmp"):
                path.with_suffix(extension).unlink(missing_ok=True)
            removed_jobs.append(job["id"])
    shutil.rmtree(folder)
    batch_dir = folder.parent
    batch_removed = False
    if batch_dir.is_dir() and not any(batch_dir.iterdir()):
        batch_dir.rmdir()
        batch_removed = True
        if LOTES_DIR.is_dir():
            for path in LOTES_DIR.glob("*.json"):
                try:
                    lote = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if lote.get("batchId") == batch and not pid_alive_since(int(lote.get("pid") or 0), lote.get("startedAt")):
                    path.unlink(missing_ok=True)
                    path.with_suffix(".log").unlink(missing_ok=True)
    from .historico import unregister_subject

    unregister_subject(batch, slug)  # nunca publicado: o assunto volta a ficar livre (assuntos.md)
    return {"message": f"Vídeo excluído: pasta, {len(removed_jobs)} job(s) e {len(removed_outputs)} arquivo(s) de output/"
                       + (" e o lote vazio." if batch_removed else "."),
            "removedJobs": removed_jobs, "removedOutputs": removed_outputs}


def refazer_capa(batch: str, slug: str) -> dict[str, Any]:
    """Gera outra ilustração para a capa do vídeo horizontal (as cenas não mudam)."""
    from .formats import of_folder

    folder = video_folder(batch, slug)
    _require_idle(batch, slug)
    if of_folder(folder) != "video":
        raise RuntimeError("Só vídeos horizontais têm capa.")
    base = folder / "thumbnail-base.png"
    if base.is_file():
        base.rename(folder / f"thumbnail-base-rejeitada-{int(time.time())}.png")
    job = start_job("keyframes", {"influencer_name": INFLUENCER, "batch_id": batch, "script": _script(folder)})
    write_state(folder, None, "capa refeita", "refazer_capa")
    return {"jobId": job["id"], "message": "Capa nova na fila (cerca de 1 minuto de GPU)."}


def limpar_arquivados() -> dict[str, Any]:
    """Apaga as pastas de trabalho (cenas, quadros, clipes, áudios) dos vídeos arquivados (PT e os idiomas ativos).

    Os entregáveis em output/ ficam intactos; o registro de envio ao YouTube vai para o histórico antes.
    """
    from .output_ok import fully_archived
    from .youtube_publish import archive_record, comment_pending, video_folders

    cleaned, skipped = [], []
    for folder in video_folders():
        batch, slug = folder.parent.name, folder.name
        if not fully_archived(folder):
            continue
        if _active_jobs(batch, slug) or comment_pending(folder):
            # Agendado ou no ar sem o comentário: a pasta precisa ficar até o comentário sair (sem ela o publicador
            # deixava de acompanhar o vídeo e o comentário nunca era postado; 2026-10-06).
            skipped.append(slug)
            continue
        archive_record(folder)
        for path in _job_files():
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            params = job.get("params", {})
            if params.get("batch_id") == batch and (params.get("video_slug") or params.get("script", {}).get("slug")) == slug:
                for extension in (".json", ".log", ".tmp"):
                    path.with_suffix(extension).unlink(missing_ok=True)
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            skipped.append(slug)  # algum arquivo em uso; o resto foi apagado, tenta de novo no próximo arquivamento
            continue
        cleaned.append(slug)
        batch_dir = folder.parent
        if batch_dir.is_dir() and not any(batch_dir.iterdir()):
            batch_dir.rmdir()
            if LOTES_DIR.is_dir():
                for path in LOTES_DIR.glob("*.json"):
                    try:
                        lote = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if lote.get("batchId") == batch and not pid_alive_since(int(lote.get("pid") or 0), lote.get("startedAt")):
                        path.unlink(missing_ok=True)
                        path.with_suffix(".log").unlink(missing_ok=True)
    return {"cleaned": cleaned, "skipped": skipped}


def limpar_idiomas_desligados() -> dict[str, Any]:
    """Apaga das pastas de trabalho as traduções e as versões (fr/, en/) de idiomas desativados em "- idiomas:".

    Não toca em output/ nem em vídeos com job ativo ou versão sendo montada."""
    from .youtube_publish import video_folders

    active = set(enabled_languages())
    removed, skipped = [], []
    for folder in video_folders():
        batch, slug = folder.parent.name, folder.name
        for language in LANGUAGES:
            if language in active:
                continue
            translation, work = translation_path(folder, language), folder / language
            if not translation.is_file() and not work.is_dir():
                continue
            if _active_jobs(batch, slug) or language_build_running(folder, language):
                skipped.append(f"{slug} ({language})")
                continue
            translation.unlink(missing_ok=True)
            (folder / f"{language}-build.log").unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)
            removed.append(f"{slug} ({language})")
    message = f"{len(removed)} tradução(ões)/versão(ões) de idiomas desligados apagadas."
    if skipped:
        message += f" {len(skipped)} puladas (job ativo ou versão sendo montada)."
    return {"removed": removed, "skipped": skipped, "message": message}


def arquivar_publicados() -> dict[str, Any]:
    from .output_ok import archive_published

    result = archive_published()
    total = len(result["shorts"]) + len(result["videos"])
    message = f"Arquivados {total} arquivo(s): {len(result['shorts'])} de Shorts e {len(result['videos'])} de vídeos."
    if result["failed"]:
        message += f" {len(result['failed'])} em uso não puderam ser movidos (feche o player e tente de novo)."
    cleanup = limpar_arquivados()
    if cleanup["cleaned"]:
        message += f" {len(cleanup['cleaned'])} vídeo(s) arquivados foram limpos e saíram da tela."
    if cleanup["skipped"]:
        message += f" {len(cleanup['skipped'])} não puderam ser limpos agora (job ativo, comentário do YouTube ainda não postado ou arquivo em uso)."
    return {**result, **cleanup, "message": message}


def _job_file(job_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
        raise ValueError("Job inválido.")
    path = JOBS_DIR / f"{job_id}.json"
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Esse job não existe mais.") from error


def pausar_job(job_id: str) -> dict[str, Any]:
    from .motion_jobs import pause_path

    _, job = _job_file(job_id)
    if job["state"] not in ("queued", "running"):
        raise RuntimeError("Esse job já terminou.")
    pause_path(job_id).write_text(_now(), encoding="utf-8")
    if job["state"] == "running":
        return {"message": "Pausa pedida: o job para no próximo ponto seguro (fim do clipe ou da imagem) e libera a GPU."}
    return {"message": "Job pausado: os outros da fila passam na frente."}


def retomar_job(job_id: str) -> dict[str, Any]:
    from .motion_jobs import pause_path

    _job_file(job_id)
    pause_path(job_id).unlink(missing_ok=True)
    return {"message": "Job retomado: continua de onde parou quando a GPU estiver livre."}


def mover_gpu(job_id: str, gpu: str) -> dict[str, Any]:
    """Passa um job que ainda espera na fila para a outra GPU (local/remota), sem perder nada do vídeo
    (pedido de 2026-10-06). Job rodando não muda: a animação continua de onde parou se for movida depois."""
    from .motion_jobs import TARGETS, job_target, pause_path

    if gpu not in TARGETS:
        raise ValueError("GPU inválida (local ou remota).")
    path, job = _job_file(job_id)
    if job.get("state") != "queued":
        raise RuntimeError("Só dá para mover um job que ainda espera na fila (o que já está na GPU termina nela).")
    if job_target(job) == gpu:
        return {"jobId": job_id, "message": f"O job já está na GPU {gpu}."}
    params = {**job.get("params", {}), "gpu": gpu}
    batch, slug = params.get("batch_id"), params.get("video_slug") or params.get("script", {}).get("slug")
    if job["kind"] == "keyframes":
        params["script"] = {**params["script"], "gpu": gpu}
    folder = GENERATED_ROOT / _slug(INFLUENCER) / batch / slug
    script_path = folder / "motion-script.json"
    if script_path.is_file():  # refazer cenas e animar seguem na mesma GPU do vídeo
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["gpu"] = gpu
        script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _stop_jobs(batch, slug)
    pause_path(job_id).unlink(missing_ok=True)
    new = start_job(job["kind"], params)
    if folder.is_dir():
        write_state(folder, read_state(folder).get("stage"), f"job movido para a GPU {gpu}", "mover_gpu")
    return {"jobId": new["id"], "message": f"Job movido para a fila da GPU {gpu}."}


def cancelar_job(job_id: str) -> dict[str, Any]:
    """Encerra o job e apaga o vídeo dele por completo (pasta, jobs e cópias em output/)."""
    from .motion_jobs import pause_path

    _, job = _job_file(job_id)
    params = job.get("params", {})
    batch, slug = params.get("batch_id"), params.get("video_slug") or params.get("script", {}).get("slug")
    _stop_jobs(batch, slug)
    pause_path(job_id).unlink(missing_ok=True)
    try:
        excluir_video(batch, slug)
    except FileNotFoundError:
        # Job cancelado antes da primeira imagem: pode haver só SEO/traduções salvos; apaga a pasta assim mesmo.
        if BATCH_RE.match(batch or "") and SLUG_RE.match(slug or ""):
            shutil.rmtree(GENERATED_ROOT / _slug(INFLUENCER) / batch / slug, ignore_errors=True)
    except (RuntimeError, ValueError) as error:
        return {"message": f"Job cancelado. Os arquivos do vídeo foram mantidos: {error}"}
    return {"message": f"Job cancelado e vídeo {slug} excluído com todos os arquivos."}


def desligar_gpu_remota() -> dict[str, Any]:
    """Só o usuário desliga a máquina remota (decisão de 2026-10-03)."""
    from .gpu_remota import configured, shutdown
    from .motion_jobs import job_target, list_jobs

    if not configured():
        raise RuntimeError("GPU remota não configurada.")
    if any(job["state"] in ("queued", "running") and job_target(job) == "remota" for job in list_jobs()):
        raise RuntimeError("Há jobs na GPU remota; pause ou cancele antes de desligar.")
    shutdown()
    return {"message": "GPU remota desligada. Ela liga de novo sozinha quando entrar um vídeo remoto."}


def desligar_comfyui() -> dict[str, Any]:
    """Desliga o ComfyUI local para liberar GPU e memória; os jobs o iniciam de novo sozinhos."""
    from . import comfyui
    from .motion_jobs import job_target, list_jobs

    if any(job["state"] in ("queued", "running") and job_target(job) == "local" for job in list_jobs()):
        raise RuntimeError("Há jobs na GPU local; pause ou cancele antes de desligar o ComfyUI.")
    if not comfyui.stop():
        return {"message": "O ComfyUI já estava desligado."}
    return {"message": "ComfyUI desligado. Ele liga de novo sozinho quando entrar um vídeo local."}


ACTIONS = {
    "desligar_comfyui": lambda body: desligar_comfyui(),
    "desligar_gpu_remota": lambda body: desligar_gpu_remota(),
    "pausar_job": lambda body: pausar_job(body.get("job", "")),
    "retomar_job": lambda body: retomar_job(body.get("job", "")),
    "cancelar_job": lambda body: cancelar_job(body.get("job", "")),
    "mover_gpu": lambda body: mover_gpu(body.get("job", ""), body.get("gpu", "")),
    "arquivar_publicados": lambda body: arquivar_publicados(),
    "limpar_idiomas_desligados": lambda body: limpar_idiomas_desligados(),
    "refazer_capa": lambda body: refazer_capa(body["batch"], body["slug"]),
    "excluir_video": lambda body: excluir_video(body["batch"], body["slug"]),
    "conectar_canal": lambda body: conectar_canal(),
    "cancelar_publicacao": lambda body: cancelar_publicacao(body["batch"], body["slug"]),
    "publicar_comentario": lambda body: publicar_comentario(body["batch"], body["slug"]),
    "refazer_cenas": lambda body: refazer_cenas(body["batch"], body["slug"], body.get("scenes", []), bool(body.get("urgent"))),
    "reanimar_cenas": lambda body: reanimar_cenas(body["batch"], body["slug"], body.get("scenes", [])),
    "aprovar_imagens": lambda body: aprovar_imagens(body["batch"], body["slug"]),
    "remover_cena": lambda body: remover_cena(body["batch"], body["slug"], int(body["scene"])),
    "remontar_idioma": lambda body: remontar_idioma(body["batch"], body["slug"], body.get("language", "")),
    "salvar_metadados": lambda body: salvar_metadados(body["batch"], body["slug"], body.get("fields", {})),
    "definir_estado": lambda body: definir_estado(body["batch"], body["slug"], body.get("stage", ""), body.get("note", "")),
}


def run_action(body: dict[str, Any]) -> dict[str, Any]:
    action = ACTIONS.get(body.get("action", ""))
    if action is None:
        raise ValueError("Ação desconhecida.")
    return action(body)


def _spawn(args: list[str], log: Any, *, env: dict[str, str] | None = None) -> subprocess.Popen:
    """Processo destacado do painel: continua rodando se o painel for fechado."""
    launch = dict(args=args, cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT,
                  stdin=subprocess.DEVNULL, close_fds=True, env=env or dict(os.environ))
    if os.name == "nt":
        try:
            return subprocess.Popen(**launch, **detached(breakaway=True))
        except OSError:
            return subprocess.Popen(**launch, **detached())
    return subprocess.Popen(**launch, **detached())


def claude_executable() -> str:
    found = shutil.which("claude")
    if not found:
        raise RuntimeError("Claude Code (claude) não encontrado no PATH.")
    return found


def _character_instruction(plan: list[str], characters: dict[str, dict[str, str]]) -> str:
    """Personagem de cada vídeo do lote (o automático alterna para manter ~1 homem a cada 3 vídeos)."""
    if len(set(plan)) == 1:
        item = characters[plan[0]]
        return (f"Personagem deste lote: {item['label']} (perfil visual {plan[0]}); grave \"visualProfile\": "
                f"\"{plan[0]}\" em todo roteiro; a narração segue em segunda pessoa (\"você\"), com a concordância no {item['gender']}.")
    parts = [f"vídeo {index}: {characters[name]['label']} (\"visualProfile\": \"{name}\", narração no "
             f"{characters[name]['gender']})" for index, name in enumerate(plan, 1)]
    return "Personagem de cada vídeo deste lote, nesta ordem: " + "; ".join(parts) + "."


# Análise de 2026-10-06: com 10 a 14 vídeos por dia a mediana caiu de ~100 para ~20 visualizações por vídeo.
DEFAULT_DAILY_LIMIT = 3


def daily_limit() -> int | None:
    """Vídeos novos por dia (linha "- videos-por-dia: N" de fluxo_aprovacao.md; padrão 3).

    "sem limite", "ilimitado" ou 0 desligam a trava (pedido do usuário, 2026-10-07).
    """
    from .flags import flag

    value = flag("videos-por-dia")
    if value is None:
        return DEFAULT_DAILY_LIMIT
    value = value.lower()
    if re.fullmatch(r"sem limite|ilimitado|0", value):
        return None
    return int(value) if value.isdigit() else DEFAULT_DAILY_LIMIT


def videos_started_today(now: datetime | None = None) -> int:
    """Vídeos pedidos hoje (soma das quantidades dos lotes iniciados no dia, no fuso deste computador)."""
    today = (now or datetime.now()).astimezone().date()
    total = 0
    for path in LOTES_DIR.glob("*.json") if LOTES_DIR.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(record["startedAt"]).astimezone().date()
        except (OSError, ValueError, KeyError):
            continue
        if started == today:
            total += int(record.get("quantity") or 0)
    return total


def _spawn_lote(prompt: str, batch_id: str, plan: list[str], characters: dict[str, Any], log_path: Path):
    log = open(log_path, "wb")  # noqa: SIM115 - herdado pelo processo filho
    return _spawn(
        [claude_executable(), "-p", prompt, "--model", LOTE_MODEL, "--mcp-config", str(PROJECT_ROOT / ".mcp.json"), "--strict-mcp-config",
         "--permission-mode", "acceptEdits", "--allowedTools", *LOTE_TOOLS,
         "--append-system-prompt",
         f"Use exatamente o batch_id {batch_id} neste lote. " + _character_instruction(plan, characters)],
        # Sem janela, o Claude desiste das tarefas em segundo plano após 10 min; as imagens-chave podem esperar horas
        # na fila da GPU, então o lote precisa esperar sem limite.
        # MCP_TOOL_TIMEOUT: start_motion_keyframes narra na hora (~1 min, mais com vários lotes na CPU).
        log, env={**os.environ, "CLAUDE_PROJECT_DIR": str(PROJECT_ROOT), "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
                  "MCP_TOOL_TIMEOUT": "900000"},
    )


LOTE_LIMIT_RETRY_SECONDS = 60 * 60
LOTE_LIMIT_MAX_AGE_SECONDS = 8 * 24 * 3600


def retomar_lotes_interrompidos(now: datetime | None = None) -> list[str]:
    """Refaz, a cada hora, lotes que pararam por limite de uso antes de gerar qualquer vídeo (até 8 dias depois)."""
    from .perfis import selectable

    if not LOTES_DIR.is_dir():
        return []
    now = now or datetime.now(UTC)
    characters = None
    resumed = []
    for path in LOTES_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(record["startedAt"])
        except (OSError, ValueError, KeyError):
            continue
        if record.get("cancelled"):
            continue  # lote parado pelo usuário: não volta sozinho
        age = (now - started).total_seconds()
        log = path.with_suffix(".log")
        if age < LOTE_LIMIT_RETRY_SECONDS or age > LOTE_LIMIT_MAX_AGE_SECONDS or not log.is_file():
            continue
        if pid_alive_since(int(record.get("pid") or 0), record.get("startedAt")) or not _limit_hit(log.read_text(encoding="utf-8", errors="replace")):
            continue
        batch = record.get("batchId") or ""
        if not batch or _batch_has_video(batch):
            continue  # com vídeos já criados, refazer duplicaria; o usuário decide
        characters = characters or {item["id"]: item for item in selectable()}
        profile = record.get("profile")
        if profile not in characters:
            continue
        process = _spawn_lote(record["prompt"], batch, [profile] * int(record.get("quantity") or 1), characters, log)
        record.update({"pid": process.pid, "startedAt": _now(), "resumes": int(record.get("resumes") or 0) + 1})
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
        resumed.append(batch)
    return resumed


def _batch_has_video(batch: str) -> bool:
    if any((GENERATED_ROOT / _slug(INFLUENCER) / batch).glob("*")):
        return True
    for job_path in _job_files() if JOBS_DIR.is_dir() else []:
        try:
            if json.loads(job_path.read_text(encoding="utf-8")).get("params", {}).get("batch_id") == batch:
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def iniciar_lote(quantity: int, theme: str, fmt: str = "short", profile: str | None = None,
                 gpu: str = "local", identity: str | None = None) -> dict[str, Any]:
    """Dispara /gerar-lote no Claude Code sem janela (uma sessão: temas, roteiros e SEO); o vídeo para na revisão."""
    from .perfis import AUTO, DEFAULT_IDENTITY, active_name, balanced_plan, get, identities, selectable

    profile = profile or active_name()
    if profile != AUTO:
        # O personagem já tem a sua identidade; a escolhida no painel só decide o "Automático" (e uma página antiga,
        # sem o seletor de identidade, não manda nenhuma).
        try:
            identity = get(profile)["identity"]
        except KeyError:
            raise ValueError("Personagem inválido (confira personagens.md).") from None
    identity = identity or DEFAULT_IDENTITY
    labels = {item["id"]: item["label"] for item in identities()}
    if identity not in labels:
        raise ValueError("Identidade visual inválida (confira identidade_visual.md).")
    # personagens.md, "selecionavel: sim", só os da identidade escolhida
    characters = {item["id"]: item for item in selectable(identity)}
    if profile not in characters:
        raise ValueError("Personagem inválido para esta identidade visual (confira personagens.md).")
    plan = balanced_plan(int(quantity), identity=identity) if profile == AUTO else [profile] * int(quantity)
    profile = plan[0]
    character = characters[profile]
    if gpu not in ("local", "remota"):
        raise ValueError("GPU inválida (local ou remota).")
    from .ambiente import local_gpu, remote_checks, remote_ready

    if gpu == "local":
        local = local_gpu()
        if not local["available"]:
            raise RuntimeError("Este computador não tem GPU local utilizável: " + " ".join(local["problems"])
                               + " Use a GPU remota.")
    else:
        items = remote_checks(check_api=False)
        if not remote_ready(items):
            missing = [f"{item['label']}: {item['help']}" for item in items if not item["ok"] and item["id"] != "saldo"]
            raise RuntimeError("A GPU remota ainda não está pronta. Falta: " + " | ".join(missing))
    quantity = int(quantity)
    if not 1 <= quantity <= 5:
        raise ValueError("Quantidade entre 1 e 5 vídeos.")
    limit, started = daily_limit(), videos_started_today()
    if limit is not None and started + quantity > limit:
        raise RuntimeError(f"Limite de {limit} vídeos novos por dia: hoje já foram pedidos {started}. Mais vídeos no "
                           "mesmo dia dividem o público e cada um recebe menos visualizações (análise de 2026-10-06). "
                           "Amanhã libera de novo; para mudar o limite, edite \"videos-por-dia\" em fluxo_aprovacao.md.")
    theme = " ".join(re.sub(r"[\x00-\x1f]", " ", str(theme or "")).split())[:200]
    # Vários lotes podem rodar ao mesmo tempo (decisão do usuário, 2026-10-02): cada um tem pasta e jobs próprios,
    # e a GPU segue atendendo um job por vez, na ordem de chegada.
    LOTES_DIR.mkdir(parents=True, exist_ok=True)
    lote_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    batch_id = free_batch_id(datetime.now())
    from .formats import normalize

    fmt = normalize(fmt)
    prompt = f"/gerar-lote {quantity} {'video ' if fmt == 'video' else ''}{theme}".strip()
    process = _spawn_lote(prompt, batch_id, plan, characters, LOTES_DIR / f"{lote_id}.log")
    record = {"id": lote_id, "pid": process.pid, "quantity": quantity, "theme": theme, "prompt": prompt, "format": fmt,
              "batchId": batch_id, "profile": profile, "character": character["label"], "identity": identity,
              "identityLabel": labels[identity], "gpu": gpu, "startedAt": _now()}
    (LOTES_DIR / f"{lote_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"id": lote_id, "message": f"Lote iniciado ({labels[identity]} · {character['label']}): {prompt}"}


# SEO automático (pedido do usuário, 2026-10-03): o vigia do painel gera o SEO (PT e idiomas ativos) de todo vídeo que estiver sem,
# com um Claude sem janela por vídeo (/seo-video). Lotes que paravam antes do SEO deixavam o vídeo sem título,
# sem FR/EN e fora de output/. Desde 2026-10-14 o SEO vem dentro do roteiro e é salvo ao aceitá-lo, então o Claude
# só entra para vídeos antigos (roteiro sem "seo"); com "seo" no roteiro, o vigia salva pelo código.
SEO_DIR = GENERATED_ROOT / "seo"
SEO_MAX_ATTEMPTS = 3
SEO_RETRY_SECONDS = 20 * 60
SEO_LIMIT_RETRY_SECONDS = 60 * 60
SEO_MAX_PARALLEL = 2


def seo_missing(folder: Path) -> list[str]:
    missing = [] if (folder / "youtube-metadata.json").is_file() else ["pt"]
    return missing + [language for language in enabled_languages() if not translation_path(folder, language).is_file()]


def _seo_record_path(batch: str, slug: str) -> Path:
    return SEO_DIR / f"{batch}__{slug}.json"


def _limit_hit(text: str) -> bool:
    """O Claude parou por limite de uso (sessão, semanal ou geral), não por falha do trabalho."""
    low = text.lower()
    return any(m in low for m in ("hit your session limit", "hit your weekly limit", "hit your usage limit", "usage limit", "weekly limit"))


def seo_status(folder: Path) -> dict[str, Any]:
    """Situação do SEO automático deste vídeo, para o painel."""
    missing = seo_missing(folder)
    if not missing:
        return {"state": "ok"}
    path = _seo_record_path(folder.parent.name, folder.name)
    record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    running = bool(record) and pid_alive_since(int(record.get("pid") or 0), record.get("startedAt"))
    if running:
        return {"state": "gerando", "missing": missing}
    log = path.with_suffix(".log")
    limit_hit = log.is_file() and _limit_hit(log.read_text(encoding="utf-8", errors="replace"))
    if not limit_hit and record.get("attempts", 0) >= SEO_MAX_ATTEMPTS:
        return {"state": "erro", "missing": missing, "attempts": record["attempts"]}
    return {"state": "falta", "missing": missing, "attempts": record.get("attempts", 0)}


def _batch_running(batch: str) -> bool:
    if not LOTES_DIR.is_dir():
        return False
    for path in LOTES_DIR.glob("*.json"):
        try:
            lote = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if lote.get("batchId") == batch and pid_alive_since(int(lote.get("pid") or 0), lote.get("startedAt")):
            return True
    return False


def gerar_seo_pendente(now: datetime | None = None) -> list[str]:
    """Dispara /seo-video para vídeos sem SEO completo (no máximo 2 por vez, até 3 tentativas cada)."""
    from .youtube_publish import video_folders

    now = now or datetime.now(UTC)
    SEO_DIR.mkdir(parents=True, exist_ok=True)
    started, running = [], 0
    pending = []
    for folder in video_folders():
        if not (folder / "motion-script.json").is_file() or folder.name.upper().startswith("OK") or not seo_missing(folder):
            continue
        path = _seo_record_path(folder.parent.name, folder.name)
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if record and pid_alive_since(int(record.get("pid") or 0), record.get("startedAt")):
            running += 1
            continue
        pending.append((folder, path, record))
    for folder, path, record in pending:
        batch, slug = folder.parent.name, folder.name
        script = _script(folder)
        if "pt" in seo_missing(folder) and isinstance(script.get("seo"), dict):
            # Roteiro com o SEO dentro: salva sem IA; se ele estiver inválido, o Claude abaixo corrige.
            try:
                from .media import save_script_seo

                save_script_seo(INFLUENCER, batch, script)
            except ValueError:
                pass
            if not seo_missing(folder):
                started.append(slug)
                continue
        log = path.with_suffix(".log")
        text = log.read_text(encoding="utf-8", errors="replace") if record.get("startedAt") and log.is_file() else ""
        # Limite de uso do Claude não é falha do vídeo: a tentativa não conta (antes esgotava as 3 e virava "erro").
        limit_hit = _limit_hit(text)
        attempts = max(record.get("attempts", 0) - 1, 0) if limit_hit else record.get("attempts", 0)
        if running >= SEO_MAX_PARALLEL or attempts >= SEO_MAX_ATTEMPTS or _batch_running(batch):
            continue  # o próprio lote ainda pode salvar o SEO; o vigia só entra depois que ele termina
        if record.get("startedAt"):
            wait = SEO_LIMIT_RETRY_SECONDS if limit_hit else SEO_RETRY_SECONDS
            if (now - datetime.fromisoformat(record["startedAt"])).total_seconds() < wait:
                continue
        log = open(path.with_suffix(".log"), "wb")  # noqa: SIM115 - herdado pelo processo filho
        process = _spawn(
            [claude_executable(), "-p", f"/seo-video {batch} {slug}", "--model", LOTE_MODEL, "--mcp-config", str(PROJECT_ROOT / ".mcp.json"),
             "--strict-mcp-config", "--permission-mode", "acceptEdits", "--allowedTools", *LOTE_TOOLS],
            log, env={**os.environ, "CLAUDE_PROJECT_DIR": str(PROJECT_ROOT)},
        )
        path.write_text(json.dumps({"pid": process.pid, "startedAt": _now(), "attempts": attempts + 1,
                                    "missing": seo_missing(folder)}, ensure_ascii=False) + "\n", encoding="utf-8")
        started.append(slug)
        running += 1
    return started


def free_batch_id(now: datetime) -> str:
    """Nome de lote AAAA-MM-DD-HHMM ainda não usado: dois lotes iniciados no mesmo minuto não se misturam."""
    from datetime import timedelta

    taken = {path.name for path in (GENERATED_ROOT / _slug(INFLUENCER)).glob("*") if path.is_dir()}
    if LOTES_DIR.is_dir():
        for path in LOTES_DIR.glob("*.json"):
            try:
                taken.add(json.loads(path.read_text(encoding="utf-8")).get("batchId") or "")
            except (OSError, json.JSONDecodeError):
                continue
    moment = now
    while moment.strftime("%Y-%m-%d-%H%M") in taken:
        moment += timedelta(minutes=1)
    return moment.strftime("%Y-%m-%d-%H%M")


def list_lotes(limit: int = 8) -> list[dict[str, Any]]:
    if not LOTES_DIR.is_dir():
        return []
    rows = []
    for path in sorted(LOTES_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        log = path.with_suffix(".log")
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        record["running"] = pid_alive_since(int(record.get("pid") or 0), record.get("startedAt"))
        record["logTail"] = text[-4000:]
        # Lote que terminou sem nenhum vídeo (pasta ou job) aparece em vermelho, com o motivo do log.
        batch = record.get("batchId") or ""
        has_video = bool(batch) and _batch_has_video(batch)
        record["hasVideo"] = has_video
        record["limitHit"] = _limit_hit(text)
        rows.append(record)
    return rows
