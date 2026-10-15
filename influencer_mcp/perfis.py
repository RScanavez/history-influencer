"""Personagens (personagens.md) sobre a identidade visual global (identidade_visual.md).

identidade_visual.md guarda as identidades visuais (uma seção cada: modelo, LoRA, estilo do desenho, negativos gerais
e vídeo); a seção "Estilo global" é a identidade "aquarela", a atual.
personagens.md guarda cada personagem: descrição, negativos próprios, referência de rosto, olhar, vozes, gênero e a
identidade a que pertence ("- identidade:", vazio = aquarela). O painel escolhe identidade e personagem por lote.
O personagem padrão para vídeos NOVOS é a linha "- padrao:" de personagens.md. Cada roteiro grava o personagem com
que foi criado ("visualProfile"); refazer cenas ou traduzir um vídeo antigo usa sempre o dele. Roteiros sem o campo
são anteriores a 2026-10-03 e usam "ilustracao-homem". Os arquivos são relidos quando mudam (sem reiniciar nada).
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STYLE_PATH = PROJECT_ROOT / "identidade_visual.md"
CHARACTERS_PATH = PROJECT_ROOT / "personagens.md"
# Imagens de referência dos personagens (rosto fixo entre cenas, via IP-Adapter).
REFERENCE_DIR = PROJECT_ROOT / "personagens"
LEGACY = "ilustracao-homem"
DEFAULT = "aquarela-mulher"
DEFAULT_IDENTITY = "aquarela"
# Ids antigos que deixaram de existir e o personagem que os substitui (o fotográfico foi removido em 2026-10-03).
ALIASES = {"foto-mulher": "aquarela-mulher"}

PROFILES: dict[str, dict[str, Any]] = {}
IDENTITIES: dict[str, dict[str, str]] = {}
_loaded_mtimes: tuple[float, float] | None = None


def _sections(text: str) -> list[tuple[str, dict[str, str]]]:
    """Seções "## Título" com as linhas "- chave: valor" de cada uma."""
    result = []
    for match in re.finditer(r"^##\s+(.+?)\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S):
        values = {key.lower(): value.strip() for key, value in re.findall(r"^-\s*([\w-]+):[ \t]*(.*?)\s*$", match.group(2), re.M)}
        result.append((match.group(1), values))
    return result


def _yes(value: str | None) -> bool:
    return (value or "").strip().lower() in ("sim", "s", "yes", "true", "1")


def _number(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _join(*parts: str) -> str:
    return ", ".join(part.strip().strip(",") for part in parts if part and part.strip())


def identities_from(style_text: str) -> dict[str, dict[str, str]]:
    """Identidades visuais de identidade_visual.md: "## Estilo global" (id aquarela) e seções com "- id:"."""
    result: dict[str, dict[str, str]] = {}
    for title, values in _sections(style_text):
        identity_id = values.get("id") or (DEFAULT_IDENTITY if "global" in title.lower() else "")
        if identity_id:
            result[identity_id] = {**values, "nome": values.get("nome") or title}
    return result


def build(style_text: str, characters_text: str) -> tuple[dict[str, dict[str, Any]], str]:
    """Monta os perfis (formato usado pelo motion.py) e devolve também o personagem padrão."""
    identities = identities_from(style_text)
    profiles: dict[str, dict[str, Any]] = {}
    default = DEFAULT
    for title, values in _sections(characters_text):
        if "padrao" in values and "id" not in values:
            default = values["padrao"] or DEFAULT
            continue
        profile_id = values.get("id")
        if not profile_id:
            continue
        identity = values.get("identidade") or DEFAULT_IDENTITY
        if identities and identity not in identities:
            raise ValueError(f"Personagem {profile_id!r}: identidade visual {identity!r} não existe em identidade_visual.md.")
        style = identities.get(identity, {})
        lora = {"file": style.get("lora", ""), "strength": _number(style.get("lora-forca"), 0.6),
                "trigger": style.get("lora-gatilho", "")} if style.get("lora") else None
        reference = None
        if values.get("referencia"):
            reference = {"file": values["referencia"], "face_weight": _number(values.get("referencia-peso-rosto"), 0.5),
                         "look_weight": _number(values.get("referencia-peso-aparencia"), 0),
                         "start_at": _number(values.get("referencia-inicio"), 0.0)}
        # Chaves de estilo no próprio personagem, se presentes, valem no lugar das da identidade.
        own_lora = {"file": values["lora"], "strength": _number(values.get("lora-forca"), 0.6),
                    "trigger": values.get("lora-gatilho", "")} if values.get("lora") else lora
        gaze = tuple(option.strip() for option in values.get("olhar", "").split("|") if option.strip()) or None
        outfits = tuple(option.strip().rstrip(".") for option in values.get("roupas", "").split("|") if option.strip())
        voices = {lang: values[key] for lang, key in (("p", "voz-pt"), ("f", "voz-fr"), ("a", "voz-en")) if values.get(key)}
        profiles[profile_id] = {
            "label": values.get("nome") or title, "gender": values.get("genero") or "feminino", "identity": identity,
            "selectable": _yes(values.get("selecionavel")),
            "character": values.get("personagem", ""),
            "style": values.get("estilo") or style.get("estilo", ""),
            "negative": _join(values.get("negativo-global") or style.get("negativo", ""), values.get("negativo", "")),
            "video_style": values.get("video-estilo") or style.get("video-estilo", ""),
            "video_negative": values.get("video-negativo") or style.get("video-negativo", ""),
            "solo": values.get("uma-pessoa") or "(one single person, solo:1.4)",
            "use_env_lora": bool(own_lora), "lora": own_lora,
            "checkpoint": values.get("modelo") or style.get("modelo") or None,
            "reference": reference, "gaze": gaze, "voices": voices, "outfits": outfits,
            # Peso do estilo do desenho no prompt (vazio = sem peso). Com prompt-primeiro a cena vai com peso 1.2 e o
            # estilo, sem peso, perdia para o modelo fotográfico (closes viravam foto, 2026-10-05).
            "style_weight": _number(values.get("estilo-peso"), 0) or None,
            "prompt_first": _yes(values.get("prompt-primeiro")), "always_pose": _yes(values.get("pose-sempre")),
        }
    return profiles, default


def _refresh() -> None:
    """Relê os dois arquivos se mudaram desde a última leitura."""
    global _loaded_mtimes, _default
    mtimes = (STYLE_PATH.stat().st_mtime if STYLE_PATH.is_file() else 0.0,
              CHARACTERS_PATH.stat().st_mtime if CHARACTERS_PATH.is_file() else 0.0)
    if mtimes == _loaded_mtimes:
        return
    style_text = STYLE_PATH.read_text(encoding="utf-8") if STYLE_PATH.is_file() else ""
    characters_text = CHARACTERS_PATH.read_text(encoding="utf-8") if CHARACTERS_PATH.is_file() else ""
    profiles, _default = build(style_text, characters_text)
    if LEGACY not in profiles:
        raise ValueError(f"personagens.md precisa manter o personagem {LEGACY!r} (vídeos antigos dependem dele).")
    PROFILES.clear()
    PROFILES.update(profiles)
    IDENTITIES.clear()
    IDENTITIES.update(identities_from(style_text))
    _loaded_mtimes = mtimes


_default = DEFAULT
_refresh()


AUTO = "auto"
MALE_SHARE = 1 / 3  # análise de 2026-10-06: 40% do público é homem; ~1 vídeo com o personagem homem a cada 3
RECENT_VIDEOS = 12


def identities() -> list[dict[str, str]]:
    """Identidades visuais do seletor "Novo lote" (só as que têm personagem selecionável); a atual primeiro."""
    _refresh()
    used = {value["identity"] for key, value in PROFILES.items() if value.get("selectable") and has_face(key)}
    items = [{"id": key, "label": value["nome"]} for key, value in IDENTITIES.items() if key in used]
    return sorted(items, key=lambda item: item["id"] != DEFAULT_IDENTITY)


def selectable(identity: str | None = None) -> list[dict[str, str]]:
    """Personagens do seletor "Novo lote" do painel, de uma identidade visual (None = todas, por identidade). Em cada
    identidade, primeiro (padrão) o "Automático", que mantém a proporção de ~2 vídeos com a mulher para 1 com o homem."""
    _refresh()
    items = []
    for current in [identity] if identity else [item["id"] for item in identities()]:
        group = [{"id": key, "label": value["label"], "gender": value["gender"], "identity": current}
                 for key, value in PROFILES.items()
                 if value.get("selectable") and value["identity"] == current and has_face(key)]
        group = sorted(group, key=lambda item: item["id"] != _default)
        if {"feminino", "masculino"} <= {item["gender"] for item in group}:
            group.insert(0, {"id": AUTO, "label": "Automático (2 mulheres : 1 homem)", "gender": "auto",
                             "identity": current})
        items += group
    return items


def _by_gender(gender: str, identity: str = DEFAULT_IDENTITY) -> str:
    return next(key for key, value in PROFILES.items()
                if value.get("selectable") and value["gender"] == gender and value["identity"] == identity
                and has_face(key))


def recent_profiles(root: Path | None = None, limit: int = RECENT_VIDEOS) -> list[str]:
    """Personagens dos vídeos mais recentes (pelos roteiros gravados)."""
    root = root or PROJECT_ROOT / "generated"
    scripts = sorted(root.glob("*/*/*/motion-script.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    out = []
    for path in scripts[:limit]:
        try:
            out.append(of_script(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def recent_outfits(profile: str, root: Path | None = None, limit: int = RECENT_VIDEOS) -> list[str]:
    """Roupas dos vídeos mais recentes deste personagem (campo "outfit" dos roteiros), do mais novo ao mais velho."""
    root = root or PROJECT_ROOT / "generated"
    scripts = sorted(root.glob("*/*/*/motion-script.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    out = []
    for path in scripts:
        try:
            script = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if script.get("visualProfile") == profile and (script.get("outfit") or "").strip():
            out.append(script["outfit"].strip().rstrip("."))
            if len(out) >= limit:
                break
    return out


def outfit_for_new_video(profile: str, proposed: str | None, recent: list[str] | None = None) -> str | None:
    """Roupa de um vídeo NOVO de personagem com "roupas:" (pedido de 2026-10-11: novo Short, nova roupa; a mesma em
    todas as cenas). A do roteiro vale se não repetir uma recente; senão, sorteia uma da lista que não apareceu nos
    últimos vídeos (todas usadas: a usada há mais tempo). Personagem sem lista: devolve a do roteiro."""
    options = get(profile)["outfits"]
    proposed = (proposed or "").strip().rstrip(".") or None
    if not options:
        return proposed
    history = recent_outfits(profile) if recent is None else recent
    used = [item.lower() for item in history]
    if proposed and proposed.lower() not in used:
        return proposed
    fresh = [option for option in options if option.lower() not in used]
    if fresh:
        return random.choice(fresh)
    return max(options, key=lambda option: used.index(option.lower()))


def balanced_plan(quantity: int, recent: list[str] | None = None, identity: str = DEFAULT_IDENTITY) -> list[str]:
    """Personagem de cada vídeo do lote para chegar perto de 1 homem a cada 3 vídeos, contando os recentes."""
    _refresh()
    female, male = _by_gender("feminino", identity), _by_gender("masculino", identity)
    history = recent_profiles() if recent is None else recent
    men = sum(1 for name in history if PROFILES.get(name, {}).get("gender") == "masculino")
    total = len(history)
    plan = []
    for _ in range(quantity):
        pick = male if total and men / total < MALE_SHARE else female
        plan.append(pick)
        men += pick == male
        total += 1
    return plan


# Pose padrão quando o roteiro não escolhe uma, deduzida da ação descrita no keyframePrompt.
_POSE_HINTS = (
    (("sitting", "seated", "sits ", "sit on", "on a bench", "on a chair"), "seated_posture"),
    (("marching",), "march"),
    # "walking shoes" não é caminhar: só verbos de ação contam.
    (("walking along", "walking on", "walking slowly", "walking briskly", "walking through", "walks ", "strolling",
      "mid-stride", "jogging", "running"), "walk"),
    (("squat", "sit-to-stand", "sit to stand"), "squat"),
    (("one leg", "single leg", "balance"), "balance"),
)


def default_pose(prompt: str) -> str:
    text = prompt.lower()
    for words, pose in _POSE_HINTS:
        if any(word in text for word in words):
            return pose
    return "standing"


def profile_for_batch(batch_id: str, lotes: Path | None = None) -> str | None:
    """Personagem escolhido no painel para o lote (registro em generated/lotes/*.json), se houver."""
    lotes = lotes or PROJECT_ROOT / "generated" / "lotes"
    if not batch_id or not lotes.is_dir():
        return None
    _refresh()
    for path in lotes.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("batchId") == batch_id and record.get("profile") in PROFILES:
            return record["profile"]
    return None


def active_name(text: str | None = None) -> str:
    """Personagem padrão para vídeos novos: "- padrao:" de personagens.md."""
    _refresh()
    if text is None:
        name = _default
    else:
        match = re.search(r"^-\s*padrao:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
        name = match.group(1).strip() if match else DEFAULT
    if name not in PROFILES:
        raise ValueError(f"Personagem padrão desconhecido em personagens.md: {name!r}. Opções: {', '.join(PROFILES)}.")
    return name


def of_script(script: dict[str, Any] | None) -> str:
    _refresh()
    name = (script or {}).get("visualProfile") or LEGACY
    name = ALIASES.get(name, name)
    if name not in PROFILES:
        raise ValueError(f"Personagem desconhecido no roteiro: {name!r} (confira personagens.md).")
    return name


def get(name: str) -> dict[str, Any]:
    _refresh()
    return PROFILES[ALIASES.get(name, name)]


def voice(name: str, lang: str = "p") -> str:
    return get(name)["voices"].get(lang) or os.environ.get("KOKORO_VOICE", "pm_santa")


def reference_image(name: str) -> Path | None:
    """Imagem de referência do personagem (sem ela, cada cena reinventa o rosto). Personagem que declara uma
    referência e está sem o arquivo é recusado: o rosto PRECISA ser fixo (pedido do usuário, 2026-10-13)."""
    reference = get(name)["reference"]
    if not reference:
        return None
    path = REFERENCE_DIR / reference["file"]
    if not path.is_file():
        raise FileNotFoundError(f"Personagem {name!r} sem a imagem de rosto {path} (rosto fixo é obrigatório). "
                                "Gere e salve a referência antes de usar este personagem. Nenhuma imagem foi gerada.")
    return path


def has_face(name: str) -> bool:
    """Falso só para personagem que declara uma referência e está sem o arquivo (fora do seletor do painel)."""
    reference = get(name)["reference"]
    return not reference or (REFERENCE_DIR / reference["file"]).is_file()


def gaze(name: str, scene_index: int | None = None) -> str:
    """Olhar sorteado a cada imagem gerada, quando o personagem define opções."""
    options = get(name)["gaze"]
    return random.choice(options) if options else ""
