"""Painel local (fase 1): fila da GPU, estado de cada vídeo e previsão de término. Só leitura, sem IA.

Lê o que o pipeline já grava: os jobs em generated/jobs/*.json e as pastas dos vídeos em
generated/<influenciador>/<lote>/<video>/. Serve uma página em http://127.0.0.1:8766 e a API /api/estado.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import traceback
import re
import statistics
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .ambiente import system_usage
from .localize import enabled_languages
from .media import GENERATED_ROOT, PROJECT_ROOT
from .motion_jobs import JOBS_DIR, _job_alive, is_paused, job_target
from .youtube_publish import Publisher, read_publication
from . import youtube_publish
from .painel_acoes import (iniciar_lote, language_build_running, list_lotes, read_state, remount_running, run_action,
                           seo_status, video_folder)

OUTPUT_ROOT = GENERATED_ROOT.parent / "output"
FLOW_PATH = PROJECT_ROOT / "fluxo_aprovacao.md"
PAGE_PATH = Path(__file__).with_name("painel.html")

# Valores de reserva quando ainda não há histórico suficiente.
DEFAULT_CLIP_SECONDS = 450.0
KEYFRAME_SECONDS = 45.0
SCENE_ASSEMBLY_SECONDS = 20.0
FINISH_SECONDS = 360.0  # montagem final + versões em outros idiomas

CLIP_RE = re.compile(r"clipe \d+/\d+ em (\d+)s")
ASSEMBLED_RE = re.compile(r"cena (\d+)/(\d+) montada")
KEYFRAME_RE = re.compile(r"imagem-chave da cena (\d+)/(\d+) pronta")

def _job_files() -> list[Path]:
    """Só arquivos de job (id alfanumérico): outros .json em generated/jobs não são jobs."""
    if not JOBS_DIR.is_dir():
        return []
    return [path for path in JOBS_DIR.glob("*.json") if re.fullmatch(r"[0-9A-Za-z]+", path.stem)]



def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_jobs() -> list[dict[str, Any]]:
    """Jobs crus (progresso completo). Queued/running com processo morto aparecem como failed."""
    jobs = []
    for path in _job_files():
        job = _read_json(path)
        if not job:
            continue
        if job["state"] in ("queued", "running") and not _job_alive(job):
            job["state"] = "failed"
        jobs.append(job)
    return sorted(jobs, key=lambda job: job.get("queuedAtNs", 0))


# Clipe medido na H100 remota (2026-10-03, ~115 s de GPU + download em paralelo): usado até haver histórico remoto.
REMOTE_DEFAULT_CLIP_SECONDS = 130


def clip_seconds(jobs: list[dict[str, Any]], recent: int = 60, default: float | None = None) -> float:
    """Mediana dos últimos clipes animados (a GPU esquenta e varia de 6 a 14 min por clipe)."""
    samples = [float(match.group(1)) for job in jobs if job["kind"] == "animation"
               for line in job.get("progress", []) if (match := CLIP_RE.search(line))]
    return statistics.median(samples[-recent:]) if samples else (default or DEFAULT_CLIP_SECONDS)


def _job_slug(job: dict[str, Any]) -> str:
    params = job.get("params", {})
    return params.get("video_slug") or params.get("script", {}).get("slug", "?")


def video_dir_for(job: dict[str, Any]) -> Path | None:
    params = job.get("params", {})
    from .media import _slug

    try:
        base = GENERATED_ROOT / _slug(params["influencer_name"]) / params["batch_id"]
    except (KeyError, ValueError):
        return None
    slug = _job_slug(job)
    direct = base / slug
    if direct.is_dir():
        return direct
    # Pastas aprovadas recebem o prefixo "OK".
    return next((path for path in base.glob(f"OK*{slug}") if path.is_dir()), None)


def remaining_seconds(job: dict[str, Any], clip: float) -> float:
    """Estimativa do que falta para o job terminar, pelo progresso registrado e pelos arquivos da pasta."""
    folder = video_dir_for(job)
    progress = job.get("progress", [])
    if job["kind"] == "keyframes":
        script = job["params"].get("script", {})
        scenes = script.get("scenes", [])
        selected = job["params"].get("scene_numbers")
        if selected:
            todo = len(selected)
        elif folder is not None:
            todo = sum(not (folder / f"scene-{n:02d}" / "keyframe.png").is_file() for n in range(1, len(scenes) + 1))
        else:
            todo = len(scenes)
        if job["state"] == "running":
            todo -= sum(bool(KEYFRAME_RE.search(line)) for line in progress)
        return max(todo, 0) * KEYFRAME_SECONDS + 30
    script = _read_json(folder / "motion-script.json") if folder else None
    scenes = (script or {}).get("scenes", [])
    selected = set(job["params"].get("scene_numbers") or [])
    pending = []
    for number, scene in enumerate(scenes, 1):
        if selected and number not in selected:
            continue
        if not selected and folder and (folder / f"scene-{number:02d}" / "segment.mp4").is_file():
            continue
        pending.append(len(scene.get("motions", [])) or 2)
    if job["state"] == "running" and pending:
        # Cenas montadas neste job já saíram da lista (segment.mp4 existe); descontar clipes da cena atual.
        last_assembled = max((i for i, line in enumerate(progress) if ASSEMBLED_RE.search(line)), default=-1)
        clips_in_current = sum(bool(CLIP_RE.search(line)) for line in progress[last_assembled + 1:])
        pending[0] = max(pending[0] - clips_in_current, 0)
    return sum(count * clip + SCENE_ASSEMBLY_SECONDS for count in pending) + FINISH_SECONDS


def queue(jobs: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Jobs ativos em ordem de execução, com início e término previstos.

    Cada GPU (local e remota) roda um job por vez, em fila própria; as previsões são calculadas por GPU.
    """
    active = [job for job in jobs if job["state"] in ("running", "queued")]
    active.sort(key=lambda job: (job_target(job) != "local", job["state"] != "running", job["kind"] != "keyframes",
                                 job.get("queuedAtNs", 0)))
    cursors, clips, rows = {}, {}, []
    for job in active:
        target = job_target(job)
        if target not in clips:
            same = [other for other in jobs if job_target(other) == target]
            # Cada GPU com o seu histórico; a remota, sem clipes medidos ainda, usa o tempo medido na H100
            # (antes caía no padrão da GPU local e mostrava ~3x o tempo real; 2026-10-04).
            clips[target] = clip_seconds(same, default=REMOTE_DEFAULT_CLIP_SECONDS if target == "remota" else None)
        clip = clips[target]
        cursor = cursors.get(target, now)
        remaining = remaining_seconds(job, clip)
        start = cursor if job["state"] == "queued" else _parse_time(job.get("startedAt")) or now
        finish = cursor + timedelta(seconds=remaining)
        cursors[target] = finish
        rows.append({
            "gpu": target,
            "id": job["id"], "kind": job["kind"], "state": job["state"], "paused": is_paused(job["id"]), "slug": _job_slug(job),
            "batch": job["params"].get("batch_id"), "startedAt": job.get("startedAt"),
            "expectedStart": start.isoformat(), "expectedFinish": finish.isoformat(),
            "remainingSeconds": round(remaining), "lastProgress": (job.get("progress") or [None])[-1],
        })
    return rows


# Decisão do usuário (2026-10-02): enviado ao YouTube com sucesso já conta como "Publicado", mesmo antes do horário.
PUBLICATION_STAGES = {"enviando": "Enviando ao YouTube", "agendado": "Publicado", "publicado": "Publicado",
                      "erro": "Erro no envio", "cancelado": "Cancelado (privado)"}


def _stale_adjustments(folder: Path, jobs_for_video: list[dict[str, Any]]) -> bool:
    """"Pedir ajustes" já não vale: nada existe para ajustar (job falhou antes de gerar imagens ou vídeo) ou um job
    mais novo que o pedido (refeito fora do painel, pelo MCP ou por script) já tratou dele. Antes o cartão ficava
    em "Ajustes" para sempre, mesmo com o vídeo refeito (2026-10-08)."""
    if not (folder / "short.mp4").is_file() and not (folder / "keyframes-sheet.png").is_file():
        return True
    asked = read_state(folder).get("updatedAt") or ""
    return any((job.get("createdAt") or "") > asked for job in jobs_for_video)


def video_stage(folder: Path, jobs_for_video: list[dict[str, Any]], review: str | None = None) -> str:
    """Etapa do vídeo: jobs ativos têm prioridade; depois a decisão registrada no painel; depois os arquivos."""
    states = [job for job in jobs_for_video if job["state"] in ("running", "queued")]
    if any(job["kind"] == "keyframes" for job in states):
        return "Em produção (imagens-chave)"
    if any(job["kind"] == "animation" and job["state"] == "running" for job in states):
        return "Animando"
    if any(job["kind"] == "animation" for job in states):
        return "Na fila da animação"
    publication = read_publication(folder)
    if publication and publication.get("status") in PUBLICATION_STAGES:
        return PUBLICATION_STAGES[publication["status"]]
    if folder.name.upper().startswith("OK"):
        return "Aprovado"
    if review in ("Aprovado", "Rejeitado") or (review == "Ajustes" and not _stale_adjustments(folder, jobs_for_video)):
        return review
    # Cena com imagem-chave refeita e ainda sem animação: o short.mp4 é o antigo, falta aprovar as imagens novas
    # (antes o card mostrava "Revisão do vídeo" e escondia a aprovação; 2026-10-04).
    pending_scenes = any(not (scene / "segment.mp4").is_file() for scene in folder.glob("scene-*"))
    if (folder / "short.mp4").is_file() and not pending_scenes:
        return "Revisão do vídeo"
    if (folder / "keyframes-sheet.png").is_file():
        return "Revisão das imagens-chave"
    if jobs_for_video and jobs_for_video[-1]["state"] == "failed":
        return "Falhou"
    return "Em produção"


JOB_STAGES = ("Em produção (imagens-chave)", "Animando", "Na fila da animação")


def videos(jobs: list[dict[str, Any]], finish_by_slug: dict[str, str]) -> list[dict[str, Any]]:
    from .formats import of_folder
    from .output_ok import ok_names, published_in_output

    names = ok_names()
    rows = []
    for script_path in GENERATED_ROOT.glob("*/*/*/motion-script.json"):
        folder = script_path.parent
        batch = folder.parent.name
        slug = re.sub(r"^OK[\s_-]*", "", folder.name)
        script = _read_json(script_path) or {}
        metadata = _read_json(folder / "youtube-metadata.json") or {}
        related = [job for job in jobs if job["params"].get("batch_id") == batch and _job_slug(job) == slug]
        finished = [job for job in related if job["kind"] == "animation" and job["state"] == "done"
                    and isinstance(job.get("result"), dict) and job["result"].get("durationSeconds")]
        # Português (BR) primeiro: pronto até o .mp4 ganhar "OK - " em output/ (pedido do usuário, 2026-10-03).
        animating = any(job["kind"] == "animation" and job["state"] in ("queued", "running") for job in related)
        languages = {"br": (
            "publicado" if published_in_output(folder, None, names)
            else "montando" if animating
            else "pronto" if (folder / "short.mp4").is_file() else "—"
        )}
        for language in enabled_languages():
            languages[language] = (
                "publicado" if published_in_output(folder, language, names)
                else "montando" if language_build_running(folder, language)
                else "pronto" if (folder / language / "short.mp4").is_file()
                else "tradução salva" if (folder / f"translation-{language}.json").is_file() else "—"
            )
        title = metadata.get("title")
        review = read_state(folder)
        if review.get("stage") == "Rejeitado":
            # Sai da grade na hora (2026-10-09). Os arquivos são apagados pelo vigia, salvo se alguma versão já
            # estiver publicada: aí ficam no disco, só fora da tela.
            continue
        stage = video_stage(folder, related, review.get("stage"))
        # "OK - " no .mp4 em português dentro de output/ = o usuário publicou à mão.
        if stage not in JOB_STAGES and not (read_publication(folder) or {}).get("videoId") \
                and published_in_output(folder, None, names):
            stage = "Publicado"
        rows.append({
            "batch": batch, "slug": slug, "folder": str(folder.relative_to(GENERATED_ROOT)),
            "title": title or script.get("title") or script.get("topic") or slug,
            "scenes": len(script.get("scenes", [])), "stage": stage, "languages": languages,
            "seo": (folder / "youtube-metadata.json").is_file(), "seoStatus": seo_status(folder),
            "duration": _duration(folder, finished),
            "keyframeSheet": _media_url(folder / "keyframes-sheet.png"),
            "reviewSheet": _media_url(folder / "motion-review.png"),
            "expectedFinish": finish_by_slug.get(f"{batch}/{slug}"),
            "editable": not folder.name.upper().startswith("OK"),
            "note": "remontando o vídeo com os trechos prontos (sem GPU)" if remount_running(folder) else review.get("note"),
            "format": of_folder(folder), "thumbnail": _media_url(folder / "thumbnail.jpg"),
            "publication": read_publication(folder),
            "updatedAt": datetime.fromtimestamp(script_path.stat().st_mtime, UTC).isoformat(),
        })
    rows.extend(_pending_videos(jobs, {(row["batch"], row["slug"]) for row in rows}, finish_by_slug))
    return sorted(rows, key=lambda row: (row["batch"], row["slug"]), reverse=True)


def _duration(folder: Path, finished: list[dict[str, Any]]) -> float | None:
    """Duração do vídeo pronto: o relatório da última montagem (vale também para a remontagem sem GPU)."""
    try:
        report = json.loads((folder / "motion-report.json").read_text(encoding="utf-8"))
        return round(sum(item["seconds"] for item in report), 1)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return finished[-1]["result"]["durationSeconds"] if finished else None


def _pending_videos(jobs: list[dict[str, Any]], listed: set[tuple[str, str]],
                    finish_by_slug: dict[str, str]) -> list[dict[str, Any]]:
    """Vídeos cujo job de imagens-chave ainda não começou: o roteiro só vira arquivo quando o job roda na GPU,
    e o vídeo sumia do painel até lá (lote "terminou" sem vídeo visível; corrigido em 2026-10-03)."""
    rows = []
    for job in jobs:
        script = job["params"].get("script") or {}
        batch, slug = job["params"].get("batch_id"), script.get("slug")
        if job["kind"] != "keyframes" or job["state"] not in ("queued", "running") or not batch or not slug:
            continue
        if (batch, slug) in listed:
            continue
        listed.add((batch, slug))
        from .media import _slug

        folder = GENERATED_ROOT / _slug(job["params"].get("influencer_name") or "Historiador") / batch / slug
        rows.append({
            "batch": batch, "slug": slug, "folder": f"{batch}/{slug}",
            "title": script.get("title") or script.get("topic") or slug,
            "scenes": len(script.get("scenes", [])), "stage": "Na fila das imagens-chave",
            "languages": {"br": "—", **{language: "—" for language in enabled_languages()}},
            "seo": (folder / "youtube-metadata.json").is_file(), "duration": None,
            "keyframeSheet": None, "reviewSheet": None, "expectedFinish": finish_by_slug.get(f"{batch}/{slug}"),
            "editable": False, "note": None, "format": script.get("format") or "short", "thumbnail": None,
            "publication": None, "pending": True, "updatedAt": job.get("createdAt"),
        })
    return rows


def _media_url(path: Path) -> str | None:
    if not path.is_file():
        return None
    # O carimbo de modificação força o navegador a buscar de novo uma imagem refeita.
    return ("/media/" + urllib.parse.quote(path.relative_to(GENERATED_ROOT).as_posix())
            + f"?v={int(path.stat().st_mtime)}")


def publish_slots(text: str | None = None) -> dict[str, Any]:
    """Lê do fluxo_aprovacao.md as linhas '- chave: valor' da seção 'Grade de publicação'."""
    if text is None:
        text = FLOW_PATH.read_text(encoding="utf-8") if FLOW_PATH.is_file() else ""
    section = re.search(r"^##\s*Grade de publica[çc][ãa]o\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    values = dict(re.findall(r"^-\s*([\w-]+):\s*(.+?)\s*$", section.group(1), re.M)) if section else {}
    times = [item.strip() for item in values.get("horarios", "").split(",") if item.strip()]
    return {"timezone": values.get("fuso"), "times": times, "languages": values.get("idiomas"),
            "audit": values.get("auditoria"), "approvedSince": values.get("aprovados-desde"),
            "playlist": values.get("playlist"), "playlistActive": values.get("playlist-ativa"),
            "delayMinutes": values.get("agendar-apos-min"), "commentSince": values.get("comentario-automatico-desde")}


_REMOTE_CACHE: dict[str, Any] = {"at": 0.0, "value": {"configured": False}}


def _remote_gpu() -> dict[str, Any]:
    """Situação da GPU remota (consulta a API do RunPod no máximo a cada 60 s)."""
    from . import gpu_remota

    if not gpu_remota.configured():
        return {"configured": False}
    if time.monotonic() - _REMOTE_CACHE["at"] > 60:
        try:
            _REMOTE_CACHE["value"] = gpu_remota.status()
        except Exception as error:  # noqa: BLE001 - sem internet/API: mostra o erro, o painel segue
            _REMOTE_CACHE["value"] = {"configured": True, "error": str(error)[:200]}
        _REMOTE_CACHE["at"] = time.monotonic()
    return _REMOTE_CACHE["value"]


def _characters() -> list[dict[str, str]]:
    from .perfis import selectable

    try:
        return selectable()
    except ValueError:
        return []


def _identities() -> list[dict[str, str]]:
    from .perfis import identities

    try:
        return identities()
    except ValueError:
        return []


def comfyui_up() -> bool:
    from .comfyui import is_up
    from .media import _comfyui_url

    try:
        return is_up(_comfyui_url(require_avatar_checkpoint=False))
    except RuntimeError:
        return False


def state(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    jobs = load_jobs()
    rows = queue(jobs, now)
    finish_by_slug = {}
    for row in rows:
        finish_by_slug[f"{row['batch']}/{row['slug']}"] = row["expectedFinish"]
    # Só a falha que ainda vale: se o mesmo vídeo já tem um job mais novo da mesma etapa, ela foi superada.
    latest = {(job.get("params", {}).get("batch_id"), _job_slug(job), job["kind"]): job["id"] for job in jobs}
    recent_failures = [
        {"id": job["id"], "kind": job["kind"], "slug": _job_slug(job), "finishedAt": job.get("finishedAt"),
         "error": (job.get("error") or "").splitlines()[0][:300] if job.get("error") else None}
        for job in jobs if job["state"] == "failed"
        and latest[(job.get("params", {}).get("batch_id"), _job_slug(job), job["kind"])] == job["id"]
    ][-10:]
    return {
        "now": now.isoformat(), "comfyui": comfyui_up(), "clipSeconds": round(clip_seconds(jobs)),
        "queue": rows, "queueFinish": rows[-1]["expectedFinish"] if rows else None,
        "characters": _characters(),
        "identities": _identities(),
        "remoteGpu": _remote_gpu(),
        "activeLanguages": ["pt", *enabled_languages()],
        "environment": ENVIRONMENT,
        "videos": videos(jobs, finish_by_slug), "failures": recent_failures, "publishing": publishing(now),
        "lotes": list_lotes(),
    }


PUBLISHER: Publisher | None = None


def publishing(now: datetime) -> dict[str, Any]:
    flow = publish_slots()
    info: dict[str, Any] = {**flow, "clientSecret": youtube_publish.CLIENT_SECRET.is_file(),
                            "connected": youtube_publish.TOKEN_PATH.is_file(), "channel": None, "queue": [], "error": None,
                            "cutoff": None, "beforeCutoff": []}
    try:
        info["channel"] = youtube_publish.channel_info() if info["connected"] else None
        cfg = youtube_publish.settings(flow)
        info["auditApproved"] = cfg["audit"]
        info["cutoff"] = cfg["cutoff"].isoformat() if cfg["cutoff"] else None
        info["beforeCutoff"] = [
            {"batch": folder.parent.name, "slug": folder.name,
             "title": (_read_json(folder / "youtube-metadata.json") or {}).get("title")}
            for folder in youtube_publish.approved_before_cutoff(cfg["cutoff"])
        ]
        info["queue"] = [
            {"folder": str(folder.relative_to(GENERATED_ROOT)), "batch": folder.parent.name, "slug": folder.name,
             "title": (_read_json(folder / "youtube-metadata.json") or {}).get("title"), "slot": slot.isoformat()}
            for folder, slot in youtube_publish.projected_slots(now, cfg).items()
        ]
    except Exception as error:  # noqa: BLE001 - o painel mostra o problema em vez de cair
        info["error"] = str(error)
    if PUBLISHER and PUBLISHER.last_error:
        info["error"] = PUBLISHER.last_error
    from .painel_acoes import CONNECT_ERROR

    if CONNECT_ERROR:
        info["error"] = f"Login do canal falhou: {CONNECT_ERROR[0]}"
    info["scheduled"] = sorted(
        ({"slug": folder.name, "batch": folder.parent.name, **record}
         for folder in youtube_publish.video_folders()
         if (record := read_publication(folder)) and record.get("status") in ("enviando", "agendado", "erro")),
        key=lambda row: row.get("slot") or "",
    )
    return info


def video_detail(batch: str, slug: str) -> dict[str, Any]:
    """Tudo o que a tela de revisão de um vídeo precisa: cenas, mídias, metadados e jobs ativos."""
    folder = video_folder(batch, slug)
    script = _read_json(folder / "motion-script.json") or {}
    jobs = [job for job in load_jobs() if job["params"].get("batch_id") == batch and _job_slug(job) == slug]
    translations = {language: _read_json(folder / f"translation-{language}.json") for language in enabled_languages()}
    scenes = []
    for number, scene in enumerate(script.get("scenes", []), 1):
        scene_dir = folder / f"scene-{number:02d}"
        scenes.append({
            "number": number, "narration": scene.get("narration"), "caption": scene.get("caption"),
            "keyframe": _media_url(scene_dir / "keyframe.png"), "animated": (scene_dir / "segment.mp4").is_file(),
            "translations": {
                language: next((item["narration"] for item in (data or {}).get("scenes", []) if item.get("scene") == number), None)
                for language, data in translations.items()
            },
        })
    videos_by_language = {"pt": _media_url(folder / "short.mp4")}
    videos_by_language.update({language: _media_url(folder / language / "short.mp4") for language in translations})
    active = [job for job in jobs if job["state"] in ("running", "queued")]
    review = read_state(folder)
    from .formats import of_folder
    from .output_ok import published_in_output

    stage = video_stage(folder, jobs, review.get("stage"))
    if stage not in JOB_STAGES and not (read_publication(folder) or {}).get("videoId") and published_in_output(folder):
        stage = "Publicado"
    return {
        "batch": batch, "slug": slug, "title": (_read_json(folder / "youtube-metadata.json") or {}).get("title"),
        "stage": stage, "review": review, "format": of_folder(folder),
        "thumbnail": _media_url(folder / "thumbnail.jpg"),
        "busy": bool(active), "scenes": scenes, "videos": videos_by_language,
        "keyframeSheet": _media_url(folder / "keyframes-sheet.png"), "reviewSheet": _media_url(folder / "motion-review.png"),
        "metadata": _read_json(folder / "youtube-metadata.json"), "publication": read_publication(folder),
        "languages": {language: {"translation": bool(data), "building": language_build_running(folder, language)}
                      for language, data in translations.items()},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - assinatura da biblioteca
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - nome exigido pela biblioteca
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/estado":
            body = json.dumps(state(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/api/sistema":
            self._json(200, system_usage())
        elif path == "/api/video":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                detail = video_detail(query.get("batch", [""])[0], query.get("slug", [""])[0])
            except (ValueError, FileNotFoundError) as error:
                self._json(404, {"error": str(error)})
                return
            self._json(200, detail)
        elif path.startswith("/media/"):
            target = (GENERATED_ROOT / urllib.parse.unquote(path[len("/media/"):])).resolve()
            # Só arquivos dentro de generated/ (nada de ../).
            if GENERATED_ROOT.resolve() not in target.parents or not target.is_file():
                self._send(404, b"not found", "text/plain")
                return
            self._send_file(target)
        else:
            self._send(404, b"not found", "text/plain")

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_file(self, target: Path) -> None:
        """Arquivo com suporte a Range, para o player de vídeo poder avançar e voltar."""
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        size = target.stat().st_size
        match = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        start, end = 0, size - 1
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
            else:
                start = max(size - int(match.group(2)), 0)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = handle.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # o navegador cancela pedidos de vídeo ao avançar

    def do_POST(self) -> None:  # noqa: N802 - nome exigido pela biblioteca
        # Só a própria página pode chamar as ações (bloqueia pedidos vindos de outros sites).
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host", ""):
            self._json(403, {"error": "Origem não permitida."})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "Pedido inválido."})
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/acao":
                result = run_action(body)
            elif path == "/api/verificar":
                result = check_environment()
            elif path == "/api/lote":
                result = iniciar_lote(body.get("quantity", 1), body.get("theme", ""), body.get("format", "short"),
                                      body.get("profile"), body.get("gpu", "local"), body.get("identity"))
            else:
                self._json(404, {"error": "Rota desconhecida."})
                return
        except (ValueError, FileNotFoundError, RuntimeError, KeyError) as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, result)


def sync_manual_published() -> list[str]:
    """Vídeo marcado "OK - " em output/ vira "publicado" no publicacao.json: o envio automático nunca o sobe."""
    from .output_ok import ok_names, published_in_output
    from .youtube_publish import read_publication, video_folders, write_publication

    names, marked = ok_names(), []
    for folder in video_folders():
        record = read_publication(folder) or {}
        # Enviado pelo painel (tem videoId): o status vem do YouTube; o "OK - " ali só indica "já processado".
        if record.get("videoId") or record.get("status") in ("publicado", "enviando") \
                or not published_in_output(folder, None, names):
            continue
        write_publication(folder, status="publicado", source="output-ok",
                          markedAt=datetime.now(UTC).isoformat(timespec="seconds"))
        marked.append(folder.name)
    return marked


def _guard_jobs(interval: int = 60) -> None:
    """Reinicia jobs da fila que perderam o processo (ex.: o Claude do lote terminou e o Windows os encerrou)."""
    from .motion_jobs import revive_dead_jobs

    while True:
        try:
            from .output_ok import sync_ok_covers

            sync_ok_covers()  # capa acompanha o "OK - " do vídeo
            sync_manual_published()
            from .painel_acoes import gerar_seo_pendente, limpar_rejeitados

            seo = gerar_seo_pendente()
            if seo:
                print("SEO automático iniciado:", ", ".join(seo), flush=True)
            from .painel_acoes import retomar_lotes_interrompidos

            retomados = retomar_lotes_interrompidos()
            if retomados:
                print("lotes retomados após limite de uso:", ", ".join(retomados), flush=True)
            deleted = limpar_rejeitados()
            from . import gpu_remota
            from .motion_jobs import list_jobs

            remote_active = any(job["state"] in ("queued", "running") and job_target(job) == "remota" for job in list_jobs())
            if gpu_remota.stop_if_idle(remote_active):
                print("GPU remota desligada por ociosidade", flush=True)
            if deleted:
                print("rejeitados excluídos:", ", ".join(deleted), flush=True)
            revived = revive_dead_jobs()
            if revived:
                print("jobs disparados de novo:", ", ".join(revived), flush=True)
        except Exception:  # noqa: BLE001 - tenta de novo no próximo ciclo
            traceback.print_exc()
        time.sleep(interval)


ENVIRONMENT: dict[str, Any] = {}


def check_environment() -> dict[str, Any]:
    """Verificação do ambiente na abertura do painel (GPU local e requisitos da GPU remota)."""
    from .ambiente import check_all

    ENVIRONMENT.clear()
    ENVIRONMENT.update(check_all())
    local = ENVIRONMENT["local"]
    print("GPU local:", ", ".join(local["gpus"]) if local["available"] else "indisponível — " + " ".join(local["problems"]),
          flush=True)
    pending = [item["label"] for item in ENVIRONMENT["remote"]["items"] if not item["ok"]]
    print("GPU remota:", "pronta" if ENVIRONMENT["remote"]["ready"] else "pendente: " + "; ".join(pending), flush=True)
    return ENVIRONMENT


def serve(port: int = 8766) -> None:
    global PUBLISHER
    check_environment()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    PUBLISHER = Publisher(publish_slots)
    PUBLISHER.start()
    threading.Thread(target=_guard_jobs, daemon=True, name="guarda-jobs").start()
    print(f"Painel em http://127.0.0.1:{port} (Ctrl+C para parar)", flush=True)
    server.serve_forever()
