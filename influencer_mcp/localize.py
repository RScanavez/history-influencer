"""Versões do Short em outro idioma: o MESMO vídeo, só com a narração trocada.

Lê uma tradução (narração por cena, mais título, descrição e tags), narra cada cena com a mesma voz da Kokoro
no novo idioma e encaixa o áudio na duração exata do trecho original: acelera a fala quando passa do tempo e
completa com silêncio quando sobra. O vídeo original é copiado sem reprocessar (imagens e legendas intactas);
só a faixa de áudio muda. O MP4 e o TXT vão para output/ com o título traduzido e um sufixo (ex.: _FRA).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from .media import DESCRIPTION_INTRO, GENERATED_ROOT, _audio_duration, _get_executable, _run_checked, _title_filename, synthesize_narration

# Faixa de velocidade da fala: abaixo disso soa arrastada, acima fica atropelada.
MIN_SPEED, MAX_SPEED = 0.9, 1.4
SAMPLE_RATE = 24000

LANGUAGES = {
    "fr": {
        "kokoro": "f",
        "suffix": "_FRA",
        # Tradução da assinatura fixa das descrições (DESCRIPTION_INTRO em media.py; vai no fim).
        "intro": "Chaîne Historiador : un épisode d'histoire par jour, raconté en une minute. Abonnez-vous.",
    },
    "en": {
        "kokoro": "a",  # inglês americano
        "suffix": "_ENG",
        "intro": "Historiador channel: one episode of history a day, told in one minute. Subscribe.",
    },
}

Progress = Callable[[str], None] | None


def fitted_speed(translated_seconds: float, target_seconds: float) -> float:
    """Velocidade para a narração traduzida caber no trecho; se for mais curta, só desacelera até MIN_SPEED."""
    ratio = translated_seconds / target_seconds
    if 0.97 <= ratio <= 1.0:
        return 1.0
    # Margem de 2% para a fala não encostar no corte do trecho.
    return round(min(MAX_SPEED, max(MIN_SPEED, ratio * 1.02 if ratio > 1 else ratio)), 3)


def segment_scenes(video_dir: Path) -> list[int]:
    """Números das cenas que compõem o short.mp4, na ordem do segments.txt (respeita cenas suprimidas)."""
    manifest = (video_dir / "segments.txt").read_text(encoding="utf-8")
    return [int(number) for number in re.findall(r"scene-(\d+)/segment\.mp4", manifest)]


def write_localized_txt(destination: Path, metadata: dict[str, Any], video_name: str) -> Path:
    hashtags = " ".join(metadata.get("hashtags", []))
    intro = LANGUAGES.get(metadata.get("language", ""), {}).get("intro", DESCRIPTION_INTRO)
    # Assinatura no fim, como no português (2026-10-14).
    description = metadata["description"] + (f"\n\n{hashtags}" if hashtags else "") + f"\n\n{intro}"
    destination.write_text(
        "\n".join([
            "TÍTULO", metadata["title"], "",
            "DESCRIÇÃO (já com as hashtags)", description, "",
            "TAGS (separadas por vírgula)", ", ".join(metadata.get("tags", [])), "",
            "COMENTÁRIO PARA FIXAR (sugestão)", metadata.get("pinned_comment", ""), "",
            "ARQUIVO DE VÍDEO", video_name, "",
            "Publicação manual no YouTube Studio.", "",
        ]),
        encoding="utf-8-sig",
    )
    return destination


def translation_path(video_dir: Path, language: str = "fr") -> Path:
    return video_dir / f"translation-{language}.json"


def save_translation(video_dir: Path, translation: dict[str, Any]) -> Path:
    """Valida e grava a tradução na pasta do vídeo; a versão é montada quando o short.mp4 existir."""
    language = translation.get("language")
    if language not in LANGUAGES:
        raise ValueError(f"Idioma sem suporte: {language!r}.")
    require_enabled(language)
    for field in ("title", "description"):
        if not isinstance(translation.get(field), str) or not translation[field].strip():
            raise ValueError(f"A tradução precisa de {field}.")
    if len(translation["title"]) > 100:
        raise ValueError("O título traduzido passa de 100 caracteres.")
    from .media import clean_hashtags

    translation = {**translation, "hashtags": clean_hashtags(translation.get("hashtags") or [])}
    scenes = translation.get("scenes")
    if not isinstance(scenes, list) or not scenes or any(
        not isinstance(item, dict) or not isinstance(item.get("scene"), int)
        or not isinstance(item.get("narration"), str) or not item["narration"].strip()
        for item in scenes
    ):
        raise ValueError('Cada cena traduzida precisa de "scene" (número) e "narration".')
    video_dir.mkdir(parents=True, exist_ok=True)  # tradução pode chegar antes das imagens-chave (fila da GPU)
    destination = translation_path(video_dir, language)
    destination.write_text(json.dumps(translation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def enabled_languages() -> list[str]:
    """Idiomas extras a montar, da linha "- idiomas: pt" de fluxo_aprovacao.md (pt = só português, sem FR/EN).

    Sem a linha, valem todos (comportamento antigo). A flag só decide o que o projeto gera sozinho;
    "save_french_version"/"save_english_version" e o botão do painel continuam montando um idioma sob demanda.
    """
    from .flags import flag

    value = flag("idiomas")
    if value is None:
        return list(LANGUAGES)
    wanted = {item.strip().lower() for item in re.split(r"[,\s]+", value) if item.strip()}
    return [language for language in LANGUAGES if language in wanted]


def require_enabled(language: str) -> None:
    """Recusa idioma desligado em fluxo_aprovacao.md ("- idiomas:"): nada de tradução, SEO ou vídeo dele."""
    if language in LANGUAGES and language not in enabled_languages():
        raise ValueError(
            f"O idioma {language!r} está desativado (linha \"- idiomas:\" de fluxo_aprovacao.md). "
            "Nenhuma tradução foi salva nem montada; para reativar, inclua-o na linha, por exemplo \"idiomas: pt, fr, en\"."
        )


def localize_if_ready(
    video_dir: Path, *, progress: Progress = None, languages: list[str] | None = None,
) -> dict[str, Any] | None:
    """Monta as versões traduzidas que já têm tradução salva, se o short.mp4 existir (todas, ou só `languages`)."""
    if not (video_dir / "short.mp4").is_file():
        return None
    results = {}
    for language in languages or enabled_languages():
        path = translation_path(video_dir, language)
        if path.is_file():
            translation = json.loads(path.read_text(encoding="utf-8"))
            results[language] = build_localized_short(video_dir, translation, progress=progress)
    return results or None


def build_localized_short(video_dir: Path, translation: dict[str, Any], *, progress: Progress = None) -> dict[str, Any]:
    language = translation["language"]
    if language not in LANGUAGES:
        raise ValueError(f"Idioma sem suporte: {language!r}.")
    require_enabled(language)
    settings = LANGUAGES[language]
    source_video = video_dir / "short.mp4"
    if not source_video.is_file():
        raise FileNotFoundError(f"O vídeo original ainda não existe: {source_video}")
    scenes = segment_scenes(video_dir)
    translated = {int(item["scene"]): item["narration"] for item in translation["scenes"]}
    if sorted(translated) != sorted(scenes):
        raise ValueError(f"A tradução precisa ter exatamente as cenas do vídeo: {scenes}.")
    work = video_dir / language
    work.mkdir(exist_ok=True)
    ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")

    from . import perfis

    script_file = video_dir / "motion-script.json"
    script = json.loads(script_file.read_text(encoding="utf-8")) if script_file.is_file() else {}
    voice = perfis.voice(perfis.of_script(script), settings["kokoro"])  # voz do perfil com que o vídeo nasceu
    report, fitted = [], []
    for number in scenes:
        target_seconds = _audio_duration(ffprobe, video_dir / f"scene-{number:02d}" / "segment.mp4")
        raw = work / f"scene-{number:02d}-raw.wav"
        synthesize_narration(translated[number], raw, lang=settings["kokoro"], speed=1.0, voice=voice)
        speed = fitted_speed(_audio_duration(ffprobe, raw), target_seconds)
        if speed != 1.0:
            synthesize_narration(translated[number], raw, lang=settings["kokoro"], speed=speed, voice=voice)
        spoken = _audio_duration(ffprobe, raw)
        # Se ainda passar do trecho mesmo na velocidade máxima, comprime o restante com atempo.
        tempo = f"atempo={spoken / target_seconds:.4f}," if spoken > target_seconds else ""
        scene_audio = work / f"scene-{number:02d}.wav"
        _run_checked(
            [ffmpeg, "-y", "-v", "error", "-i", str(raw), "-af",
             f"{tempo}apad,atrim=0:{target_seconds:.4f}", "-ar", str(SAMPLE_RATE), "-ac", "1", str(scene_audio)],
            timeout=300,
        )
        raw.unlink(missing_ok=True)
        fitted.append(scene_audio)
        report.append({"scene": number, "segment": round(target_seconds, 2), "spoken": round(min(spoken, target_seconds), 2),
                       "speed": speed, "compressed": bool(tempo)})
        if progress:
            progress(f"cena {number}: fala {spoken:.1f}s em trecho de {target_seconds:.1f}s (velocidade {speed})")

    listing = work / "audio.txt"
    listing.write_text("".join(f"file '{path.as_posix()}'\n" for path in fitted), encoding="utf-8")
    narration = work / "narration.wav"
    _run_checked([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(narration)],
                 timeout=600)
    video = work / "short.mp4"
    _run_checked(
        [ffmpeg, "-y", "-v", "error", "-i", str(source_video), "-i", str(narration),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", str(video)],
        timeout=1800,
    )

    base = _title_filename(translation["title"]) + settings["suffix"]
    from .formats import of_folder
    from .output_ok import export_files

    marker = work / "output-export.json"
    previous = json.loads(marker.read_text(encoding="utf-8")).get("base") if marker.is_file() else None
    txt = write_localized_txt(work / "youtube-metadata.txt", translation, f"{base}.mp4")
    files = {".mp4": video, ".txt": txt}
    if (video_dir / "thumbnail-base.png").is_file():
        from .thumbnails import compose_thumbnail

        text = translation.get("thumbnail_text") or translation["title"]
        files[".jpg"] = compose_thumbnail(video_dir / "thumbnail-base.png", text, work / "thumbnail.jpg")
    exported = export_files(of_folder(video_dir), base, previous, files)
    marker.write_text(json.dumps({"base": base}, ensure_ascii=False) + "\n", encoding="utf-8")
    (work / "localize-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "outputPath": str(exported),
        "durationSeconds": round(_audio_duration(ffprobe, video), 1),
        "originalSeconds": round(_audio_duration(ffprobe, source_video), 1),
        "scenes": report,
    }
