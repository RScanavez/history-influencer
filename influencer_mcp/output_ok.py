"""Marca de "publicado" feita à mão pelo usuário: renomear o arquivo em output/ com o prefixo "OK - ".

O nome base de cada arquivo exportado fica em output-export.json (na pasta do vídeo para o português e em
<pasta>/<idioma>/ para as versões traduzidas). Vale o .mp4; o .txt pode ser renomeado junto ou não.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

from .media import GENERATED_ROOT, _title_filename

# "OK - Título", "OK – Título", "OK_Título", "OK Título"; exige separador ou espaço depois do OK.
OK_PREFIX = re.compile(r"^\s*OK(?:\s*[-–—_:.]+\s*|\s+)", re.IGNORECASE)


def output_root() -> Path:
    return GENERATED_ROOT.parent / "output"


def _normalize(name: str) -> str:
    return unicodedata.normalize("NFC", " ".join(name.split())).casefold()


def output_base(folder: Path, language: str | None = None) -> str | None:
    """Nome (sem extensão) com que o vídeo foi exportado para output/."""
    marker = (folder / language / "output-export.json") if language else (folder / "output-export.json")
    try:
        base = json.loads(marker.read_text(encoding="utf-8")).get("base")
        if base:
            return base
    except (OSError, json.JSONDecodeError):
        pass
    if language is None:
        try:
            title = json.loads((folder / "youtube-metadata.json").read_text(encoding="utf-8")).get("title")
            return _title_filename(title) if title else None
        except (OSError, json.JSONDecodeError):
            return None
    return None


def ok_names(root: Path | None = None) -> set[str]:
    """Nomes base (normalizados, sem o prefixo) dos .mp4 renomeados com "OK" em output/."""
    root = root or output_root()
    names = set()
    for folder in (root, root / "shorts", root / "videos", root / "archive" / "shorts", root / "archive" / "videos"):
        if not folder.is_dir():
            continue
        for path in folder.glob("*.mp4"):
            match = OK_PREFIX.match(path.stem)
            if match:
                names.add(_normalize(path.stem[match.end():]))
    return names


OK_TAG = "OK - "


def mark_processed(folder: Path) -> list[str]:
    """Prefixa "OK - " no .mp4 e no .txt em português deste vídeo (pasta do formato em output/).

    Usado quando o painel agenda o vídeo no YouTube. Devolve os nomes novos; arquivo ausente é ignorado e
    arquivo em uso (PermissionError) sobe a exceção para o publicador tentar de novo depois.
    """
    from .formats import of_folder, output_dir

    base = output_base(folder)
    if not base:
        return []
    target = output_dir(of_folder(folder))
    renamed = []
    for suffix in (".mp4", ".txt", ".jpg"):
        source, marked = target / f"{base}{suffix}", target / f"{OK_TAG}{base}{suffix}"
        if source.is_file() and not marked.exists():
            source.rename(marked)
            renamed.append(marked.name)
    return renamed


def archive_root() -> Path:
    return output_root() / "archive"


def _format_folders(fmt: str) -> tuple[Path, Path]:
    from .formats import FORMATS, output_dir

    return output_dir(fmt), archive_root() / FORMATS[fmt]["folder"]


def export_files(fmt: str, base: str, previous: str | None, files: dict[str, Path]) -> Path:
    """Copia os arquivos de um vídeo para output/, respeitando a marca "OK - " e o arquivo (archive/).

    Se o vídeo já está marcado "OK - " (na pasta do formato ou arquivado), a cópia nova vai para o mesmo lugar e
    com a mesma marca, em vez de criar uma duplicata sem marca. Nomes antigos (título mudou) são apagados.
    """
    home, archived = _format_folders(fmt)
    location = None
    for name in [name for name in (previous, base) if name]:
        location = next((folder for folder in (home, archived) if (folder / f"{OK_TAG}{name}.mp4").is_file()), None)
        if location:
            break
    target, tag = (location, OK_TAG) if location else (home, "")
    target.mkdir(parents=True, exist_ok=True)
    if previous and previous != base:
        for folder in (home, archived):
            for prefix in ("", OK_TAG):
                for suffix in (".mp4", ".txt", ".jpg"):
                    (folder / f"{prefix}{previous}{suffix}").unlink(missing_ok=True)
    for suffix, source in files.items():
        shutil.copyfile(source, target / f"{tag}{base}{suffix}")
    return target / f"{tag}{base}.mp4"


def sync_ok_covers(root: Path | None = None) -> list[str]:
    """Capa acompanha o vídeo: se "X.mp4" virou "OK - X.mp4", "X.jpg" vira "OK - X.jpg"."""
    root = root or output_root()
    renamed = []
    for folder in (root / "shorts", root / "videos", root / "archive" / "shorts", root / "archive" / "videos"):
        if not folder.is_dir():
            continue
        for video in folder.glob("*.mp4"):
            match = OK_PREFIX.match(video.stem)
            if not match:
                continue
            cover, marked = folder / f"{video.stem[match.end():]}.jpg", folder / f"{video.stem}.jpg"
            if cover.is_file() and not marked.exists():
                try:
                    cover.rename(marked)
                    renamed.append(marked.name)
                except OSError:
                    continue  # em uso: tenta de novo no próximo ciclo
    return renamed


def archive_published(root: Path | None = None) -> dict[str, list[str]]:
    """Move tudo que tem "OK - " em output/shorts e output/videos para output/archive/<mesma pasta>."""
    root = root or output_root()
    sync_ok_covers(root)
    moved: dict[str, list[str]] = {"shorts": [], "videos": []}
    failed = []
    for name in moved:
        source, target = root / name, root / "archive" / name
        target.mkdir(parents=True, exist_ok=True)
        if not source.is_dir():
            continue
        for path in sorted(source.iterdir()):
            if path.is_file() and path.suffix.lower() in (".mp4", ".txt", ".jpg") and OK_PREFIX.match(path.stem):
                try:
                    path.replace(target / path.name)
                    moved[name].append(path.name)
                except OSError:
                    failed.append(path.name)
    return {**moved, "failed": failed}


def archived_in_output(folder: Path, language: str | None = None) -> bool:
    """O .mp4 deste vídeo (nesse idioma) está em output/archive com "OK - "."""
    from .formats import FORMATS, of_folder

    base = output_base(folder, language)
    if not base:
        return False
    archived = archive_root() / FORMATS[of_folder(folder)]["folder"]
    if not archived.is_dir():
        return False
    target = _normalize(base)
    for path in archived.glob("*.mp4"):
        match = OK_PREFIX.match(path.stem)
        if match and _normalize(path.stem[match.end():]) == target:
            return True
    return False


def fully_archived(folder: Path) -> bool:
    """PT e as traduções já exportadas dos idiomas ATIVOS estão arquivadas (decisão do usuário, 2026-10-02).

    Idioma desligado em "- idiomas:" de fluxo_aprovacao.md não conta (2026-10-08): sobra de FR/EN antigos em
    output/ não impede arquivar nem limpar o vídeo."""
    from .localize import enabled_languages

    if not archived_in_output(folder):
        return False
    exported = [language for language in enabled_languages() if (folder / language / "output-export.json").is_file()]
    return all(archived_in_output(folder, language) for language in exported)


def published_in_output(folder: Path, language: str | None = None, names: set[str] | None = None) -> bool:
    base = output_base(folder, language)
    if not base:
        return False
    return _normalize(base) in (names if names is not None else ok_names())
