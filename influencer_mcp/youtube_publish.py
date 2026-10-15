"""Publicação agendada no YouTube (fase 4 do painel), pela YouTube Data API v3.

Decisões do usuário (2026-10-01):
- envio no momento da aprovação, como privado com publishAt no próximo horário livre da grade do
  fluxo_aprovacao.md (o próprio YouTube publica na hora); só PT;
- quem envia é o processo do painel; nada é enviado enquanto "- auditoria:" não for "aprovada";
- categoria Guias e estilo (26), pt-BR, não é para crianças, mídia sintética declarada, notificar inscritos;
- descrição igual à do youtube-metadata.txt; sem #Shorts;
- comentário sugerido publicado só quando o usuário clicar; cancelar = deixar privado (sem apagar);
- desde 2026-10-04: no modo "imediato", o Short sobe na hora da aprovação, privado e agendado para
  `agendar-apos-min` (20) minutos depois da aprovação; se o envio atrasar e esse horário já tiver passado,
  agenda para 20 minutos depois do envio (decisão do usuário). Com 0, vai ao ar público na hora;
- desde 2026-10-03 ("elimine os horários, publique assim que eu aprovar"): com "- horarios: imediato" o vídeo é
  enviado já público assim que aprovado, sem grade nem limite por horário (a cota da API ainda limita ~6 por dia;
  estourou, o envio fica em erro e é tentado de novo a cada 30 min até a cota renovar);
- permissão youtube.force-ssl; credencial e token em secrets/ (fora do git); fuso fixo (ex.: -03:00).
O Claude e os agentes nunca chamam este módulo.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .media import DESCRIPTION_INTRO, GENERATED_ROOT, PROJECT_ROOT, clean_youtube_text

SECRETS_DIR = PROJECT_ROOT / "secrets"
CLIENT_SECRET = SECRETS_DIR / "client_secret.json"
TOKEN_PATH = SECRETS_DIR / "token.json"
CHANNEL_PATH = SECRETS_DIR / "channel.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CATEGORY_ID = "26"  # Guias e estilo (Howto & Style)
LANGUAGE = "pt-BR"
PUBLICATION_FILE = "publicacao.json"
# Folga mínima até o horário da grade (2026-10-10: era 1 h; o usuário quer o primeiro horário disponível). 15 min
# cobrem o envio (~30 s) e o processamento do YouTube antes do publishAt.
MIN_LEAD = timedelta(minutes=15)
RETRY_AFTER = timedelta(minutes=30)
ACTIVE = ("enviando", "agendado")


# ---------- grade e fila ----------

def parse_offset(value: str | None) -> timezone:
    """'-03:00', 'UTC-03:00' ou 'UTC−3' viram um fuso fixo (decisão: sem base de fusos no Windows)."""
    match = re.fullmatch(r"\s*(?:UTC|GMT)?\s*([+\-−])\s*(\d{1,2})(?::?(\d{2}))?\s*", value or "", re.I)
    if not match:
        raise ValueError(f"Fuso inválido no fluxo_aprovacao.md: {value!r} (use, por exemplo, -03:00).")
    sign = -1 if match.group(1) in "-−" else 1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0)))


def settings(flow: dict[str, Any]) -> dict[str, Any]:
    """Converte a seção 'Grade de publicação' (já lida pelo painel) em horários e fuso utilizáveis."""
    times = []
    immediate = False
    for item in flow.get("times", []):
        if item.strip().lower() in ("imediato", "imediata", "ao aprovar"):
            immediate = True
            continue
        hours, minutes = (int(part) for part in item.split(":"))
        times.append((hours, minutes))
    tz = parse_offset(flow.get("timezone"))
    return {
        "times": sorted(times), "immediate": immediate, "tz": tz,
        "delay_minutes": int(flow.get("delayMinutes") or 0),
        "audit": (flow.get("audit") or "").strip().lower() in ("aprovada", "aprovado", "sim"),
        "cutoff": parse_cutoff(flow.get("approvedSince"), tz),
        "playlist": (flow.get("playlist") or "").strip() or None,
        "playlistActive": (flow.get("playlistActive") or "").strip().lower() in ("sim", "ativa", "ativo", "true"),
        # Comentário sugerido postado sozinho quando o vídeo vai ao ar (decisão de 2026-10-04), só nos enviados a
        # partir desse instante; sem a linha, só pelo botão. Fixar continua manual (a API não permite).
        "commentSince": parse_cutoff(flow.get("commentSince"), tz),
    }


def parse_cutoff(value: str | None, tz: timezone) -> datetime | None:
    """'- aprovados-desde:' do fluxo: só aprovações a partir desse instante são enviadas (decisão de 2026-10-02).

    Sem a linha (ou com valor inválido) devolve None, e então nada é enviado.
    """
    if not value or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=tz)


def _approved_at(state: dict[str, Any]) -> datetime | None:
    """Instante da última aprovação feita pelo botão do painel (histórico do painel.json)."""
    for item in reversed(state.get("history", [])):
        if item.get("action") == "Aprovado" and item.get("at"):
            try:
                return datetime.fromisoformat(item["at"])
            except ValueError:
                return None
    return None


# YouTube exige publishAt no futuro; com menos que isso de folga, conta como "horário já passou".
IMMEDIATE_MIN_LEAD = timedelta(minutes=5)


def immediate_slot(folder: Path, now: datetime, cfg: dict[str, Any]) -> datetime | None:
    """Horário de publicação no modo imediato: aprovação + atraso; se já passou, envio + atraso. None = público já."""
    delay = timedelta(minutes=cfg.get("delay_minutes", 0))
    if not delay:
        return None
    from .painel_acoes import read_state

    approved = _approved_at(read_state(folder)) or now
    slot = approved + delay
    return slot if slot >= now + IMMEDIATE_MIN_LEAD else now + delay


def slot_candidates(now: datetime, cfg: dict[str, Any], days: int = 60):
    local_now = now.astimezone(cfg["tz"])
    for offset in range(days):
        day = (local_now + timedelta(days=offset)).date()
        for hours, minutes in cfg["times"]:
            slot = datetime(day.year, day.month, day.day, hours, minutes, tzinfo=cfg["tz"])
            if slot >= now + MIN_LEAD:
                yield slot


def video_folders() -> list[Path]:
    return [path.parent for path in GENERATED_ROOT.glob("*/*/*/motion-script.json")]


def read_publication(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / PUBLICATION_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_publication(folder: Path, **fields: Any) -> dict[str, Any]:
    record = {**(read_publication(folder) or {}), **fields, "updatedAt": datetime.now(UTC).isoformat(timespec="seconds")}
    (folder / PUBLICATION_FILE).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


ARCHIVED_LEDGER = GENERATED_ROOT / "publicacoes-arquivadas.jsonl"


def archive_record(folder: Path) -> None:
    """Guarda o registro de envio antes de a pasta do vídeo ser limpa (cota de 24 h e horários da grade)."""
    record = read_publication(folder)
    if not record:
        return
    entry = {"folder": str(folder.relative_to(GENERATED_ROOT)), **record}
    with ARCHIVED_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def archived_records() -> list[dict[str, Any]]:
    if not ARCHIVED_LEDGER.is_file():
        return []
    rows = []
    for line in ARCHIVED_LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def taken_slots() -> set[datetime]:
    taken = set()
    for record in archived_records():
        if record.get("status") in ACTIVE and record.get("slot"):
            taken.add(datetime.fromisoformat(record["slot"]))
    for folder in video_folders():
        record = read_publication(folder)
        if record and record.get("status") in ACTIVE and record.get("slot"):
            taken.add(datetime.fromisoformat(record["slot"]))
    return taken


def _approved_candidates() -> list[tuple[datetime | None, Path]]:
    """Aprovados no painel, com vídeo pronto e ainda não enviados (ou com erro de envio)."""
    from .painel_acoes import read_state

    rows = []
    for folder in video_folders():
        state = read_state(folder)
        record = read_publication(folder)
        if state.get("stage") != "Aprovado" or not (folder / "short.mp4").is_file():
            continue
        from .output_ok import published_in_output

        if published_in_output(folder):  # o usuário já publicou à mão ("OK - " em output/)
            continue
        from .formats import of_folder

        if of_folder(folder) != "short":  # vídeo horizontal: publicação manual no Studio
            continue
        if record and record.get("status") not in ("erro",):
            continue
        rows.append((_approved_at(state), folder))
    return rows


def approved_queue(cutoff: datetime | None) -> list[Path]:
    """Fila de envio: só aprovações feitas a partir do corte, na ordem de aprovação. Sem corte, fila vazia."""
    if cutoff is None:
        return []
    rows = [(at, folder) for at, folder in _approved_candidates() if at is not None and at >= cutoff]
    return [folder for _, folder in sorted(rows)]


def approved_before_cutoff(cutoff: datetime | None) -> list[Path]:
    """Aprovados que NÃO serão enviados por terem sido aprovados antes do corte (precisam ser aprovados de novo)."""
    return [folder for at, folder in _approved_candidates() if cutoff is None or at is None or at < cutoff]


def projected_slots(now: datetime, cfg: dict[str, Any]) -> dict[Path, datetime]:
    """Horário que cada aprovado pegaria, na ordem de aprovação, pulando os horários já ocupados."""
    if cfg.get("immediate"):
        return {folder: immediate_slot(folder, now, cfg) or now for folder in approved_queue(cfg["cutoff"])}
    taken = taken_slots()
    candidates = slot_candidates(now, cfg)
    result = {}
    for folder in approved_queue(cfg["cutoff"]):
        slot = next(slot for slot in candidates if slot not in taken)
        result[folder] = slot
    return result


# ---------- conta ----------

def load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_PATH.is_file():
        return None
    credentials = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    return credentials if credentials.valid else None


def youtube():
    from googleapiclient.discovery import build

    credentials = load_credentials()
    if credentials is None:
        raise RuntimeError("Canal não conectado: use \"Conectar canal\" no painel.")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


_connect_lock = threading.Lock()


def connect() -> dict[str, Any]:
    """Login OAuth no navegador (uma vez). Grava o token em secrets/ e guarda o nome do canal."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET.is_file():
        raise RuntimeError(f"Falta a credencial OAuth: salve o JSON baixado do Google Cloud em {CLIENT_SECRET}.")
    if not _connect_lock.acquire(blocking=False):
        raise RuntimeError("O login já está aberto no navegador.")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, prompt="consent",
            authorization_prompt_message="", success_message="Canal conectado ao painel. Pode fechar esta aba.",
        )
        SECRETS_DIR.mkdir(exist_ok=True)
        TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
        return channel_info(refresh=True)
    finally:
        _connect_lock.release()


def channel_info(*, refresh: bool = False) -> dict[str, Any] | None:
    if CHANNEL_PATH.is_file() and not refresh:
        return json.loads(CHANNEL_PATH.read_text(encoding="utf-8"))
    if not TOKEN_PATH.is_file():
        return None
    response = youtube().channels().list(part="snippet", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("A conta autorizada não tem canal do YouTube.")
    info = {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}
    CHANNEL_PATH.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return info


# ---------- envio, verificação, cancelamento e comentário ----------

def upload_body(folder: Path, slot: datetime | None) -> dict[str, Any]:
    """slot None = publicação imediata (público na hora); senão privado com publishAt no horário."""
    metadata = json.loads((folder / "youtube-metadata.json").read_text(encoding="utf-8"))
    # Igual ao youtube-metadata.txt: descrição + hashtags + assinatura do Instagram no fim (2026-10-14).
    description = f"{metadata.get('descriptionForUpload') or metadata['description']}\n\n{DESCRIPTION_INTRO}"
    return {
        "snippet": {
            "title": metadata["title"], "description": description, "tags": metadata.get("tags", []),
            "categoryId": CATEGORY_ID, "defaultLanguage": LANGUAGE, "defaultAudioLanguage": LANGUAGE,
        },
        "status": {
            **({"privacyStatus": "public"} if slot is None else
               {"privacyStatus": "private", "publishAt": slot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}),
            "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
        },
    }


def upload(folder: Path, slot: datetime | None) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    body = upload_body(folder, slot)
    write_publication(folder, status="enviando", slot=(slot or datetime.now(UTC)).isoformat(), progress=0, error=None,
                      attemptAt=datetime.now(UTC).isoformat(timespec="seconds"))
    media = MediaFileUpload(str(folder / "short.mp4"), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube().videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            write_publication(folder, progress=round(status.progress() * 100))
    video_id = response["id"]
    record = write_publication(
        folder, status="agendado" if slot else "publicado", videoId=video_id, url=f"https://youtu.be/{video_id}", progress=100,
        uploadedAt=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    return mark_output_processed(folder) or record


def mark_output_processed(folder: Path) -> dict[str, Any] | None:
    """Agendado pelo painel: o .mp4 e o .txt em output/ recebem "OK - " (decisão do usuário, 2026-10-02).

    Se o arquivo estiver em uso, registra e o publicador tenta de novo no próximo ciclo.
    """
    from .output_ok import mark_processed

    try:
        renamed = mark_processed(folder)
    except OSError as error:
        return write_publication(folder, outputOkError=str(error)[:300])
    return write_publication(folder, outputOk=True, outputOkError=None, outputOkFiles=renamed)


def refresh_published(now: datetime) -> None:
    """Agendados cujo horário passou: confere no YouTube se já estão públicos (1 unidade de cota por chamada)."""
    due = {}
    for folder in video_folders():
        record = read_publication(folder)
        if record and record.get("status") == "agendado" and datetime.fromisoformat(record["slot"]) <= now:
            due[record["videoId"]] = folder
    if not due:
        return
    response = youtube().videos().list(part="status", id=",".join(list(due)[:50])).execute()
    for item in response.get("items", []):
        folder = due[item["id"]]
        privacy = item["status"].get("privacyStatus")
        publish_at = item["status"].get("publishAt")
        if privacy == "public":
            write_publication(folder, status="publicado", publishedAt=now.isoformat(timespec="seconds"), warning=None)
        elif publish_at and datetime.fromisoformat(publish_at.replace("Z", "+00:00")) > now:
            # Horário mudado no YouTube Studio: segue o novo, sem alarme falso (2026-10-06).
            write_publication(folder, slot=datetime.fromisoformat(publish_at.replace("Z", "+00:00")).isoformat(), warning=None)
        else:
            write_publication(folder, warning=f"Horário passou e o vídeo está {privacy} (o projeto já passou na auditoria?).")


def private_status(video_id: str) -> dict[str, Any]:
    """Status atual do vídeo com privacidade "private" e sem data de publicação.

    O videos.update substitui o bloco status inteiro: campos omitidos (incorporação, estatísticas públicas,
    licença) voltariam ao padrão desligado. Por isso parte do status que o YouTube devolve hoje.
    """
    items = youtube().videos().list(part="status", id=video_id).execute().get("items", [])
    if not items:
        raise RuntimeError(f"O vídeo {video_id} não foi encontrado no canal.")
    status = {key: value for key, value in items[0]["status"].items()
              if key in ("license", "embeddable", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia")}
    status.setdefault("selfDeclaredMadeForKids", False)
    status.setdefault("containsSyntheticMedia", True)
    return {**status, "privacyStatus": "private"}


def cancel(folder: Path) -> dict[str, Any]:
    record = read_publication(folder)
    if not record or record.get("status") != "agendado":
        raise RuntimeError("Só dá para cancelar um vídeo agendado que ainda não foi ao ar.")
    youtube().videos().update(part="status", body={"id": record["videoId"], "status": private_status(record["videoId"])}).execute()
    return write_publication(folder, status="cancelado", cancelledAt=datetime.now(UTC).isoformat(timespec="seconds"))


# Lembrete de inscrição, abaixo da pergunta do SEO no mesmo comentário (pedido do usuário, 2026-10-06): um só
# comentário por vídeo, o que é fixado no Studio.
SUBSCRIBE_REMINDER = ("Não se esqueça de seguir nosso canal e ativar o sininho, para que possa receber as notificações "
                      "dos novos vídeos!")


def comment_text(suggestion: str | None) -> str:
    """Pergunta sugerida pelo SEO e, abaixo, o lembrete do sininho (sem pergunta, só o lembrete)."""
    suggestion = (suggestion or "").strip()
    return f"{suggestion}\n\n{SUBSCRIBE_REMINDER}" if suggestion else SUBSCRIBE_REMINDER


def post_comment(folder: Path) -> dict[str, Any]:
    record = read_publication(folder)
    if not record or record.get("status") != "publicado" or not record.get("videoId"):
        raise RuntimeError("O comentário só pode ser publicado depois que o vídeo estiver no ar.")
    if record.get("commentId"):
        raise RuntimeError("O comentário já foi publicado.")
    metadata = json.loads((folder / "youtube-metadata.json").read_text(encoding="utf-8"))
    text = comment_text(metadata.get("pinnedCommentSuggestion"))
    response = youtube().commentThreads().insert(part="snippet", body={"snippet": {
        "videoId": record["videoId"], "topLevelComment": {"snippet": {"textOriginal": text}},
    }}).execute()
    return write_publication(folder, commentId=response["id"])


PLAYLISTS_PATH = SECRETS_DIR / "playlists.json"


def playlist_id(name: str) -> str:
    """ID da playlist do canal com esse nome exato (consulta guardada em secrets/playlists.json)."""
    cache = json.loads(PLAYLISTS_PATH.read_text(encoding="utf-8")) if PLAYLISTS_PATH.is_file() else {}
    if name in cache:
        return cache[name]
    request = youtube().playlists().list(part="snippet", mine=True, maxResults=50)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            cache[item["snippet"]["title"]] = item["id"]
        request = youtube().playlists().list_next(request, response)
    PLAYLISTS_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if name not in cache:
        raise RuntimeError(f"Playlist {name!r} não encontrada no canal.")
    return cache[name]


def pending_playlist(folders: list[Path] | None = None) -> list[Path]:
    """Vídeos enviados pelo painel (com videoId) que ainda não entraram na playlist; inclui os cancelados (privados)."""
    rows = []
    for folder in folders if folders is not None else video_folders():
        record = read_publication(folder)
        if record and record.get("videoId") and not record.get("playlistItemId") \
                and record.get("status") in ("agendado", "publicado", "cancelado"):
            rows.append(folder)
    return rows


def add_to_playlist(folder: Path, name: str) -> dict[str, Any]:
    record = read_publication(folder) or {}
    response = youtube().playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": playlist_id(name), "resourceId": {"kind": "youtube#video", "videoId": record["videoId"]},
    }}).execute()
    return write_publication(folder, playlist=name, playlistItemId=response["id"], playlistError=None)


COMMENT_ATTEMPTS = 5


def comment_pending(folder: Path) -> bool:
    """Enviado pelo painel e o comentário (pergunta do SEO + lembrete do sininho) ainda não saiu nem esgotou as
    tentativas. Todo vídeo recebe comentário: sem pergunta do SEO, vai só o lembrete."""
    record = read_publication(folder) or {}
    if not record.get("videoId") or record.get("commentId") or record.get("status") not in ("agendado", "publicado"):
        return False
    return record.get("commentAttempts", 0) < COMMENT_ATTEMPTS
COMMENT_RETRY = timedelta(minutes=10)

# Vídeos antigos sem comentário (pedido de 2026-10-06): lista em generated/comentarios-pendentes.json
# ({"notBefore": ..., "items": [{"videoId", "text"}]}), postada aos poucos para não gastar a cota dos envios:
# um por minuto, no máximo BACKLOG_PER_DAY em 24 h (50 unidades cada).
BACKLOG_PATH = GENERATED_ROOT / "comentarios-pendentes.json"
BACKLOG_PER_DAY = 60


def sync_comment_backlog(now: datetime) -> str | None:
    """Posta o próximo comentário da lista de vídeos antigos; devolve o videoId postado (ou None)."""
    if not BACKLOG_PATH.is_file():
        return None
    data = json.loads(BACKLOG_PATH.read_text(encoding="utf-8"))
    if data.get("notBefore") and now < datetime.fromisoformat(data["notBefore"]):
        return None
    items = data.get("items", [])
    recent = [item for item in items if item.get("postedAt") and now - datetime.fromisoformat(item["postedAt"]) < timedelta(hours=24)]
    pending = [item for item in items if not item.get("postedAt") and item.get("attempts", 0) < COMMENT_ATTEMPTS]
    if not pending or len(recent) >= BACKLOG_PER_DAY:
        return None
    item = pending[0]
    try:
        response = youtube().commentThreads().insert(part="snippet", body={"snippet": {
            "videoId": item["videoId"], "topLevelComment": {"snippet": {"textOriginal": item["text"]}},
        }}).execute()
        item.update(postedAt=now.isoformat(timespec="seconds"), commentId=response["id"], error=None)
    except Exception as error:  # noqa: BLE001 - registrado; tenta de novo nos próximos ciclos (até 5 vezes)
        item.update(error=str(error)[:300], attempts=item.get("attempts", 0) + 1)
        if "quotaExceeded" in str(error):  # cota do dia acabou: espera 6 h em vez de insistir
            data["notBefore"] = (now + timedelta(hours=6)).isoformat(timespec="seconds")
    BACKLOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return item["videoId"] if item.get("postedAt") else None


def pending_comments(since: datetime | None, now: datetime, folders: list[Path] | None = None) -> list[Path]:
    """No ar, enviados a partir de `since`, sem comentário e sem esgotar as tentativas."""
    if since is None:
        return []
    rows = []
    for folder in folders if folders is not None else video_folders():
        record = read_publication(folder) or {}
        uploaded = record.get("uploadedAt")
        if record.get("status") != "publicado" or not record.get("videoId") or record.get("commentId") or not uploaded:
            continue
        if datetime.fromisoformat(uploaded) < since or record.get("commentAttempts", 0) >= COMMENT_ATTEMPTS:
            continue
        last = record.get("commentAttemptAt")
        if last and now - datetime.fromisoformat(last) < COMMENT_RETRY:
            continue
        rows.append(folder)
    return rows


def uploads_last_24h(now: datetime) -> int:
    count = 0
    for record in archived_records():
        uploaded = record.get("uploadedAt")
        if uploaded and now - datetime.fromisoformat(uploaded) < timedelta(hours=24):
            count += 1
    for folder in video_folders():
        record = read_publication(folder)
        uploaded = record and record.get("uploadedAt")
        if uploaded and now - datetime.fromisoformat(uploaded) < timedelta(hours=24):
            count += 1
    return count


class Publisher(threading.Thread):
    """Roda dentro do painel: a cada minuto envia os aprovados (se a auditoria estiver aprovada) e confere os agendados."""

    def __init__(self, flow_reader, interval: int = 60) -> None:
        super().__init__(daemon=True, name="publicador")
        self.flow_reader, self.interval = flow_reader, interval
        self.lock = threading.Lock()
        self.last_error: str | None = None

    def run(self) -> None:
        while True:
            try:
                self.tick(datetime.now(UTC))
                self.last_error = None
            except Exception as error:  # noqa: BLE001 - mostrado no painel, tenta de novo no próximo ciclo
                self.last_error = f"{error}"
                traceback.print_exc()
            time.sleep(self.interval)

    def sync_output_marks(self) -> None:
        """Enviados pelo painel cujo "OK - " ainda não foi aplicado (arquivo estava em uso, por exemplo)."""
        for folder in video_folders():
            record = read_publication(folder) or {}
            if record.get("videoId") and record.get("status") in ("agendado", "publicado") and not record.get("outputOk"):
                mark_output_processed(folder)

    def sync_playlist(self, cfg: dict[str, Any]) -> None:
        """Playlist ligada no fluxo (decisão do usuário: só depois da auditoria): inclui os enviados que faltam."""
        if not cfg.get("playlist") or not cfg.get("playlistActive"):
            return
        for folder in pending_playlist():
            try:
                add_to_playlist(folder, cfg["playlist"])
            except Exception as error:  # noqa: BLE001 - registrado no vídeo; tenta de novo no próximo ciclo
                write_publication(folder, playlistError=str(error)[:500])

    def sync_comments(self, cfg: dict[str, Any], now: datetime) -> None:
        for folder in pending_comments(cfg.get("commentSince"), now):
            record = read_publication(folder) or {}
            try:
                post_comment(folder)
                write_publication(folder, commentError=None)
            except Exception as error:  # noqa: BLE001 - registrado no vídeo; nova tentativa em 10 min (até 5)
                write_publication(folder, commentError=str(error)[:500], commentAttempts=record.get("commentAttempts", 0) + 1,
                                  commentAttemptAt=now.isoformat(timespec="seconds"))

    def tick(self, now: datetime) -> None:
        cfg = settings(self.flow_reader())
        with self.lock:
            self.sync_output_marks()  # local, não depende da API nem da auditoria
        if not cfg["audit"] or not (cfg["times"] or cfg["immediate"]) or not TOKEN_PATH.is_file():
            return
        with self.lock:
            refresh_published(now)
            self.sync_playlist(cfg)
            self.sync_comments(cfg, now)
            sync_comment_backlog(now)
            self.sync_output_marks()
            for folder in approved_queue(cfg["cutoff"]):
                record = read_publication(folder)
                if record and record.get("status") == "erro" and record.get("attemptAt") \
                        and now - datetime.fromisoformat(record["attemptAt"]) < RETRY_AFTER:
                    continue
                if cfg["immediate"]:
                    # Agendado para 20 min após a aprovação (ou após o envio, se atrasou); cota estourada vira erro
                    # e nova tentativa em 30 min.
                    slot = immediate_slot(folder, now, cfg)
                else:
                    # Sem limite de envios por 24 h (pedido do usuário, 2026-10-10): o vídeo sobe na hora da aprovação
                    # e pega o primeiro horário livre da grade; cota estourada vira erro e nova tentativa em 30 min.
                    taken = taken_slots()
                    slot = next(slot for slot in slot_candidates(now, cfg) if slot not in taken)
                try:
                    upload(folder, slot)
                    self.sync_playlist(cfg)
                except Exception as error:  # noqa: BLE001 - registrado no vídeo, nova tentativa em 30 min
                    write_publication(folder, status="erro", error=str(error)[:1000])
                    if "quota" in str(error).lower():
                        return
