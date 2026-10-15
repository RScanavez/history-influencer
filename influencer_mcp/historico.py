"""Histórico de assuntos para nenhum Short repetir o assunto de outro (pedidos do usuário, 2026-10-04 e 2026-10-11).

Decisões de 2026-10-11: o MESMO PROBLEMA do dia a dia nunca volta, nem com outra solução (temas amplos como sono ou
menopausa continuam, com problemas diferentes). A lista fica em assuntos.md (fora do git desde 2026-10-14: cada máquina mantém a sua e a reconstrói
pelos vídeos do canal): cada vídeo novo entra nela assim que o roteiro é aceito; os vídeos do canal entram pela API do YouTube no
máximo uma vez por dia (SYNC_HOURS), não a cada roteiro. Antes de gastar GPU, o código compara as palavras do
problema com toda a lista. Até 2026-10-14 um Claude Haiku conferia paráfrases; saiu a pedido do usuário (menos IA):
quem evita paráfrases é o próprio Claude do lote, que recebe a lista inteira (research_topic_history).

A comparação usa as palavras de conteúdo do título e do tema (sem acentos, sem palavras genéricas do nicho como
"exercício" ou "depois dos 60"): é deliberadamente conservadora, para barrar só o que seria praticamente o mesmo
vídeo. O julgamento fino de "outro ângulo" fica com o Claude do lote, que recebe a lista completa.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED = PROJECT_ROOT / "generated"
CHANNEL_CACHE = GENERATED / "research" / "historico-canal.json"
CHANNEL_CACHE_HOURS = 12
SUBJECTS_PATH = PROJECT_ROOT / "assuntos.md"
SYNC_HOURS = 24

# Palavras que não diferenciam um vídeo de outro neste canal (todo vídeo tem algumas delas).
_STOP = set("""
a o as os um uma uns umas de da do das dos e em no na nos nas por para pra com sem que se ao aos sua seu suas seus
voce voces sobre como mais menos muito muita ja ate depois antes apos entre isso isto este esta esse essa qual quais
quando onde porque por que ou nao sim tem ter ser sao foi vai faz faca fazer pode podem deve devem saiba veja entenda
dicas dica guia simples facil faceis rapido certo certa certos certas melhor melhores novo nova jeito forma formas
todo todos toda todas sempre agora hoje diariamente melhore melhorar melhora usando usar uso leve
exercicio exercicios exercitar treino treinos treinar idoso idosos idosa idosas anos ano 50 60 70 50+ 60+ mulher
mulheres homem homens maturidade terceira idade saude saudavel diario diaria diarios diarias dia dias casa video
""".split())


def _words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    words = set()
    for word in re.findall(r"[a-z0-9+]+", text):
        if word in _STOP or len(word) < 3:
            continue
        # plural simples: "quedas" e "queda" contam igual
        words.add(word[:-1] if word.endswith("s") and len(word) > 4 else word)
    return words


def similarity(a: str, b: str) -> float:
    """Sobreposição das palavras de conteúdo (0 a 1) em relação ao MAIOR dos dois textos: um título curto e genérico
    ("Exercícios para evitar quedas") não pode bloquear todo vídeo novo que fale de quedas com outro ângulo."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


# Acima disto (e com ao menos 2 palavras de conteúdo em comum) é "o mesmo tema com o mesmo ângulo".
BLOCK_SCORE = 0.75
BLOCK_MIN_SHARED = 2


def is_repeat(a: str, b: str) -> bool:
    shared = _words(a) & _words(b)
    return len(shared) >= BLOCK_MIN_SHARED and similarity(a, b) >= BLOCK_SCORE


# Mesmo problema do dia a dia conta como repetido, mesmo com outro ângulo (2026-10-06: "levantar do sofá" saiu em 04/10
# e 05/10, um com halteres e outro sem, e os dois dividiram o mesmo público). Até 2026-10-11 valia só por 7 dias;
# desde então, para sempre.
PROBLEM_SCORE = 0.5


def problem(title: str) -> str:
    """O problema que o título promete resolver: a pergunta antes do "?" ("Difícil levantar do sofá depois dos 60?")."""
    return (title or "").split("?", 1)[0]


def same_problem(a: str, b: str) -> bool:
    pa, pb = problem(a), problem(b)
    return len(_words(pa) & _words(pb)) >= BLOCK_MIN_SHARED and similarity(pa, pb) >= PROBLEM_SCORE


def _batch_date(batch: str) -> str:
    return batch[:10] if re.match(r"\d{4}-\d{2}-\d{2}", batch or "") else ""


def local_videos(exclude: tuple[str, str] | None = None) -> list[dict[str, str]]:
    """Vídeos deste computador (em produção e arquivados): tema do roteiro e título do SEO."""
    rows = []
    for script_path in GENERATED.glob("*/*/*/motion-script.json"):
        folder = script_path.parent
        if exclude and (folder.parent.name, folder.name) == exclude:
            continue
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        title = ""
        metadata = folder / "youtube-metadata.json"
        if metadata.is_file():
            try:
                title = json.loads(metadata.read_text(encoding="utf-8")).get("title", "")
            except (OSError, json.JSONDecodeError):
                pass
        rows.append({"source": f"{folder.parent.name}/{folder.name}", "title": title or script.get("title", ""),
                     "topic": script.get("topic", ""), "date": _batch_date(folder.parent.name)})
    return rows


def channel_videos(refresh: bool = False) -> list[dict[str, str]]:
    """Títulos de todos os vídeos do canal (cache de 12 h; sem conexão, usa o último cache)."""
    if CHANNEL_CACHE.is_file() and not refresh:
        cached = json.loads(CHANNEL_CACHE.read_text(encoding="utf-8"))
        if datetime.now(UTC) - datetime.fromisoformat(cached["at"]) < timedelta(hours=CHANNEL_CACHE_HOURS):
            return cached["videos"]
    try:
        from .youtube_publish import TOKEN_PATH, youtube

        if not TOKEN_PATH.is_file():
            raise RuntimeError("canal não conectado")
        client = youtube()
        uploads = client.channels().list(part="contentDetails", mine=True).execute()["items"][0]["contentDetails"][
            "relatedPlaylists"]["uploads"]
        videos, request = [], client.playlistItems().list(part="snippet", playlistId=uploads, maxResults=50)
        while request:
            response = request.execute()
            videos += [{"source": f"youtube:{item['snippet']['resourceId']['videoId']}", "title": item["snippet"]["title"],
                        "topic": "", "date": item["snippet"].get("publishedAt", "")[:10]}
                       for item in response.get("items", [])]
            request = client.playlistItems().list_next(request, response)
    except Exception:  # noqa: BLE001 - sem internet/token: segue com o cache antigo, se houver
        if CHANNEL_CACHE.is_file():
            return json.loads(CHANNEL_CACHE.read_text(encoding="utf-8"))["videos"]
        return []
    CHANNEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CHANNEL_CACHE.write_text(json.dumps({"at": datetime.now(UTC).isoformat(), "videos": videos}, ensure_ascii=False),
                             encoding="utf-8")
    return videos


_HASHTAG = re.compile(r"\s*#\S+")
_HEADER = """# Assuntos já abordados

Lista dos problemas do dia a dia que o canal já tratou: nenhum Short novo pode repetir um deles, nem com outra
solução (decisão do usuário, 2026-10-11). Mantida pelo código (`influencer_mcp/historico.py`): cada roteiro aceito
entra aqui na hora; os vídeos do canal entram pela API do YouTube uma vez por dia. Vídeo excluído no painel sai.
Pode editar à mão: uma linha por vídeo, `- data | problema | título | origem`. Apagar uma linha libera o assunto.

"""


def subject_of(title: str) -> str:
    """O problema do título, sem hashtags: a pergunta antes do "?" (ou o título inteiro, se não houver pergunta)."""
    return _clean(problem(title))


def _clean(text: str) -> str:
    return " ".join(_HASHTAG.sub(" ", text or "").replace("|", "/").split())


@contextmanager
def _locked(path: Path | None = None):
    """Trava simples entre processos (vários lotes podem gravar a lista ao mesmo tempo)."""
    lock = (path or SUBJECTS_PATH).with_suffix(".lock")
    for _ in range(300):
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 120:
                    lock.unlink(missing_ok=True)  # trava esquecida por um processo que caiu
            except OSError:
                pass
            time.sleep(0.1)
    else:
        raise RuntimeError(f"A lista de assuntos está travada: apague {lock} se nenhum lote estiver rodando.")
    try:
        yield
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)


def read_subjects(path: Path | None = None) -> tuple[list[dict[str, str]], str]:
    """Linhas de assuntos.md e o instante da última leitura do canal ("" se nunca)."""
    path = path or SUBJECTS_PATH
    if not path.is_file():
        return [], ""
    text = path.read_text(encoding="utf-8")
    synced = re.search(r"^- sincronizado-canal:\s*(\S+)", text, re.M)
    rows = []
    for line in text.splitlines():
        parts = [part.strip() for part in line[2:].split(" | ")] if line.startswith("- ") and " | " in line else []
        if len(parts) == 4:
            rows.append({"date": parts[0], "problem": parts[1], "title": parts[2], "source": parts[3]})
    value = synced.group(1) if synced else ""
    return rows, "" if value == "nunca" else value


def _write_subjects(rows: list[dict[str, str]], synced: str, path: Path | None = None) -> None:
    rows = sorted(rows, key=lambda row: (row["date"], row["source"]))
    lines = [f"- {row['date'] or '?'} | {row['problem']} | {row['title']} | {row['source']}" for row in rows]
    (path or SUBJECTS_PATH).write_text(
        _HEADER + f"- sincronizado-canal: {synced or 'nunca'}\n\n## Lista\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8")


def _row(source: str, title: str, date: str, topic: str = "") -> dict[str, str]:
    title = _clean(title) or _clean(topic)[:160]
    return {"date": date or "", "problem": subject_of(title), "title": title, "source": source}


def sync_subjects(force: bool = False, now: datetime | None = None) -> list[dict[str, str]]:
    """Lista de assuntos atualizada: soma os vídeos deste computador (sempre) e os do canal (API, 1x por dia)."""
    now = now or datetime.now(UTC)
    with _locked():
        rows, synced = read_subjects()
        known = {row["source"] for row in rows}
        titles = {row["title"].lower() for row in rows}
        fresh: list[dict[str, str]] = []

        def add(row: dict[str, str]) -> None:
            fresh.append(row)
            known.add(row["source"])
            titles.add(row["title"].lower())

        for item in local_videos():
            if item["source"] not in known and (item["title"] or item["topic"]):
                add(_row(item["source"], item["title"], item["date"], item["topic"]))
        stale = not synced or now - datetime.fromisoformat(synced) >= timedelta(hours=SYNC_HOURS)
        if force or stale:
            channel = channel_videos(refresh=True)
            if channel:
                synced = now.isoformat(timespec="seconds")
            for item in channel:
                # O mesmo vídeo já pode estar na lista pela pasta local (mesmo título): não duplica.
                if item["source"] not in known and _clean(item["title"]).lower() not in titles:
                    add(_row(item["source"], item["title"], item["date"]))
        if fresh or not SUBJECTS_PATH.is_file() or force or stale:
            rows += fresh
            _write_subjects(rows, synced)
        return rows


def register_subject(script: dict[str, Any], batch_id: str) -> None:
    """Reserva o assunto do roteiro aceito (antes de gastar GPU), para outro lote em paralelo não repeti-lo."""
    source = f"{batch_id}/{script.get('slug', '')}"
    with _locked():
        rows, synced = read_subjects()
        rows = [row for row in rows if row["source"] != source]
        rows.append(_row(source, script.get("title", ""), _batch_date(batch_id), script.get("topic", "")))
        _write_subjects(rows, synced)


def unregister_subject(batch_id: str, slug: str) -> None:
    """Vídeo excluído no painel (nunca publicado): o assunto volta a ficar livre."""
    if not SUBJECTS_PATH.is_file():
        return
    source = f"{batch_id}/{slug}"
    with _locked():
        rows, synced = read_subjects()
        kept = [row for row in rows if row["source"] != source]
        if len(kept) != len(rows):
            _write_subjects(kept, synced)


def history(exclude: tuple[str, str] | None = None) -> list[dict[str, str]]:
    """Todos os assuntos já abordados (assuntos.md, atualizada antes), menos o próprio vídeo."""
    own = f"{exclude[0]}/{exclude[1]}" if exclude else None
    return [row for row in sync_subjects() if row["source"] != own]


def find_repeat(title: str, topic: str, exclude: tuple[str, str] | None = None) -> dict[str, Any] | None:
    """Vídeo da lista com o mesmo problema do roteiro novo (em qualquer data), ou o mesmo tema e ângulo."""
    best = None
    for item in history(exclude):
        old = item.get("title") or ""
        if not old:
            continue
        candidates = []
        if title and same_problem(title, old):
            candidates.append((similarity(problem(title), problem(old)), "mesmo problema"))
        for new in (title, topic):
            if new and is_repeat(new, old):
                candidates.append((similarity(new, old), "mesmo tema e ângulo"))
        for score, reason in candidates:
            if not best or score > best["score"]:
                best = {**item, "score": round(score, 2), "reason": reason}
    return best


# Pausa temporária (pedido do usuário, 2026-10-05): marcha em pé e exercícios na cadeira saíram demais;
# nenhum roteiro novo com eles até a data abaixo. Cadeira só como apoio (abertura lateral, calcanhar em pé) pode.
PAUSED_UNTIL = "2000-01-01"  # pausa de exercícios do projeto Influencer; desligada no Historiador
PAUSED_EXERCISES = {"standing_march", "seated_march", "sit_to_stand", "seated_knee_extension", "seated_calf_raise"}
_PAUSED_WORDS = re.compile(r"\bmarcha|\bsentad[ao]s?\b|\b(?:treino|treinos|exercicios?) (?:na|de|com) cadeira\b")


def check_paused(script: dict[str, Any], today: str | None = None) -> None:
    today = today or datetime.now(UTC).date().isoformat()
    if today >= PAUSED_UNTIL:
        return
    used = sorted({scene.get(key) for scene in script.get("scenes", []) for key in ("exercise", "movement")} & PAUSED_EXERCISES)
    # Palavras só no título: o tema é uma descrição longa que costuma dizer o que o vídeo NÃO é ("não é exercício
    # sentado") e barrava vídeos permitidos (2026-10-06). O que a cena demonstra já é conferido pelos exercícios.
    text = unicodedata.normalize("NFKD", script.get("title", "")).encode("ascii", "ignore").decode().lower()
    if used or _PAUSED_WORDS.search(text):
        raise ValueError(
            f"Até {PAUSED_UNTIL}, nada de marcha (em pé ou sentada) nem exercício sentado/na cadeira "
            f"(sentar e levantar, extensão de joelho, calcanhar sentada){': ' + ', '.join(used) if used else ''}. "
            "Escolha outro tema e outros exercícios; cadeira só como apoio pode. Nenhuma imagem foi gerada."
        )


def check_new_script(script: dict[str, Any], batch_id: str) -> None:
    """Bloqueia (antes de gastar GPU) roteiro novo que repete o problema de um vídeo já feito; aceito, reserva o
    assunto em assuntos.md."""
    check_paused(script)
    title = script.get("title") or ""
    topic = script.get("topic", "")
    own = (batch_id, script.get("slug", ""))
    # "permitirTemaRepetido": decisão explícita do usuário para um roteiro específico (2026-10-14); nunca é posto por padrão.
    repeat = None if script.get("permitirTemaRepetido") else find_repeat(title, topic, exclude=own)
    if repeat:
        same = f"\"{repeat.get('title') or repeat.get('problem', '')}\" ({repeat.get('source', 'lista de assuntos')})"
        raise ValueError(
            f"Tema repetido: \"{title or topic[:120]}\" trata do mesmo problema de {same}: {repeat['reason']}. "
            "Nenhum problema do dia a dia volta ao canal, nem com outra solução (assuntos.md). Escolha um problema "
            "que não esteja na lista e envie de novo. Nenhuma imagem foi gerada."
        )
    register_subject(script, batch_id)
