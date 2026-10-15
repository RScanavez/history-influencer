"""Shorts com movimento real: imagem-chave SDXL por cena animada pelo Wan 2.2 I2V no ComfyUI local."""

from __future__ import annotations

import io
import json
import math
import os
import random
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .formats import FORMATS, of_script, normalize as normalize_format
from .illustration import POSES, render_openpose_guide
from .thumbnails import compose_thumbnail
from . import movimentos
from .localize import localize_if_ready
from .media import (
    MAX_SCENES,
    MIN_SCENES,
    NARRATION_WORDS_PER_SECOND,
    _contact_sheet,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    GENERATED_ROOT,
    MAX_VIDEO_SECONDS,
    _audio_duration,
    _comfyui_url,
    _get_executable,
    _request_json,
    _run_checked,
    _slug,
    _upload_comfy_image,
    export_to_output,
    narration_voice,
    synthesize_narration,
    _wrap_text,
)

from . import gpu_auxiliar, perfis
from .comfyui import discard_outputs

Progress = Callable[[str], None] | None

# Wan 2.2 foi treinado com 81 quadros a 16 fps (~5 s por clipe).
WAN_FPS = 16
WAN_MAX_FRAMES = 81
WAN_WIDTH, WAN_HEIGHT = 480, 832
# A interpolação permite desacelerar até 25% para o clipe cobrir a narração sem cortar movimento.
MAX_SLOWDOWN = 1.25
OUTPUT_FPS = 30
DOWNLOAD_WORKERS = 8  # downloads simultâneos dos quadros HD de cada clipe

# Estilo global (identidade_visual.md) + personagem do roteiro (personagens.md), montados em perfis.py.
# Os nomes abaixo continuam apontando para o perfil antigo (homem, lápis e aquarela).
CHARACTER = perfis.PROFILES[perfis.LEGACY]["character"]
STYLE = perfis.PROFILES[perfis.LEGACY]["style"]
KEYFRAME_NEGATIVE = perfis.PROFILES[perfis.LEGACY]["negative"]
# Objetos proibidos por padrão que uma cena pode liberar com "allowObjects" (ex.: vídeo de rosca com halteres).
# Cada nome remove do negativo da imagem-chave os termos correspondentes, só naquela cena.
ALLOWABLE_OBJECTS = {"dumbbells": ("(dumbbells:1.5), ",)}


# Cenas de assunto ("showCharacter": false): só o tema na imagem (a meia, o calor), sem gente (pedido de 2026-10-03).
NO_PERSON_NEGATIVE = ", (person:1.6), (people:1.6), (woman:1.5), (man:1.5), human figure, face, hands"
# Cenas sem personagem (pedido do usuário, 2026-10-09): nada de gente nem mão entrando no quadro também na animação
# (no vídeo da barriga uma mão surgiu despejando aveia) e um movimento de câmera escolhido pelo sistema, em rodízio,
# em vez do "slow camera push-in" que os roteiros repetiam. Órbita é um arco parcial: o Wan parte de uma imagem só e
# inventaria (deformando) o lado de trás numa volta de 360 graus.
VIDEO_NO_PERSON_NEGATIVE = (", person, people, human, man, woman, face, hand, hands, fingers, arm, arms, body parts, "
                            "hand entering the frame, someone reaching into the frame")
# A órbita saiu em 2026-10-13 (pedido do usuário): era o movimento que mais deformava os objetos.
CAMERA_MOVES = {
    "aproximacao": "slow smooth camera dolly-in toward the main subject",
    "afastamento": "slow smooth camera dolly-out, gradually revealing more of the scene around the subject",
    "lateral": "smooth lateral camera tracking shot gliding from left to right past the subject",
    "grua": "slow camera crane up rising above the subject while tilting down to keep it in frame",
}
_CAMERA_ORDER = tuple(CAMERA_MOVES)
# Enquadramento das cenas sem personagem (2026-10-13, pedido do usuário: quase todas saíam como close do objeto na
# mesa, com cara de slide). O roteiro escolhe em "framing" ou o sistema distribui entre as cenas de objeto do vídeo;
# a câmera vem do enquadramento (a do campo "camera" do roteiro, se houver, vale no lugar).
FRAMINGS = {
    "close": ("close-up shot of the subject, shallow depth of field, softly blurred background", "lateral"),
    "de-cima": ("top-down flat lay view from directly above, the subject neatly arranged on the surface", "grua"),
    "ambiente": ("wide establishing shot of the whole room or place, the subject clearly visible in its setting",
                 "afastamento"),
    "detalhe": ("extreme macro close-up of one small detail of the subject, texture clearly visible", "aproximacao"),
}
_FRAMING_ORDER = tuple(FRAMINGS)


def assign_framings(script: dict[str, Any]) -> dict[str, Any]:
    """Enquadramento de cada cena sem personagem que o roteiro não escolheu: os quatro em rodízio, começando num
    ponto que muda de vídeo para vídeo (pelo slug), para nenhum vídeo repetir o de outro."""
    start = zlib.crc32(str(script.get("slug", "")).encode()) % len(_FRAMING_ORDER)
    used = 0
    scenes = []
    for scene in script.get("scenes", []):
        if scene.get("showCharacter") is False and scene.get("framing") not in FRAMINGS:
            scene = {**scene, "framing": _FRAMING_ORDER[(start + used) % len(_FRAMING_ORDER)]}
        if scene.get("showCharacter") is False:
            used += 1
        scenes.append(scene)
    return {**script, "scenes": scenes}


def subject_prompt(scene: dict[str, Any]) -> str:
    """Prompt da imagem-chave sem personagem: o enquadramento da cena antes do que o roteiro descreve."""
    framing = FRAMINGS.get(scene.get("framing") or "")
    return f"{framing[0]}, {scene['keyframePrompt']}" if framing else scene["keyframePrompt"]


def subject_camera(scene: dict[str, Any], scene_number: int) -> str:
    """Movimento de câmera da cena sem personagem (o mesmo em todos os clipes da cena, que continuam um do outro):
    o do campo "camera" do roteiro; senão, o do enquadramento; senão (roteiros antigos), um rodízio por cena."""
    chosen = scene.get("camera")
    if chosen in CAMERA_MOVES:
        return chosen
    if scene.get("framing") in FRAMINGS:
        return FRAMINGS[scene["framing"]][1]
    return _CAMERA_ORDER[(scene_number - 1) % len(_CAMERA_ORDER)]


def subject_clip_prompt(motion: str, camera: str) -> str:
    """Prompt do clipe sem personagem: a câmera escolhida e só o que o assunto faz (o texto de câmera do roteiro sai)."""
    subject = ", ".join(part.strip() for part in motion.split(",") if part.strip() and "camera" not in part.lower())
    return CAMERA_MOVES[camera] + (f", {subject}" if subject else "")


# OpenPose só em cenas cuja fala traz repetições ou séries (pedido do usuário, 2026-10-03).
# Frequência ("três vezes por semana", "duas vezes ao dia") não é série: a fala final do aerobica-em-pe-cadeira foi
# recusada por isso (2026-10-04).
# Só conta com uma quantidade antes ("oito a dez vezes", "dez repetições", "três séries"): conselho geral como
# "aumentando as repetições pouco a pouco" parava o lote cardio-sentado-cadeira (2026-10-05).
_COUNT = (r"(?:\d+|uma|um|duas|dois|tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quatorze|catorze|"
          r"quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta)")
_SERIES_RE = re.compile(rf"\b{_COUNT}(?:\s+(?:a|ou|e)\s+{_COUNT})?\s+(?:vezes(?!\s+(?:por|ao|na|no|a\s+cada)\s+"
                        r"(?:semana|dia|m[eê]s))|repeti[cç][õo]es|repeti[cç][ãa]o|s[ée]ries?)\b", re.IGNORECASE)


def has_series(narration: str) -> bool:
    """A fala recomenda repetições/séries ("oito a dez vezes", "três séries de doze")."""
    return bool(_SERIES_RE.search(narration or ""))


def check_exercise_scenes(scenes: list[dict[str, Any]]) -> None:
    """Vídeo novo: cena com a personagem cuja fala traz repetições/séries demonstra um exercício da biblioteca."""
    from .exercicios import catalog

    missing = [index for index, scene in enumerate(scenes, 1)
               if scene.get("showCharacter", True) is not False and has_series(scene.get("narration", ""))
               and not scene.get("exercise")]
    if missing:
        options = "; ".join(f"{item['id']} ({item['label']})" for item in catalog())
        raise ValueError(
            f"As cenas {', '.join(map(str, missing))} falam de repetições ou séries, mas não dizem qual exercício "
            f"mostram. Preencha \"exercise\" com um exercício da biblioteca: {options}. Se nenhum servir, troque o "
            "exercício da fala por um deles. Nenhuma imagem foi gerada."
        )


def check_movement_scenes(scenes: list[dict[str, Any]]) -> None:
    """Biblioteca desligada (2026-10-08): cena com a personagem cuja fala traz repetições/séries precisa de "movement",
    o movimento escrito a partir dos manuais (movimentos.py); o texto detalhado é o que guia imagem e vídeo."""
    from .movimentos import MOVEMENTS

    missing = [index for index, scene in enumerate(scenes, 1)
               if scene.get("showCharacter", True) is not False and has_series(scene.get("narration", ""))
               and not scene.get("movement")]
    if missing:
        options = "; ".join(f"{movement.id} ({movement.label})" for movement in MOVEMENTS.values())
        raise ValueError(
            f"As cenas {', '.join(map(str, missing))} falam de repetições ou séries, mas não dizem qual movimento "
            f"mostram. Preencha \"movement\" com um destes: {options}. Se nenhum servir, troque o exercício da fala "
            "por um deles. Nenhuma imagem foi gerada.")


# Só exercícios da biblioteca (pedido do usuário, 2026-10-07): falas que ensinavam exercícios fora dela (torcer
# toalha, encolher os ombros, degrau, afundo, agachamento na parede, trote) viravam imagens de texto livre que não
# correspondiam ao exercício. Texto sem acento e em minúsculas.
_OUTSIDE_LIBRARY = [(re.compile(pattern), label) for pattern, label in (
    (r"\btoalha", "exercício com toalha"),
    (r"\belastic", "exercício com elástico"),
    (r"cabo de vassoura|\bbastao\b", "exercício com cabo de vassoura ou bastão"),
    (r"\bencolh\w* (?:\w+ )?ombros", "encolher os ombros"),
    (r"\bafundo|\bavanco\b", "afundo"),
    (r"agachamento (?:parado|isometrico|na parede|com bola|sumo|profundo)|\bcadeirinha\b|costas .{0,20}na parede",
     "agachamento na parede ou com bola"),
    (r"\bdegrau|\bstep\b", "exercício no degrau"),
    (r"\btrot\w*|\bcorr(?:er|o|a|ida|idinha)\b", "trote ou corrida"),
    (r"\bprancha|\babdominai?s?\b|\bponte\b|flexao (?:no chao|de bracos?)|\bburpee|\bpolichinelo",
     "exercício no chão ou de impacto"),
    (r"\balong(?:o|ar|a|ue|amento|amentos)\b(?! lateral)", "alongamento que não é o lateral"),
    (r"\b(?:perna|pe|calcanhar) (?:direit[ao] |esquerd[ao] )?para tras|chute para tras", "perna para trás"),
    (r"\b(?:circulos?|rotac\w*|gir\w*) (?:com |de |d?os |d?o |d?as )?(?:ombros|pescoco|tronco|bracos|quadril|punhos?)",
     "rotação ou círculos"),
    (r"\bbolinha|\bbola (?:entre|nos|nas|na mao)|\b(?:aperto|aperte|apertar) (?:a |uma )?bola", "exercício com bola"),
)]
# Fala que nomeia um exercício da biblioteca: a cena precisa do "exercise" para a imagem sair do esqueleto.
_LIBRARY_NAMES = re.compile(
    r"\brosca\b|elevacao lateral|desenvolvimento|agachamento|flexao na parede|ponta dos pes|elevacao (?:de|dos) "
    r"calcanhar|perna para o lado|abertura lateral|numa perna|em uma perna so|equilibrio (?:numa|em uma) perna|"
    r"alongamento lateral")


def _plain(text: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def library_exercises() -> list[dict[str, str]]:
    """Exercícios que um roteiro novo pode usar hoje (a biblioteca menos os pausados)."""
    from datetime import date

    from .exercicios import catalog
    from .historico import PAUSED_EXERCISES, PAUSED_UNTIL

    paused = PAUSED_EXERCISES if date.today().isoformat() < PAUSED_UNTIL else set()
    return [item for item in catalog() if item["id"] not in paused]


def check_library_only(scenes: list[dict[str, Any]]) -> None:
    """Vídeo novo: a fala só ensina exercícios da biblioteca, e toda cena com a personagem que nomeia um deles
    demonstra esse exercício (campo "exercise")."""
    outside = []
    unnamed = []
    for index, scene in enumerate(scenes, 1):
        text = _plain(scene.get("narration", ""))
        found = sorted({label for pattern, label in _OUTSIDE_LIBRARY if pattern.search(text)})
        if found:
            outside.append(f"cena {index}: {', '.join(found)}")
        elif (scene.get("showCharacter", True) is not False and not scene.get("exercise")
              and _LIBRARY_NAMES.search(text)):
            unnamed.append(index)
    options = "; ".join(f"{item['id']} ({item['label']})" for item in library_exercises())
    if outside:
        raise ValueError(
            "O roteiro ensina exercícios que não estão na biblioteca (" + " | ".join(outside) + "). O vídeo só pode "
            f"sugerir e mostrar exercícios da biblioteca: {options}. Troque esses trechos por um deles (e ajuste a "
            "fala ao movimento da biblioteca) ou por dica do dia a dia sem exercício. Nenhuma imagem foi gerada.")
    if unnamed:
        raise ValueError(
            f"As cenas {', '.join(map(str, unnamed))} citam um exercício da biblioteca mas não dizem qual mostram. "
            f"Preencha \"exercise\" nelas: {options}. Nenhuma imagem foi gerada.")


def check_subject_scenes(scenes: list[dict[str, Any]]) -> None:
    """Cenas só do assunto, sem a personagem, são opcionais (desde 2026-10-13, pedido do usuário; antes eram ~30%
    obrigatórias): zero vale, e o teto evita vídeo que seja uma sequência de objetos."""
    total = len(scenes)
    subject = sum(1 for scene in scenes if scene.get("showCharacter") is False)
    high = max(1, -(-total * 4 // 10))
    if subject > high:
        raise ValueError(
            f"O roteiro tem {subject} cena(s) só do assunto (\"showCharacter\": false) em {total}; use no máximo {high}. "
            "Elas são opcionais: só quando a fala pedir mostrar um objeto ou uma ideia; nas demais, o personagem."
        )


def keyframe_negative(allowed: list[str] | None = None, profile: str = perfis.LEGACY) -> str:
    negative = perfis.get(profile)["negative"]
    for name in allowed or []:
        for term in ALLOWABLE_OBJECTS[name]:
            negative = negative.replace(term, "")
    return negative


VIDEO_NEGATIVE = perfis.PROFILES[perfis.LEGACY]["video_negative"]


def _wait_for_comfyui(base: str, *, timeout: int = 600) -> bool:
    """Se o ComfyUI caiu, sobe de novo (ou espera quem já está subindo) em vez de só aguardar."""
    from .comfyui import ensure_running

    try:
        ensure_running(base, timeout=timeout)
        return True
    except RuntimeError:
        return False


def _run_workflow(base: str, workflow: dict[str, Any], *, timeout: int, attempts: int = 3) -> list[dict[str, Any]]:
    """Enfileira o workflow e devolve as imagens de saída; se o ComfyUI cair, espera voltar e reenvia."""
    for attempt in range(1, attempts + 1):
        try:
            return _run_workflow_once(base, workflow, timeout=timeout)
        except RuntimeError as error:
            connection_lost = "Falha ao comunicar com o ComfyUI" in str(error)
            if not connection_lost or attempt == attempts or not _wait_for_comfyui(base):
                raise
    raise AssertionError("inalcançável")


def _run_workflow_once(base: str, workflow: dict[str, Any], *, timeout: int) -> list[dict[str, Any]]:
    result = _request_json(f"{base}/prompt", {"prompt": workflow}, timeout=60)
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str):
        raise RuntimeError(f"ComfyUI não aceitou o workflow: {json.dumps(result)[:1500]}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _waiting_in_queue(base, prompt_id):
            # O prazo só conta depois que o ComfyUI começa este workflow: uma imagem-chave na fila atrás de um clipe
            # longo estourava os 15 min sem ter rodado (2026-10-04).
            deadline = time.monotonic() + timeout
        history = _request_json(f"{base}/history/{urllib.parse.quote(prompt_id)}", timeout=30)
        record = history.get(prompt_id, {})
        status = record.get("status", {})
        if status.get("status_str") == "error":
            details = "; ".join(
                str(message[1]) for message in status.get("messages", [])
                if isinstance(message, list) and len(message) > 1
            )
            raise RuntimeError(f"O ComfyUI falhou no workflow. Detalhes: {details[:3000]}")
        if status.get("completed"):
            return [
                item
                for node_id in sorted(record.get("outputs", {}), key=lambda key: int(key) if key.isdigit() else 0)
                for item in record["outputs"][node_id].get("images", [])
                if isinstance(item, dict) and item.get("filename")
            ]
        time.sleep(5)
    raise TimeoutError(f"O ComfyUI não concluiu o workflow em {timeout // 60} minutos.")


def _waiting_in_queue(base: str, prompt_id: str) -> bool:
    try:
        queue = _request_json(f"{base}/queue", timeout=30)
    except Exception:  # noqa: BLE001 - sem resposta da fila: segue o prazo normal
        return False
    return any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in queue.get("queue_pending", []))


def _download(base: str, item: dict[str, Any]) -> bytes:
    query = urllib.parse.urlencode({
        "filename": item["filename"],
        "subfolder": item.get("subfolder", ""),
        "type": item.get("type", "output"),
    })
    with urllib.request.urlopen(f"{base}/view?{query}", timeout=120) as response:
        return response.read()


def style_for(fmt: str = "short", profile: str = perfis.LEGACY) -> str:
    settings = perfis.get(profile)
    style = f"({settings['style']}:{settings['style_weight']})" if settings.get("style_weight") else settings["style"]
    return f"{style}, {FORMATS[normalize_format(fmt)]['composition']}"


def keyframe_workflow(
    *, prompt: str, seed: int, pose_image: str | None, allowed_objects: list[str] | None = None, fmt: str = "short",
    with_character: bool = True, profile: str = perfis.LEGACY, gaze: str = "", reference_image: str | None = None,
    init_image: str | None = None, denoise: float = 1.0, pose_strength: float = 0.85, extra_negative: str = "",
) -> dict[str, Any]:
    """Imagem-chave SDXL. init_image + denoise < 1 refaz uma imagem existente (posição final do exercício a partir
    da inicial: mesmo cenário e roupa, só o corpo muda conforme o esqueleto)."""
    # Modelo e LoRA vêm da identidade visual global (identidade_visual.md); a variável de ambiente só substitui o modelo.
    checkpoint = (os.environ.get("COMFYUI_SCENE_CHECKPOINT") or perfis.get(profile).get("checkpoint")
                  or "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors")
    settings = perfis.get(profile)
    if settings.get("prompt_first"):
        character = f", {settings['character']}" if with_character else ""
        # Cena com peso extra: objetos e cenário do assunto sumiam atrás da personagem (teste de 2026-10-03).
        positive = f"({prompt.strip()}:1.2){character}{', ' + gaze if gaze else ''}. {style_for(fmt, profile)}"
    else:
        character = settings["character"] + ", " if with_character else ""
        positive = f"{character}{gaze + ', ' if gaze else ''}{prompt.strip()}. {style_for(fmt, profile)}"
    latent_width, latent_height = FORMATS[normalize_format(fmt)]["keyframe"]
    workflow: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": keyframe_negative(allowed_objects, profile) + ("" if with_character else NO_PERSON_NEGATIVE) + (f", {extra_negative}" if extra_negative else "") + (JIUJITSU_NEGATIVE if "bjj gi" in prompt.lower() else ""), "clip": ["1", 1]}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": latent_width, "height": latent_height, "batch_size": 1}},
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": 35, "cfg": 5.0, "sampler_name": "dpmpp_2m", "scheduler": "karras",
                "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                "latent_image": ["7", 0],
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "motion-keyframe", "images": ["9", 0]}},
    }
    if pose_image:
        controlnet = os.environ.get("COMFYUI_OPENPOSE_CONTROLNET") or "xinsir-openpose-sdxl.safetensors"
        workflow["4"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": controlnet}}
        workflow["5"] = {"class_type": "LoadImage", "inputs": {"image": pose_image}}
        # Força menor que nas cenas estáticas: guia a pose e deixa espaço para o cenário.
        workflow["6"] = {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["2", 0], "negative": ["3", 0], "control_net": ["4", 0], "image": ["5", 0],
                "strength": pose_strength, "start_percent": 0.0, "end_percent": 0.8,
            },
        }
        workflow["8"]["inputs"]["positive"] = ["6", 0]
        workflow["8"]["inputs"]["negative"] = ["6", 1]
    if init_image:
        workflow["30"] = {"class_type": "LoadImage", "inputs": {"image": init_image}}
        workflow["31"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["30", 0], "vae": ["1", 2]}}
        workflow["8"]["inputs"].update(latent_image=["31", 0], denoise=denoise)
        del workflow["7"]
    lora = settings.get("lora") or {}
    if lora.get("file"):
        # LoRA de estilo da identidade visual (hoje Canopus Pencil Art, gatilho "Pencil Art").
        strength = float(lora.get("strength", 0.6))
        if not 0 <= strength <= 1:
            raise ValueError("lora-forca, em identidade_visual.md, precisa estar entre 0 e 1.")
        workflow["11"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0], "clip": ["1", 1], "lora_name": lora["file"],
                "strength_model": strength, "strength_clip": strength,
            },
        }
        trigger = f"{lora['trigger']}, " if lora.get("trigger") else ""
        workflow["2"]["inputs"].update(text=f"{trigger}{positive}", clip=["11", 1])
        workflow["3"]["inputs"]["clip"] = ["11", 1]
        workflow["8"]["inputs"]["model"] = ["11", 0]
    reference = settings.get("reference")
    if reference_image and reference and with_character:
        # Mesma personagem em todas as cenas: rosto (Plus Face) forte e aparência geral (Plus) leve.
        adapter = {"weight_type": "linear", "combine_embeds": "concat", "start_at": reference.get("start_at", 0.0), "end_at": 1.0,
                   "embeds_scaling": "V only", "image": ["23", 0], "clip_vision": ["20", 0]}
        workflow.update({
            "20": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"}},
            "21": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "ip-adapter-plus-face_sdxl_vit-h.safetensors"}},
            "23": {"class_type": "LoadImage", "inputs": {"image": reference_image}},
            "24": {"class_type": "IPAdapterAdvanced", "inputs": {
                **adapter, "model": workflow["8"]["inputs"]["model"], "ipadapter": ["21", 0], "weight": reference["face_weight"]}},
        })
        workflow["8"]["inputs"]["model"] = ["24", 0]
        if reference.get("look_weight"):
            # Aparência geral (cabelo/roupa) da referência; desligado quando a roupa deve vir do prompt.
            workflow["22"] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "ip-adapter-plus_sdxl_vit-h.safetensors"}}
            workflow["25"] = {"class_type": "IPAdapterAdvanced", "inputs": {
                **adapter, "model": ["24", 0], "ipadapter": ["22", 0], "weight": reference["look_weight"]}}
            workflow["8"]["inputs"]["model"] = ["25", 0]
    return workflow


def _reference_upload(base: str, profile: str) -> str | None:
    path = perfis.reference_image(profile)
    return _upload_comfy_image(base, path) if path else None


def widen_pose_guide(guide: Path) -> None:
    """Guia de pose vertical (512x896) centralizado numa tela 16:9 (896x512) para as imagens-chave de vídeo."""
    with Image.open(guide) as opened:
        if opened.width >= opened.height:
            return
        scale = 512 / opened.height
        figure = opened.convert("RGB").resize((round(opened.width * scale), 512))
    canvas = Image.new("RGB", (896, 512), (0, 0, 0))
    canvas.paste(figure, ((896 - figure.width) // 2, 0))
    canvas.save(guide, format="PNG", optimize=True)


def generate_keyframe(
    scene: dict[str, Any], destination: Path, *, seed: int | None = None, fmt: str = "short",
    profile: str = perfis.LEGACY, scene_index: int | None = None,
) -> Path:
    base = _comfyui_url(require_avatar_checkpoint=False)
    pose_image = None
    show_character = scene.get("showCharacter", True) is not False
    if show_character and scene.get("exercise"):
        return generate_exercise_keyframes(scene, destination, seed=seed, fmt=fmt, profile=profile,
                                           scene_index=scene_index)
    # Esqueleto só quando a fala traz repetições/séries; nas outras cenas a pose do roteiro é ignorada.
    pose = scene.get("pose") if show_character and has_series(scene.get("narration", "")) else None
    if show_character and not pose and not scene.get("movement") and perfis.get(profile).get("always_pose"):
        pose = perfis.default_pose(scene["keyframePrompt"])
    if pose:
        if pose not in POSES:
            raise ValueError(f"Pose desconhecida: {pose!r}")
        guide = destination.with_name(f"{destination.stem}-pose.png")
        render_openpose_guide(pose_name=pose, destination=guide)
        if normalize_format(fmt) == "video":
            widen_pose_guide(guide)
        pose_image = _upload_comfy_image(base, guide)
    workflow = keyframe_workflow(
        prompt=scene["keyframePrompt"] if show_character else subject_prompt(scene),
        seed=seed if seed is not None else random.SystemRandom().randrange(0, 2**32),
        pose_image=pose_image,
        allowed_objects=scene.get("allowObjects"),
        fmt=fmt,
        profile=profile,
        gaze=perfis.gaze(profile, scene_index) if show_character else "",
        reference_image=_reference_upload(base, profile) if show_character else None,
        with_character=show_character,
        extra_negative=movimentos.avoid(movimentos.get(scene["movement"])) if show_character and scene.get("movement") else "",
    )
    return _render_keyframe(base, workflow, destination)


def _render_keyframe(base: str, workflow: dict[str, Any], destination: Path) -> Path:
    images = _run_workflow(base, workflow, timeout=900)
    if not images:
        raise RuntimeError("O ComfyUI não devolveu a imagem-chave.")
    with Image.open(io.BytesIO(_download(base, images[-1]))) as generated:
        generated.convert("RGB").save(destination, format="PNG")
    discard_outputs(base, images)
    return destination


# Cenas de exercício (biblioteca em exercicios.py; pedido do usuário, 2026-10-04).
EXERCISE_KEYFRAME_ATTEMPTS = 3
# Posição final refeita a partir da inicial (cenário e roupa parecidos). 0,65 mantinha a pose antiga (teste de
# 2026-10-04); cada nova tentativa solta mais, e a última gera do zero com a mesma semente.
EXERCISE_END_DENOISE = (0.8, 0.9, 1.0)
EXERCISE_CLIP_ATTEMPTS = 3
EXERCISE_REP_FRAMES = 81  # repetição completa conduzida pelo esqueleto (~5 s)
EXERCISE_HALF_FRAMES = 41  # subida ou descida entre quadro inicial e final (~2,5 s cada)
EXERCISE_CHECK_SAMPLES = 12
EXERCISE_VIDEO_NEGATIVE = (", static pose, no movement, frozen, morphing, warping, extra limbs, extra arms, extra legs, "
                           "distorted body, jittering, sudden changes")


def exercise_end_path(keyframe: Path) -> Path:
    return keyframe.with_name("keyframe-end.png")


def exercise_check_path(keyframe: Path) -> Path:
    return keyframe.with_name("exercise-check.json")


def _update_exercise_check(keyframe: Path, **values: Any) -> None:
    path = exercise_check_path(keyframe)
    report = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    report.update(values)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_exercise_keyframes(
    scene: dict[str, Any], destination: Path, *, seed: int | None, fmt: str, profile: str, scene_index: int | None,
) -> Path:
    """Posição inicial (keyframe.png) e final (keyframe-end.png) do exercício, com o esqueleto da biblioteca e
    conferidas pelo MediaPipe; até 3 tentativas cada, fica a melhor e o resultado vai para exercise-check.json."""
    from . import exercicios

    exercise = exercicios.get(scene["exercise"])
    base = _comfyui_url(require_avatar_checkpoint=False)
    reference = _reference_upload(base, profile)
    gaze = perfis.gaze(profile, scene_index)
    seed = seed if seed is not None else random.SystemRandom().randrange(0, 2**32)
    detector = exercicios.Detector()
    # "spec": imagens feitas com o esqueleto biomecânico (2026-10-06); a animação usa o mesmo esqueleto.
    report: dict[str, Any] = {"exercise": exercise.id, "verified": detector.available, "spec": exercicios.SPEC_VERSION}
    try:
        for which in ("start", "end"):
            guide = destination.with_name(f"{destination.stem}-pose-{which}.png")
            exercicios.render_guide(exercise, which, guide)
            if normalize_format(fmt) == "video":
                widen_pose_guide(guide)
            pose_image = _upload_comfy_image(base, guide)
            target = destination if which == "start" else exercise_end_path(destination)
            init = _upload_comfy_image(base, destination) if which == "end" else None
            best: tuple[Path, dict[str, Any]] | None = None
            for attempt in range(EXERCISE_KEYFRAME_ATTEMPTS):
                candidate = target.with_name(f"{target.stem}-tentativa-{attempt + 1}.png")
                workflow = keyframe_workflow(
                    prompt=exercicios.keyframe_prompt(exercise, which, scene["keyframePrompt"]),
                    seed=(seed + 7919 * attempt * (which == "start")) % 2**32, pose_image=pose_image,
                    allowed_objects=scene.get("allowObjects"), fmt=fmt, profile=profile, gaze=gaze,
                    reference_image=reference, init_image=init, denoise=EXERCISE_END_DENOISE[attempt] if init else 1.0,
                    pose_strength=1.0, extra_negative=exercise.avoid(which),
                )
                _render_keyframe(base, workflow, candidate)
                if detector.available:
                    check = exercicios.check_pose(detector.angles(candidate), exercise, which)
                else:
                    check = {"ok": None, "score": 0.0, "reason": "MediaPipe indisponível: posição não conferida"}
                check["attempt"] = attempt + 1
                if best is None or check["score"] > best[1]["score"]:
                    best = (candidate, check)
                if check["ok"] is not False:
                    break
            assert best is not None
            best[0].replace(target)
            for leftover in target.parent.glob(f"{target.stem}-tentativa-*.png"):
                leftover.unlink()
            report[which] = best[1]
    finally:
        detector.close()
    exercise_check_path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def fun_control_models() -> tuple[str, str, dict[str, Any]] | None:
    """Wan 2.2 Fun Control na GPU usada (local: GGUF Q4 na pasta do ComfyUI; remota: fun-alto/fun-baixo em
    gpu_remota.md). None: a máquina não tem o modelo e as cenas de exercício usam quadro inicial e final."""
    if os.environ.get("INFLUENCER_GPU") == "remota":
        from .gpu_remota import settings as remote_settings

        remote = remote_settings()
        if remote["fun_high"] and remote["fun_low"]:
            return remote["fun_high"], remote["fun_low"], {"class_type": "UNETLoader", "inputs": {"weight_dtype": "default"}}
        return None
    from .comfyui import comfyui_dir

    high = os.environ.get("WAN_FUN_HIGH_MODEL", "Wan2.2-Fun-A14B-Control_HighNoise-Q4_K_M.gguf")
    low = os.environ.get("WAN_FUN_LOW_MODEL", "Wan2.2-Fun-A14B-Control_LowNoise-Q4_K_M.gguf")
    unet = comfyui_dir() / "models" / "unet"
    if (unet / high).is_file() and (unet / low).is_file():
        return high, low, {"class_type": "UnetLoaderGGUF", "inputs": {}}
    return None


def wan_i2v_workflow(
    *,
    start_image: str,
    prompt: str,
    frames: int,
    seed: int,
    prefix: str,
    fmt: str = "short",
    profile: str = perfis.LEGACY,
    with_character: bool = True,
    end_image: str | None = None,
    control_video: str | None = None,
    fun_models: tuple[str, str, dict[str, Any]] | None = None,
    exercise: bool = False,
    outfit: str | None = None,
    interpolate: bool = True,
) -> dict[str, Any]:
    """Wan 2.2 I2V 14B (GGUF) com LoRA lightx2v de 4 passos, upscale 2x e interpolação RIFE 2x.

    interpolate=False tira o RIFE do workflow ("-hd" sai só ampliado): a segunda GPU interpola (gpu_auxiliar).

    Cenas de exercício (exercise=True): três etapas de amostragem, a primeira sem o acelerador e com CFG 3,5
    (o lightx2v sozinho deixa o movimento lento e ignora o prompt e o negativo); end_image ancora o último
    quadro (FLF2V); control_video (com fun_models) conduz o corpo por um vídeo de esqueleto (Fun Control)."""
    wan_width, wan_height = FORMATS[normalize_format(fmt)]["wan"]
    high = os.environ.get("WAN_HIGH_NOISE_MODEL", "Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf")
    low = os.environ.get("WAN_LOW_NOISE_MODEL", "Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf")
    loader = {"class_type": "UnetLoaderGGUF", "inputs": {}}
    if os.environ.get("INFLUENCER_GPU") == "remota":
        # H100 (decisão do usuário, 2026-10-03): Wan 2.2 completo em FP8, no carregador padrão do ComfyUI.
        # A GPU local (8 GB) continua com a versão compacta GGUF Q4.
        from .gpu_remota import settings as remote_settings

        remote = remote_settings()
        high, low = remote["wan_high"], remote["wan_low"]
        loader = {"class_type": "UNETLoader", "inputs": {"weight_dtype": "default"}}
    high_lora = os.environ.get("WAN_HIGH_NOISE_LORA", "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors")
    low_lora = os.environ.get("WAN_LOW_NOISE_LORA", "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors")
    settings = perfis.get(profile)
    video_style = f"{settings['video_style']}, " if settings["video_style"] else ""
    # Mesma roupa da imagem-chave (2026-10-09): sem ela no prompt, o Wan trocava a roupa no meio do clipe
    # (kimono da imagem-chave virava camiseta e bermuda no teste A/B da cena 4 do quadril).
    # A roupa padrão do personagem (", wearing ..." em personagens.md) sai quando o roteiro define outra: era
    # exatamente a camiseta azul e a bermuda cinza para as quais o kimono se transformava.
    character = settings["character"]
    wearing = ""
    if outfit and outfit.strip():
        character = re.split(r",\s*wearing\b", character, maxsplit=1)[0]
        wearing = f", wearing {outfit.strip().rstrip('.')} the whole time"
    if with_character:
        positive = (f"{prompt.strip()}. {character}{wearing}. Smooth natural motion, consistent face and clothing, "
                    f"{video_style}{style_for(fmt, profile)}")
    else:
        positive = f"{prompt.strip()}. No people. Smooth subtle natural motion, {video_style}{style_for(fmt, profile)}"
    if control_video:
        if not fun_models:
            raise ValueError("control_video exige o modelo Wan 2.2 Fun Control (fun_models).")
        high, low, loader = fun_models
    sampler = {"sampler_name": "euler", "scheduler": "simple", "cfg": 1.0, "steps": 4, "noise_seed": seed}
    workflow = {
        "1": {"class_type": loader["class_type"], "inputs": {**loader["inputs"], "unet_name": high}},
        "2": {"class_type": loader["class_type"], "inputs": {**loader["inputs"], "unet_name": low}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": high_lora, "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": low_lora, "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": 5.0}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": 5.0}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["7", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": settings["video_negative"] + ("" if with_character else VIDEO_NO_PERSON_NEGATIVE), "clip": ["7", 0]}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "11": {"class_type": "LoadImage", "inputs": {"image": start_image}},
        "12": {
            "class_type": "WanImageToVideo",
            "inputs": {
                "positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], "start_image": ["11", 0],
                "width": wan_width, "height": wan_height, "length": frames, "batch_size": 1,
            },
        },
        "13": {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                **sampler, "model": ["5", 0], "add_noise": "enable", "positive": ["12", 0], "negative": ["12", 1],
                "latent_image": ["12", 2], "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable",
            },
        },
        "14": {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                **sampler, "model": ["6", 0], "add_noise": "disable", "positive": ["12", 0], "negative": ["12", 1],
                "latent_image": ["13", 0], "start_at_step": 2, "end_at_step": 10000, "return_with_leftover_noise": "disable",
            },
        },
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["10", 0]}},
        # Quadros originais (480x832) para encadear o próximo clipe a partir do último quadro.
        "16": {"class_type": "SaveImage", "inputs": {"filename_prefix": f"{prefix}-raw", "images": ["15", 0]}},
        "17": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x2plus.pth"}},
        "18": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["17", 0], "image": ["15", 0]}},
        "19": {
            "class_type": "RIFE VFI",
            "inputs": {
                "frames": ["18", 0], "ckpt_name": "rife49.pth", "clear_cache_after_n_frames": 10,
                "multiplier": 2, "fast_mode": False, "ensemble": True, "scale_factor": 1.0,
                "dtype": "float32", "torch_compile": False, "batch_size": 1,
            },
        },
        "20": {"class_type": "SaveImage", "inputs": {"filename_prefix": f"{prefix}-hd", "images": ["19", 0]}},
    }
    size = {"width": wan_width, "height": wan_height, "length": frames, "batch_size": 1}
    conditioning = {"positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], **size}
    if control_video:
        workflow["22"] = {"class_type": "LoadVideo", "inputs": {"file": control_video}}
        workflow["23"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["22", 0]}}
        workflow["12"] = {"class_type": "Wan22FunControlToVideo", "inputs": {
            **conditioning, "ref_image": ["11", 0], "control_video": ["23", 0]}}
    elif end_image:
        workflow["24"] = {"class_type": "LoadImage", "inputs": {"image": end_image}}
        workflow["12"] = {"class_type": "WanFirstLastFrameToVideo", "inputs": {
            **conditioning, "start_image": ["11", 0], "end_image": ["24", 0]}}
    if not interpolate:
        del workflow["19"]
        workflow["20"]["inputs"]["images"] = ["18", 0]
    if exercise:
        workflow["9"]["inputs"]["text"] = settings["video_negative"] + EXERCISE_VIDEO_NEGATIVE
        # 6 passos: 0-2 alto sem acelerador (CFG 3,5), 2-3 alto com acelerador, 3-6 baixo com acelerador.
        workflow["21"] = {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}}
        steps = {**sampler, "steps": 6}
        workflow["13"]["inputs"].update({**steps, "model": ["21", 0], "cfg": 3.5, "end_at_step": 2})
        workflow["25"] = {"class_type": "KSamplerAdvanced", "inputs": {
            **steps, "model": ["5", 0], "add_noise": "disable", "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["13", 0], "start_at_step": 2, "end_at_step": 3, "return_with_leftover_noise": "enable"}}
        workflow["14"]["inputs"].update({**steps, "latent_image": ["25", 0], "start_at_step": 3})
    return workflow


def clip_plan(duration: float, motions: list[str]) -> list[tuple[str, int]]:
    """Divide a duração da narração em clipes Wan (4k+1 quadros), no máximo 81 cada."""
    needed_frames = duration * WAN_FPS / MAX_SLOWDOWN
    count = max(1, math.ceil(needed_frames / (WAN_MAX_FRAMES - 1)))
    per_clip = needed_frames / count
    frames = min(WAN_MAX_FRAMES, 4 * math.ceil(per_clip / 4) + 1)
    return [(motions[min(index, len(motions) - 1)], frames) for index in range(count)]


def animate_scene(
    scene: dict[str, Any],
    keyframe: Path,
    duration: float,
    work_dir: Path,
    *,
    seed: int,
    progress: Progress = None,
    fmt: str = "short",
    profile: str = perfis.LEGACY,
    yield_gpu: Callable[[], None] | None = None,
    background: ThreadPoolExecutor | None = None,
    pending: list[Future] | None = None,
) -> list[Path]:
    """Gera os clipes da cena encadeados pelo último quadro; devolve os quadros HD em ordem.

    Com `background` e `pending`, o download dos quadros de cada clipe roda em segundo plano e o Future vai para
    `pending`: os caminhos devolvidos só existem depois que todos terminarem."""
    scene = movimentos.apply(scene)  # cena com "movement": fases do movimento nos motions
    if scene.get("exercise") and scene.get("showCharacter", True) is not False:
        return animate_exercise_scene(scene, keyframe, duration, work_dir, seed=seed, progress=progress, fmt=fmt,
                                      profile=profile, yield_gpu=yield_gpu)
    base = _comfyui_url(require_avatar_checkpoint=False)
    start = keyframe
    hd_frames: list[Path] = []
    plan = clip_plan(duration, scene["motions"])
    with_character = scene.get("showCharacter", True) is not False
    number = re.search(r"(\d+)$", work_dir.name)
    camera = None if with_character else subject_camera(scene, int(number.group(1)) if number else 1)
    first_done = work_dir / "clip-1" / "done.json"
    if camera and first_done.is_file():
        recorded = json.loads(first_done.read_text(encoding="utf-8")).get("camera")
        camera = recorded if recorded in CAMERA_MOVES else camera
    # Duas GPUs: o RIFE sai do ComfyUI e roda na outra placa, em segundo plano, junto com o download dos quadros.
    aux = gpu_auxiliar.auxiliary()
    for clip_index, (motion, frames) in enumerate(plan, 1):
        clip_dir = work_dir / f"clip-{clip_index}"
        if (clip_dir / "done.json").is_file():
            done = json.loads((clip_dir / "done.json").read_text(encoding="utf-8"))
            hd_frames.extend(clip_dir / name for name in done["hd"])
            start = clip_dir / done["last_raw"]
            continue
        if yield_gpu:
            yield_gpu()  # imagens-chave na fila têm prioridade: pausa aqui, entre clipes, sem perder trabalho
        clip_dir.mkdir(parents=True, exist_ok=True)
        uploaded = _upload_comfy_image(base, start)
        prefix = f"motion-{work_dir.name}-{clip_index}-{int(time.time())}"
        started = time.monotonic()
        images = _run_workflow(
            base,
            wan_i2v_workflow(start_image=uploaded, prompt=subject_clip_prompt(motion, camera) if camera else motion,
                             frames=frames, seed=seed + clip_index, prefix=prefix, fmt=fmt, profile=profile,
                             with_character=with_character, outfit=scene.get("outfit"), interpolate=aux is None),
            timeout=4 * 3600,
        )
        raw = [item for item in images if item["filename"].startswith(f"{prefix}-raw")]
        hd = [item for item in images if item["filename"].startswith(f"{prefix}-hd")]
        if not raw or not hd:
            raise RuntimeError(f"O Wan não devolveu os quadros do clipe {clip_index} da cena.")
        seconds = round(time.monotonic() - started, 1)
        last_raw = clip_dir / "last-raw.png"
        last_raw.write_bytes(_download(base, raw[-1]))
        # O primeiro quadro de um clipe encadeado repete o último do anterior.
        skip_first = clip_index > 1
        if aux is None:
            wanted = [(f"hd-{index:04d}.png", item) for index, item in enumerate(hd[1 if skip_first else 0:], 1)]
            names = [name for name, _ in wanted]
        else:
            # Quadros só ampliados (todos: o primeiro entra na interpolação); o RIFE 2x faz 2N-1 quadros HD.
            wanted = [(f"up-{index:04d}.png", item) for index, item in enumerate(hd, 1)]
            names = [f"hd-{index:04d}.png" for index in range(1, 2 * len(hd) - (1 if skip_first else 0))]
        record = {"motion": motion, "camera": camera, "frames": frames, "seconds": seconds, "hd": names,
                  "last_raw": last_raw.name}
        interpolation = (aux, skip_first) if aux is not None else None
        if background is not None and pending is not None:
            # O próximo clipe só precisa do último quadro: os ~130 quadros HD descem enquanto a GPU já gera o próximo
            # (2026-10-09: a GPU ficava em 0% durante o download). done.json só é gravado depois deles, então uma
            # queda no meio refaz o clipe, como antes.
            pending.append(background.submit(_finish_clip, base, clip_dir, wanted, record, images, interpolation))
        else:
            _finish_clip(base, clip_dir, wanted, record, images, interpolation)
        hd_frames.extend(clip_dir / name for name in names)
        start = last_raw
        if progress:
            progress(f"{work_dir.name}: clipe {clip_index}/{len(plan)} em {seconds:.0f}s")
    return hd_frames


def _finish_clip(base: str, clip_dir: Path, wanted: list[tuple[str, dict[str, Any]]], record: dict[str, Any],
                 images: list[dict[str, Any]],
                 interpolation: tuple[gpu_auxiliar.Auxiliary, bool] | None = None) -> None:
    """Baixa os quadros HD do clipe, grava done.json e apaga as saídas do ComfyUI.

    Com `interpolation` (segunda GPU), baixa os quadros só ampliados numa subpasta e o RIFE 2x grava os HD."""
    folder = clip_dir / "ampliados" if interpolation else clip_dir
    folder.mkdir(parents=True, exist_ok=True)
    # Download em paralelo: na GPU remota, baixar ~130 quadros um a um era quase 1/3 do tempo do clipe.
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        list(pool.map(lambda pair: (folder / pair[0]).write_bytes(_download(base, pair[1])), wanted))
    discard_outputs(base, images)
    if interpolation:
        aux, skip_first = interpolation
        started = time.monotonic()
        record["interpolation"] = {"gpu": gpu_auxiliar.interpolate(aux, folder, clip_dir, record["hd"], skip_first),
                                   "seconds": round(time.monotonic() - started, 1)}
    (clip_dir / "done.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _download_frames(base: str, items: list[dict[str, Any]], folder: Path) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    wanted = [(f"hd-{index:04d}.png", item) for index, item in enumerate(items, 1)]
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        list(pool.map(lambda pair: (folder / pair[0]).write_bytes(_download(base, pair[1])), wanted))
    return [name for name, _ in wanted]


def animate_exercise_scene(
    scene: dict[str, Any],
    keyframe: Path,
    duration: float,
    work_dir: Path,
    *,
    seed: int,
    progress: Progress = None,
    fmt: str = "short",
    profile: str = perfis.LEGACY,
    yield_gpu: Callable[[], None] | None = None,
) -> list[Path]:
    """Cena de exercício: uma repetição animada e repetida até cobrir a narração.

    Com o Wan 2.2 Fun Control na GPU usada, a repetição inteira (posição inicial → final → inicial) é conduzida por
    um vídeo de esqueleto montado a partir das duas imagens-chave. Sem ele, dois clipes ancorados nas imagens-chave
    (FLF2V): subida e descida. O MediaPipe confere se o movimento chegou à posição final; até 3 tentativas."""
    from . import exercicios

    exercise = exercicios.get(scene["exercise"])
    end_keyframe = exercise_end_path(keyframe)
    if not end_keyframe.is_file():
        raise FileNotFoundError(f"A cena de exercício está sem a imagem-chave da posição final: {end_keyframe}. "
                                "Refaça a imagem-chave da cena.")
    gender = perfis.get(profile)["gender"]
    fun = fun_control_models()
    mode = "esqueleto" if fun else "quadros"
    base = _comfyui_url(require_avatar_checkpoint=False)
    detector = exercicios.Detector()
    expected_start, expected_end = exercicios.expected_angles(exercise)
    start_angles = detector.angles(keyframe) or expected_start
    end_angles = detector.angles(end_keyframe) or expected_end
    if mode == "esqueleto":
        clips = [("rep", keyframe, None, EXERCISE_REP_FRAMES)]
    else:
        clips = [("up", keyframe, end_keyframe, EXERCISE_HALF_FRAMES), ("down", end_keyframe, keyframe, EXERCISE_HALF_FRAMES)]
    clip_frames: list[list[Path]] = []
    checks: list[dict[str, Any]] = []
    try:
        for clip_index, (direction, start, end, frames) in enumerate(clips, 1):
            clip_dir = work_dir / f"clip-{clip_index}"
            done_path = clip_dir / "done.json"
            if done_path.is_file():
                done = json.loads(done_path.read_text(encoding="utf-8"))
                if done.get("mode") == mode:
                    clip_frames.append([clip_dir / name for name in done["hd"]])
                    checks.append(done.get("check") or {})
                    continue
            if clip_dir.exists():
                shutil.rmtree(clip_dir)  # clipe de outro modo (ex.: o Fun Control foi instalado depois)
            if yield_gpu:
                yield_gpu()
            clip_dir.mkdir(parents=True)
            prompt = exercicios.motion_prompt(exercise, direction, gender)
            control = None
            if mode == "esqueleto":
                ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
                destination = clip_dir / f"esqueleto-{work_dir.name}.mp4"
                size = FORMATS[normalize_format(fmt)]["wan"]
                check_file = exercise_check_path(keyframe)
                spec = json.loads(check_file.read_text(encoding="utf-8")).get("spec") if check_file.is_file() else None
                old_start, old_end = (None, None) if spec == exercicios.SPEC_VERSION else (
                    detector.points(keyframe), detector.points(end_keyframe))
                if spec == exercicios.SPEC_VERSION or not (old_start and old_end):
                    # Esqueleto da biblioteca (ângulos interpolados), encaixado sobre a pessoa da imagem-chave.
                    video = exercicios.control_video(exercise, frames, size, destination, ffmpeg,
                                                     detected_start=detector.points(keyframe))
                else:
                    # Imagens feitas antes do esqueleto biomecânico: segue a pessoa das duas imagens (método antigo),
                    # para o vídeo não pedir um ângulo de câmera diferente do das imagens.
                    video = exercicios.legacy_control_video(old_start, old_end, frames, size, destination, ffmpeg)
                control = _upload_comfy_image(base, video, "video/mp4")
            best: tuple[Path, dict[str, Any], list[str]] | None = None
            for attempt in range(EXERCISE_CLIP_ATTEMPTS):
                if attempt and yield_gpu:
                    yield_gpu()
                prefix = f"motion-{work_dir.name}-ex{clip_index}-{attempt + 1}-{int(time.time())}"
                started = time.monotonic()
                images = _run_workflow(base, wan_i2v_workflow(
                    start_image=_upload_comfy_image(base, start), prompt=prompt, frames=frames,
                    seed=seed + clip_index + 101 * attempt, prefix=prefix, fmt=fmt, profile=profile, outfit=scene.get("outfit"),
                    end_image=_upload_comfy_image(base, end) if end else None, control_video=control,
                    fun_models=fun, exercise=True,
                ), timeout=4 * 3600)
                hd = [item for item in images if item["filename"].startswith(f"{prefix}-hd")]
                if not hd:
                    raise RuntimeError(f"O Wan não devolveu os quadros do clipe {clip_index} da cena de exercício.")
                attempt_dir = clip_dir / f"tentativa-{attempt + 1}"
                names = _download_frames(base, hd, attempt_dir)
                discard_outputs(base, images)
                if direction == "down":
                    check: dict[str, Any] = {"ok": None, "reason": "descida entre as duas imagens-chave já conferidas"}
                elif not detector.available:
                    check = {"ok": None, "reason": "MediaPipe indisponível: movimento não conferido"}
                else:
                    sample = [names[int(i * (len(names) - 1) / (EXERCISE_CHECK_SAMPLES - 1))]
                              for i in range(EXERCISE_CHECK_SAMPLES)]
                    check = exercicios.rep_progress([detector.angles(attempt_dir / name) for name in sample],
                                                    start_angles, end_angles)
                check.update(attempt=attempt + 1, seconds=round(time.monotonic() - started, 1))
                if best is None or (check.get("progress") or 0) > (best[1].get("progress") or 0):
                    best = (attempt_dir, check, names)
                if progress:
                    progress(f"{work_dir.name}: exercício, clipe {clip_index}/{len(clips)} (tentativa {attempt + 1}): "
                             f"{check['reason']}")
                if check["ok"] is not False:
                    break
            assert best is not None
            best_dir, check, names = best
            for name in names:
                (best_dir / name).replace(clip_dir / name)
            for folder in clip_dir.glob("tentativa-*"):
                shutil.rmtree(folder)
            done_path.write_text(json.dumps({
                "mode": mode, "direction": direction, "motion": prompt, "frames": frames, "hd": names, "check": check,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            clip_frames.append([clip_dir / name for name in names])
            checks.append(check)
    finally:
        detector.close()
    _update_exercise_check(keyframe, animation={"mode": mode, "clips": checks})
    # Uma repetição: no modo de quadros, a descida começa no último quadro da subida.
    rep = clip_frames[0] + [frame for clip in clip_frames[1:] for frame in clip[1:]]
    rep_seconds = len(rep) / (WAN_FPS * 2)  # quadros HD já interpolados (RIFE 2x)
    count = max(1, round(duration / rep_seconds))
    return rep + rep[1:] * (count - 1)


def caption_overlay(caption: str, avatar_path: Path | None, destination: Path, fmt: str = "short") -> Path:
    """Avatar (e, se houver texto, painel de legenda) em PNG transparente do tamanho do quadro, para sobrepor ao vídeo."""
    FRAME_WIDTH, FRAME_HEIGHT = FORMATS[normalize_format(fmt)]["frame"]  # noqa: N806 - mesmo nome das constantes
    overlay = Image.new("RGBA", (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if caption.strip():
        font_path = Path(os.environ.get("CAPTION_FONT", r"C:\Windows\Fonts\arial.ttf"))
        font = ImageFont.truetype(str(font_path), 64) if font_path.is_file() else ImageFont.load_default()
        lines = _wrap_text(draw, caption, font, FRAME_WIDTH - 200)[:3]
        line_height = 84
        panel_height = len(lines) * line_height + 70
        top = FRAME_HEIGHT - panel_height - 260
        draw.rounded_rectangle((80, top, FRAME_WIDTH - 80, top + panel_height), radius=36, fill=(0, 0, 0, 170))
        y = top + 36
        for line in lines:
            draw.text((FRAME_WIDTH / 2, y), line, font=font, fill="white", anchor="mt")
            y += line_height
    if avatar_path is not None and avatar_path.is_file():
        with Image.open(avatar_path) as opened:
            avatar = ImageOps.fit(opened.convert("RGBA"), (200, 200))
        mask = Image.new("L", avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 199, 199), fill=255)
        avatar.putalpha(mask)
        overlay.alpha_composite(avatar, (FRAME_WIDTH - 250, 110))
    overlay.save(destination)
    return destination


def compose_scene(
    frames: list[Path], audio: Path, overlay: Path, destination: Path, ffmpeg: str, fmt: str = "short",
) -> Path:
    """Estica os quadros para cobrir a narração, sobrepõe legenda/avatar e grava o segmento."""
    FRAME_WIDTH, FRAME_HEIGHT = FORMATS[normalize_format(fmt)]["frame"]  # noqa: N806 - mesmo nome das constantes
    listing = destination.with_suffix(".frames.txt")
    audio_seconds = _audio_duration(_get_executable("FFPROBE_EXECUTABLE", "ffprobe"), audio)
    frame_seconds = audio_seconds / len(frames)
    listing.write_text(
        "".join(f"file '{frame.as_posix()}'\nduration {frame_seconds:.6f}\n" for frame in frames)
        + f"file '{frames[-1].as_posix()}'\n",
        encoding="utf-8",
    )
    _run_checked(
        [
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(overlay), "-i", str(audio),
            "-filter_complex",
            f"[0:v]scale={FRAME_WIDTH}:{FRAME_HEIGHT}:flags=lanczos:force_original_aspect_ratio=increase,"
            f"crop={FRAME_WIDTH}:{FRAME_HEIGHT},fps={OUTPUT_FPS},setsar=1[v];[v][1:v]overlay=0:0[out]",
            "-map", "[out]", "-map", "2:a", "-t", f"{audio_seconds:.3f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(destination),
        ],
        timeout=1800,
    )
    return destination


def motion_output_dir(influencer_name: str, batch_id: str, video_slug: str) -> Path:
    from .media import check_influencer

    check_influencer(influencer_name)
    return GENERATED_ROOT / _slug(influencer_name) / _slug(batch_id) / _slug(video_slug)


def validate_motion_script(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Valida o roteiro com plano visual: narração, legenda, imagem-chave e movimentos por cena."""
    slug = script.get("slug")
    scenes = script.get("scenes")
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("O roteiro precisa ter um slug válido.")
    if not isinstance(scenes, list) or not MIN_SCENES <= len(scenes) <= MAX_SCENES:
        raise ValueError(f"O roteiro precisa de {MIN_SCENES} a {MAX_SCENES} cenas.")
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValueError(f"A cena {index} precisa ser um objeto.")
        for field, limit in (("narration", 900), ("caption", 120), ("keyframePrompt", 400)):
            value = scene.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"A cena {index} precisa de {field} com 1 a {limit} caracteres.")
        motions = scene.get("motions")
        if (
            not isinstance(motions, list) or not 1 <= len(motions) <= 6
            or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in motions)
        ):
            raise ValueError(f"A cena {index} precisa de 1 a 6 motions (descrições em inglês de até 300 caracteres).")
        if index == 1:
            normalize_format(script.get("format"))  # formato inválido falha cedo, antes de gastar GPU
        allowed = scene.get("allowObjects")
        if allowed is not None and (not isinstance(allowed, list) or any(item not in ALLOWABLE_OBJECTS for item in allowed)):
            raise ValueError(f"allowObjects da cena {index} aceita só {sorted(ALLOWABLE_OBJECTS)}.")
        if "showCharacter" in scene and not isinstance(scene["showCharacter"], bool):
            raise ValueError(f"showCharacter da cena {index} precisa ser true ou false.")
        if scene.get("showCharacter") is False and scene.get("pose"):
            raise ValueError(f"A cena {index} não mostra a personagem; tire a pose dela.")
        if scene.get("pose") is not None and scene["pose"] not in POSES:
            raise ValueError(f"A pose da cena {index} não existe: {scene['pose']!r}.")
        if scene.get("movement") is not None:
            from .movimentos import MOVEMENTS

            if scene["movement"] not in MOVEMENTS:
                raise ValueError(f"O movimento da cena {index} não existe: {scene['movement']!r}. "
                                 f"Use um de: {', '.join(MOVEMENTS)} (research/describe_movements lista os detalhes).")
            if scene.get("showCharacter") is False:
                raise ValueError(f"A cena {index} não mostra a personagem; tire o movement dela.")
        if scene.get("framing") is not None:
            if scene["framing"] not in FRAMINGS:
                raise ValueError(f"O enquadramento da cena {index} não existe: {scene['framing']!r}. "
                                 f"Use um de: {', '.join(FRAMINGS)}.")
            if scene.get("showCharacter") is not False:
                raise ValueError(f"A cena {index} mostra a personagem; \"framing\" é só para cenas com "
                                 "\"showCharacter\": false.")
            if scene.get("exercise"):
                raise ValueError(f"A cena {index} tem exercise e movement; use só um (movement quando a biblioteca está desligada).")
        if scene.get("exercise") is not None:
            from .exercicios import EXERCISES

            if scene["exercise"] not in EXERCISES:
                raise ValueError(f"O exercício da cena {index} não está na biblioteca: {scene['exercise']!r}. "
                                 f"Use um de: {', '.join(EXERCISES)}.")
            if scene.get("showCharacter") is False:
                raise ValueError(f"A cena {index} não mostra a personagem; tire o exercise dela.")
    estimated = sum(len(scene["narration"].split()) / NARRATION_WORDS_PER_SECOND for scene in scenes)
    fmt = normalize_format(script.get("format"))
    cap = FORMAT_TARGETS[fmt][1] if fmt == "video" else MAX_VIDEO_SECONDS
    if estimated > cap + 5:
        raise ValueError(f"A narração estimada passa de {cap} segundos ({estimated:.0f}s).")
    return scenes


def _invalidate_animation(scene_dir: Path) -> None:
    for clip_dir in scene_dir.glob("clip-*"):
        if clip_dir.is_dir():
            shutil.rmtree(clip_dir)
    for name in ("segment.mp4", "segment.frames.txt", "overlay.png"):
        (scene_dir / name).unlink(missing_ok=True)


def keyframe_sheet(output_dir: Path, script: dict[str, Any]) -> Path:
    scenes = script["scenes"]
    labels = [{"pose": scene.get("pose") or "livre", "caption": scene["caption"], "narration": ""} for scene in scenes]
    images = [output_dir / f"scene-{index:02d}" / "keyframe.png" for index in range(1, len(scenes) + 1)]
    for index, scene in enumerate(scenes):
        end = exercise_end_path(images[index])
        if not (scene.get("exercise") and images[index].is_file() and end.is_file()):
            continue
        # Exercício: posição inicial e final lado a lado, com o resultado da conferência automática.
        check_path = exercise_check_path(images[index])
        report = json.loads(check_path.read_text(encoding="utf-8")) if check_path.is_file() else {}
        failed = any((report.get(which) or {}).get("ok") is False for which in ("start", "end"))
        labels[index]["pose"] = f"{scene['exercise']}{' ⚠ posição' if failed else ''} | início → fim"
        with Image.open(images[index]) as first, Image.open(end) as last:
            pair = Image.new("RGB", (first.width * 2 + 16, first.height), "white")
            pair.paste(first.convert("RGB"), (0, 0))
            pair.paste(last.convert("RGB").resize(first.size), (first.width + 16, 0))
        images[index] = images[index].with_name("keyframe-pair.png")
        pair.save(images[index])
    video = of_script(script) == "video"
    if video and (output_dir / "thumbnail.jpg").is_file():
        # A capa entra na folha para ser revisada junto com as cenas.
        labels.append({"pose": "capa", "caption": f"CAPA: {script.get('thumbnailText', '')}", "narration": ""})
        images.append(output_dir / "thumbnail.jpg")
    destination = output_dir / "keyframes-sheet.png"
    _contact_sheet(f"Imagens-chave: {script.get('topic') or script['slug']}", labels, images, destination, landscape=video)
    return destination


def _saved_script(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "motion-script.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _script_profile(previous_script: dict[str, Any], script: dict[str, Any], batch_id: str) -> str:
    # Vídeo existente mantém o seu; vídeo novo: o do roteiro, senão o personagem escolhido no lote, senão o ativo.
    return perfis.of_script(previous_script) if previous_script else (
        script.get("visualProfile") or perfis.profile_for_batch(batch_id) or perfis.active_name())


def check_engagement(scenes: list[dict[str, Any]]) -> None:
    """Análise de 2026-10-06: 0 comentários em 58 vídeos (a chamada só vinha no fim, quando metade já tinha saído).
    Vídeo novo precisa de uma pergunta numa cena do meio; com exercício, a cena 1 já mostra o movimento."""
    middle = scenes[1:-1]
    if not any("?" in scene["narration"] for scene in middle):
        raise ValueError("Ponha uma pergunta fácil de responder numa cena do MEIO do vídeo (não na primeira nem na "
                         "última), ex.: \"Você consegue? Responde 1, 2 ou 3 nos comentários\". O comentário fixado "
                         "repete essa pergunta.")
    for key in ("exercise", "movement"):
        used = next((scene[key] for scene in scenes if scene.get(key)), None)
        if used and not scenes[0].get(key):
            raise ValueError(f"A cena 1 precisa mostrar o movimento logo de cara: use \"{key}\": \"{used}\" nela "
                             "(a fala da cena 1 continua sendo o gancho; o sistema anima uma repetição).")


def check_new_video(script: dict[str, Any], batch_id: str) -> None:
    """Regras de vídeo novo que não precisam de GPU (cenas, exercícios, tema repetido, pausas)."""
    scenes = validate_motion_script(script)
    check_engagement(scenes)
    check_subject_scenes(scenes)
    check_scene_count(scenes, of_script(script))
    from .exercicios import library_enabled

    if library_enabled():
        check_exercise_scenes(scenes)
        check_library_only(scenes)
    else:
        check_movement_scenes(scenes)
        used = [str(index) for index, scene in enumerate(scenes, 1) if scene.get("exercise")]
        if used:
            raise ValueError(f"A biblioteca de exercícios está desligada (biblioteca-exercicios: não em fluxo_aprovacao.md): "
                             f"tire o campo \"exercise\" das cenas {', '.join(used)} e descreva o movimento no "
                             f"keyframePrompt e nos motions. Nenhuma imagem foi gerada.")
    # Mesmo tema com o mesmo ângulo de um vídeo do canal ou em produção: bloqueia antes de gastar GPU.
    from .historico import check_new_script

    check_new_script(script, batch_id)


def _narrate_scenes(output_dir: Path, script: dict[str, Any], previous: list[dict[str, Any]], profile: str,
                    ffprobe: str) -> float:
    """Narra as cenas novas ou alteradas e confere a duração do vídeo. Devolve a duração total."""
    scenes = script["scenes"]
    fmt = of_script(script)
    total_seconds = 0.0
    for index, scene in enumerate(scenes, 1):
        scene_dir = output_dir / f"scene-{index:02d}"
        scene_dir.mkdir(exist_ok=True)
        before = previous[index - 1] if index <= len(previous) else {}
        narration = scene_dir / "narration.wav"
        voice_marker = scene_dir / "narration.voice"
        # Texto narrado gravado ao lado do áudio: a narração feita na CPU enquanto o job esperava a GPU
        # (antes de o roteiro ser salvo) não é refeita depois.
        text_marker = scene_dir / "narration.text"
        voice = narration_voice(perfis.voice(profile, "p"))
        recorded_voice = voice_marker.read_text(encoding="utf-8").strip() if voice_marker.is_file() else None
        voice_changed = narration.is_file() and recorded_voice is not None and recorded_voice != voice
        recorded_text = text_marker.read_text(encoding="utf-8") if text_marker.is_file() else before.get("narration")
        if recorded_text != scene["narration"] or voice_changed:
            # A duração muda com o texto ou a voz, então a animação da cena também é refeita.
            narration.unlink(missing_ok=True)
            _invalidate_animation(scene_dir)
        if not narration.is_file():
            synthesize_narration(scene["narration"], narration, voice=perfis.voice(profile, "p"))
            voice_marker.write_text(voice, encoding="utf-8")
            text_marker.write_text(scene["narration"], encoding="utf-8")
        total_seconds += _audio_duration(ffprobe, narration)
    duration_cap = FORMAT_TARGETS[fmt][1] if fmt == "video" else MAX_VIDEO_SECONDS
    if total_seconds > duration_cap:
        raise ValueError(f"A narração tem {total_seconds:.1f}s; encurte o roteiro para até {duration_cap}s.")
    check_duration(script, total_seconds)
    return total_seconds


def prenarrate_keyframes(*, influencer_name: str, batch_id: str, script: dict[str, Any]) -> float:
    """Enquanto o job de imagens-chave espera a GPU: narra na CPU e confere a duração (pedido de 2026-10-05).
    Roteiro longo ou curto demais falha em minutos, sem esperar a fila; o roteiro só é salvo na etapa da GPU."""
    validate_motion_script(script)
    output_dir = motion_output_dir(influencer_name, batch_id, script["slug"])
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_script = _saved_script(output_dir)
    if previous_script and of_script(previous_script) != of_script(script):
        return 0.0  # mudança de formato: a etapa da GPU refaz tudo
    # Mesma meta de duração que a etapa da GPU usará (vídeo novo: a do formato; existente: a gravada).
    target = previous_script.get("targetSeconds") if previous_script else list(FORMAT_TARGETS[of_script(script)])
    script = {**script, "targetSeconds": target} if target else {k: v for k, v in script.items() if k != "targetSeconds"}
    profile = _script_profile(previous_script, script, batch_id)
    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")
    return _narrate_scenes(output_dir, script, previous_script.get("scenes", []), profile, ffprobe)


# Jiu-jitsu (pedido do usuário, 2026-10-07): o SDXL vestia roupa esportiva comum; o traje precisa ser kimono (gi) com faixa
# branca, descrito por extenso e com negativo próprio contra roupas erradas e distorções.
JIUJITSU_PATTERN = re.compile(r"jiu[\s-]?jitsu|(?<![a-z])bjj(?![a-z])|jujitsu|ju-jitsu|jiu\s*jitsu", re.IGNORECASE)
JIUJITSU_OUTFIT = (
    "bjj gi, traditional white brazilian jiu-jitsu gi kimono: heavy white cotton jacket with thick lapels crossed over "
    "the chest, matching white gi pants with drawstring waist reaching the ankles, long sleeves down to the wrists, "
    "and a plain white belt tied around the waist with a flat square knot, both belt ends hanging at the hip, "
    "barefoot, clean fabric with natural folds, no logos, no patches"
)
JIUJITSU_NEGATIVE = (
    ", (tank top:1.4), (t-shirt:1.4), (shorts:1.4), (leggings:1.3), (sportswear:1.3), (bare chest:1.4), (shirtless:1.4), "
    "(karate gi:1.2), (judo belt:1.2), (black belt:1.5), (colored belt:1.5), (blue belt:1.4), (red belt:1.4), "
    "(no belt:1.4), belt tied on chest, open jacket, torn gi, sneakers, shoes, boxing gloves, hakama, "
    "gi with text, logos, patches, kimono dress, bathrobe, extra belts, floating belt, belt through body, "
    "distorted hands, extra fingers, extra limbs, deformed body, warped fabric, melted clothing, mismatched sleeves"
)


def is_jiujitsu(script: dict[str, Any]) -> bool:
    text = " ".join([str(script.get("title", "")), str(script.get("theme", ""))]
                    + [f"{scene.get('narration', '')} {scene.get('keyframePrompt', '')}" for scene in script.get("scenes", [])])
    return bool(JIUJITSU_PATTERN.search(text))


def with_outfit(scene: dict[str, Any], outfit: str | None) -> dict[str, Any]:
    """Mesma roupa em todas as cenas com a personagem (campo "outfit" do roteiro, com peso): a roupa mudava de uma
    cena para outra do mesmo vídeo (teste de 2026-10-06)."""
    outfit = (outfit or "").strip().rstrip(".")
    if not outfit or scene.get("showCharacter", True) is False:
        return scene
    if outfit == JIUJITSU_OUTFIT:
        return {**scene, "keyframePrompt": f"{scene['keyframePrompt'].rstrip().rstrip('.')}, (wearing {outfit}:1.5)"}
    if outfit.lower() in scene["keyframePrompt"].lower():
        return scene
    return {**scene, "keyframePrompt": f"{scene['keyframePrompt'].rstrip().rstrip('.')}, (wearing {outfit}:1.3)"}


def prepare_motion_keyframes(
    *,
    influencer_name: str,
    batch_id: str,
    script: dict[str, Any],
    scene_numbers: list[int] | None = None,
    progress: Progress = None,
    yield_gpu: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Etapa rápida: salva o roteiro, narra cada cena com o Piper e gera as imagens-chave para revisão.

    Cenas cuja narração ou imagem-chave mudou, e as listadas em scene_numbers, têm a imagem-chave
    refeita e a animação descartada.
    """
    scenes = validate_motion_script(script)
    output_dir = motion_output_dir(influencer_name, batch_id, script["slug"])
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = output_dir / "motion-script.json"
    previous_script = _saved_script(output_dir)
    previous = previous_script.get("scenes", [])
    if previous_script.get("cornerAvatar") and "cornerAvatar" not in script:
        script = {**script, "cornerAvatar": True}  # vídeo antigo que manteve o avatar no canto
    profile = _script_profile(previous_script, script, batch_id)
    script = {**script, "visualProfile": profile}
    # GPU com que o vídeo nasceu (local/remota): refazer cenas e animar seguem nela. Vídeos antigos: local.
    gpu = previous_script.get("gpu") if previous_script else os.environ.get("INFLUENCER_GPU")
    if gpu and "gpu" not in script:
        script = {**script, "gpu": gpu}
    if previous_script.get("outfit") and not script.get("outfit"):
        script = {**script, "outfit": previous_script["outfit"]}  # refazer cenas mantém a roupa do vídeo
    if not previous_script:
        script = assign_framings(script)  # só vídeo novo: os existentes mantêm as imagens-chave como estão
        check_new_video(script, batch_id)
        if not is_jiujitsu(script):
            outfit = perfis.outfit_for_new_video(profile, script.get("outfit"))
            script = {**script, "outfit": outfit} if outfit else script
    # Vídeo novo usa sempre a meta do formato (Shorts 52-59 s, horizontais 210-270 s): roteiros chegavam com
    # "targetSeconds" [60, 90] de instruções antigas e eram aceitos fora da regra (2026-10-06). Vídeo existente mantém
    # a gravada (inclusive a dispensa do mínimo depois de remover cena).
    target = previous_script.get("targetSeconds") if previous_script else list(FORMAT_TARGETS[of_script(script)])
    script = {**script, "targetSeconds": target} if target else {k: v for k, v in script.items() if k != "targetSeconds"}
    if script.get("cornerAvatar"):
        avatar = GENERATED_ROOT / _slug(influencer_name) / "avatar.png"
        if not avatar.is_file():
            raise FileNotFoundError(f"Este vídeo usa o avatar no canto, mas ele não existe: {avatar}")
    fmt = of_script(script)
    if previous_script and of_script(previous_script) != fmt:
        # Mudou o formato: todas as imagens-chave (e animações) precisam ser refeitas no novo enquadramento.
        previous = []
        for scene_dir in output_dir.glob("scene-*"):
            keyframe = scene_dir / "keyframe.png"
            if keyframe.is_file():
                keyframe.rename(scene_dir / f"keyframe-rejeitada-{int(time.time())}.png")
            _invalidate_animation(scene_dir)
    selected = set(scene_numbers or [])
    if any(not 1 <= number <= len(scenes) for number in selected):
        raise ValueError(f"Informe números de cena entre 1 e {len(scenes)}.")

    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")

    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total_seconds = _narrate_scenes(output_dir, script, previous, profile, ffprobe)

    for index, scene in enumerate(scenes, 1):
        scene_dir = output_dir / f"scene-{index:02d}"
        before = previous[index - 1] if index <= len(previous) else {}
        keyframe = scene_dir / "keyframe.png"
        changed = any(before.get(field) != scene.get(field)
                      for field in ("keyframePrompt", "pose", "allowObjects", "showCharacter", "exercise", "movement", "framing"))
        # Cena de exercício precisa das duas posições (inicial e final).
        missing_end = bool(scene.get("exercise")) and scene.get("showCharacter", True) is not False             and not exercise_end_path(keyframe).is_file()
        if keyframe.is_file() and (index in selected or changed or missing_end):
            keyframe.rename(scene_dir / f"keyframe-rejeitada-{int(time.time())}.png")
            if exercise_end_path(keyframe).is_file():
                exercise_end_path(keyframe).rename(scene_dir / f"keyframe-end-rejeitada-{int(time.time())}.png")
            exercise_check_path(keyframe).unlink(missing_ok=True)
            _invalidate_animation(scene_dir)
        elif before.get("motions") != scene["motions"]:
            _invalidate_animation(scene_dir)
        if not keyframe.is_file():
            if yield_gpu:
                yield_gpu()  # pausa pedida no painel: para entre uma imagem e outra
            generate_keyframe(with_outfit(movimentos.apply(scene), JIUJITSU_OUTFIT if is_jiujitsu(script) else script.get("outfit")), keyframe, seed=scene.get("seed"), fmt=fmt,
                              profile=profile, scene_index=index)
            if progress:
                progress(f"imagem-chave da cena {index}/{len(scenes)} pronta")
    thumbnail = None
    if fmt == "video":
        thumbnail = prepare_thumbnail(output_dir, script, previous_script, progress=progress)
    sheet = keyframe_sheet(output_dir, script)
    return {
        "outputDirectory": str(output_dir),
        "keyframeSheetPath": str(sheet),
        "format": fmt,
        "thumbnailPath": str(thumbnail) if thumbnail else None,
        "sceneCount": len(scenes),
        "narrationSeconds": round(total_seconds, 1),
    }


# Duração obrigatória dos vídeos novos (pedido do usuário, 2026-10-03). Fica gravada no roteiro ao criá-lo;
# roteiros antigos, sem o campo, só respeitam o limite geral de MAX_VIDEO_SECONDS.
TARGET_SECONDS = (60, 90)  # padrão antigo; vídeos novos usam FORMAT_TARGETS
# Desde 2026-10-04 (decisão do usuário): Shorts de 52 a 59 s (retenção) e horizontais de ~4 min (210 a 270 s,
# centrado nos 240 s pedidos; a duração segue o conteúdo, sem esticar). Cenas: Shorts 5 a 7; horizontais 8 a 12
# cenas longas (~20-30 s), cada uma com vários clipes encadeados.
# Análise de 2026-10-06 (decisão do usuário): Shorts de 30 a 59 s; os de 56 s e 26 s tiveram a maior retenção.
# Decisão do usuário em 2026-10-11: Shorts novos de 35 a 45 s.
# Decisão do usuário em 2026-10-13: Shorts novos de 25 a 35 s (3 a 5 cenas de ~8 s).
# Mesma data: vídeos horizontais novos de 45 a 60 s (4 a 6 cenas de ~10 a 15 s).
FORMAT_TARGETS = {"short": (25, 35), "video": (45, 60)}
FORMAT_SCENES = {"short": (3, 5), "video": (4, 6)}


# Fonte ÚNICA dos limites de duração, cenas e palavras de cada formato. Prompts e agentes não repetem esses números:
# mandam chamar a ferramenta describe_format_limits (guarda: tests/test_perfis.py, FormatLimitsDocsTests).
VOICE_WORDS_PER_SECOND = {"feminina": 3.1, "masculina": 3.0}  # Kokoro, medido em 2026-10-09


def format_limits() -> dict[str, dict[str, Any]]:
    """Limites por formato, derivados de FORMAT_TARGETS e FORMAT_SCENES; palavras com margem de 1 s nas pontas."""
    limits = {}
    for fmt, (low, high) in FORMAT_TARGETS.items():
        scenes_low, scenes_high = FORMAT_SCENES[fmt]
        limits[fmt] = {
            "segundos": [low, high],
            "cenas": [scenes_low, scenes_high],
            "palavras_total": {
                voice: [math.ceil((low + 1) * rate), math.floor((high - 1) * rate)]
                for voice, rate in VOICE_WORDS_PER_SECOND.items()
            },
        }
    return limits


def check_scene_count(scenes: list[dict[str, Any]], fmt: str) -> None:
    low, high = FORMAT_SCENES[fmt]
    if not low <= len(scenes) <= high:
        kind = "Short" if fmt == "short" else "vídeo horizontal"
        raise ValueError(f"Um {kind} novo precisa de {low} a {high} cenas (o roteiro tem {len(scenes)}).")
WORDS_PER_SECOND = 3.2  # ef_dora, medido em 2026-10-03


def check_duration(script: dict[str, Any], total_seconds: float) -> None:
    """Recusa narração fora da faixa do roteiro, dizendo quantas palavras acrescentar ou tirar."""
    target = script.get("targetSeconds")
    if not target:
        return
    low, high = target
    if low <= total_seconds <= high:
        return
    if total_seconds < low:
        words = round((low + 3 - total_seconds) * WORDS_PER_SECOND)
        advice = f"acrescente cerca de {words} palavras de conteúdo útil (detalhe de execução, erro comum, variação), sem enchimento"
    else:
        words = round((total_seconds - high + 3) * WORDS_PER_SECOND)
        advice = f"retire cerca de {words} palavras"
    raise ValueError(
        f"A narração tem {total_seconds:.1f}s; o vídeo precisa ter entre {low} e {high}s: {advice}. "
        "As imagens-chave não foram geradas; ajuste as falas e envie o roteiro de novo."
    )


def thumbnail_text(script: dict[str, Any]) -> str:
    return (script.get("thumbnailText") or script.get("topic") or script["slug"]).strip()


def prepare_thumbnail(
    output_dir: Path, script: dict[str, Any], previous_script: dict[str, Any], *, progress: Progress = None,
) -> Path:
    """Capa do vídeo horizontal: ilustração do assunto central (thumbnailPrompt) + texto curto (thumbnailText)."""
    base_image = output_dir / "thumbnail-base.png"
    prompt = script.get("thumbnailPrompt") or script["scenes"][0]["keyframePrompt"]
    if not base_image.is_file() or previous_script.get("thumbnailPrompt") != script.get("thumbnailPrompt"):
        base = _comfyui_url(require_avatar_checkpoint=False)
        # Uma pessoa só: a capa já saiu com o personagem duplicado.
        profile = perfis.of_script(script)
        workflow = keyframe_workflow(prompt=f"{perfis.get(profile)['solo']}, {prompt}",
                                     seed=random.SystemRandom().randrange(0, 2**32), pose_image=None, fmt="video",
                                     profile=profile, gaze=perfis.gaze(profile, 1),
                                     reference_image=_reference_upload(base, profile))
        images = _run_workflow(base, workflow, timeout=900)
        if not images:
            raise RuntimeError("O ComfyUI não devolveu a imagem da capa.")
        with Image.open(io.BytesIO(_download(base, images[-1]))) as generated:
            generated.convert("RGB").save(base_image, format="PNG")
        discard_outputs(base, images)
        if progress:
            progress("imagem da capa pronta")
    thumbnail = compose_thumbnail(base_image, thumbnail_text(script), output_dir / "thumbnail.jpg")
    refresh_exported_thumbnails(output_dir)
    return thumbnail


def refresh_exported_thumbnails(output_dir: Path) -> None:
    """Capa refeita depois da exportação: substitui os .jpg já entregues em output/videos (PT, FR e EN)."""
    from .localize import enabled_languages, translation_path
    from .output_ok import export_files

    def replace_cover(base: str, cover: Path) -> None:
        # Só a capa: vai para onde o vídeo está (com "OK - " e em archive/, se for o caso).
        export_files("video", base, None, {".jpg": cover})

    marker = output_dir / "output-export.json"
    if marker.is_file() and (output_dir / "thumbnail.jpg").is_file():
        base = json.loads(marker.read_text(encoding="utf-8")).get("base")
        if base:
            replace_cover(base, output_dir / "thumbnail.jpg")
    for language in enabled_languages():
        lang_marker = output_dir / language / "output-export.json"
        translation_file = translation_path(output_dir, language)
        if not (lang_marker.is_file() and translation_file.is_file()):
            continue
        base = json.loads(lang_marker.read_text(encoding="utf-8")).get("base")
        translation = json.loads(translation_file.read_text(encoding="utf-8"))
        text = translation.get("thumbnail_text") or translation.get("title") or ""
        localized = compose_thumbnail(output_dir / "thumbnail-base.png", text, output_dir / language / "thumbnail.jpg")
        if base:
            replace_cover(base, localized)


def review_sheet(output_dir: Path, script: dict[str, Any], samples: int = 6) -> Path:
    """Folha de revisão da animação: uma linha por cena com quadros amostrados ao longo dos clipes."""
    thumb_width, thumb_height, label_height = 180, 312, 34
    rows = len(script["scenes"])
    sheet = Image.new("RGB", (samples * thumb_width, rows * (thumb_height + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    font_path = Path(os.environ.get("CAPTION_FONT", r"C:\Windows\Fonts\arial.ttf"))
    font = ImageFont.truetype(str(font_path), 20) if font_path.is_file() else ImageFont.load_default()
    for row, scene in enumerate(script["scenes"]):
        scene_dir = output_dir / f"scene-{row + 1:02d}"
        frames = sorted(scene_dir.glob("clip-*/hd-*.png"), key=lambda path: (path.parent.name, path.name))
        top = row * (thumb_height + label_height)
        draw.text((8, top + 6), f"{row + 1}. {scene['caption']}", font=font, fill=(30, 30, 30))
        for column in range(samples if frames else 0):
            frame = frames[int(column * (len(frames) - 1) / max(samples - 1, 1))]
            with Image.open(frame) as opened:
                thumb = ImageOps.fit(opened.convert("RGB"), (thumb_width, thumb_height))
            sheet.paste(thumb, (column * thumb_width, top + label_height))
    destination = output_dir / "motion-review.png"
    sheet.save(destination)
    return destination


def _join_segments(
    output_dir: Path, script: dict[str, Any], segments: list[Path], report: list[dict[str, Any]], ffmpeg: str, ffprobe: str,
) -> tuple[Path, float, Path, Path | None]:
    """Junta os trechos já montados no vídeo final, confere a duração e exporta para output/."""
    fmt = of_script(script)
    manifest = output_dir / "segments.txt"
    manifest.write_text("".join(f"file '{segment.as_posix()}'\n" for segment in segments), encoding="utf-8")
    video = output_dir / "short.mp4"
    _run_checked(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(video)],
        timeout=1800,
    )
    total = _audio_duration(ffprobe, video)
    video_cap = FORMAT_TARGETS[fmt][1] if fmt == "video" else MAX_VIDEO_SECONDS
    if total > video_cap:
        raise ValueError(f"O vídeo ficou com {total:.1f}s, acima de {video_cap}s.")
    review = review_sheet(output_dir, script)
    exported = export_to_output(output_dir)
    (output_dir / "motion-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return video, total, review, exported


def remount_from_segments(output_dir: Path, *, progress: Progress = None) -> dict[str, object]:
    """Remonta o vídeo (PT e FR/EN) só com os trechos já animados, sem GPU: usado depois de remover uma cena na
    revisão (pedido do usuário, 2026-10-05). Imagens, narração e animação de cada cena continuam as mesmas."""
    script = json.loads((output_dir / "motion-script.json").read_text(encoding="utf-8"))
    segments = [output_dir / f"scene-{index:02d}" / "segment.mp4" for index in range(1, len(script["scenes"]) + 1)]
    missing = [index for index, segment in enumerate(segments, 1) if not segment.is_file()]
    if missing:
        raise FileNotFoundError(f"Cenas sem trecho animado: {missing}; esse vídeo precisa passar pela animação.")
    ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")
    report = [{"scene": index, "seconds": round(_audio_duration(ffprobe, segment), 2)}
              for index, segment in enumerate(segments, 1)]
    video, total, _, _ = _join_segments(output_dir, script, segments, report, ffmpeg, ffprobe)
    if progress:
        progress(f"vídeo remontado: {len(segments)} cenas, {total:.1f}s")
    localized = localize_if_ready(output_dir, progress=progress)
    return {"videoPath": str(video), "durationSeconds": round(total, 1), "languages": sorted(localized or {})}


def animate_motion_short(
    *,
    influencer_name: str,
    batch_id: str,
    video_slug: str,
    scene_numbers: list[int] | None = None,
    progress: Progress = None,
    yield_gpu: Callable[[], None] | None = None,
    gpu_done: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Etapa longa: anima cada cena com o Wan 2.2 e monta o Short. Retoma de onde parou.

    scene_numbers descarta e refaz a animação só dessas cenas (as demais são reaproveitadas).
    """
    output_dir = motion_output_dir(influencer_name, batch_id, video_slug)
    script_path = output_dir / "motion-script.json"
    if not script_path.is_file():
        raise FileNotFoundError(f"Prepare as imagens-chave antes de animar: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    scenes = validate_motion_script(script)
    fmt = of_script(script)
    profile = perfis.of_script(script)
    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")
    narrations = [output_dir / f"scene-{index:02d}" / "narration.wav" for index in range(1, len(scenes) + 1)]
    if all(path.is_file() for path in narrations):
        check_duration(script, sum(_audio_duration(ffprobe, path) for path in narrations))
    for number in scene_numbers or []:
        if not 1 <= number <= len(scenes):
            raise ValueError(f"Informe números de cena entre 1 e {len(scenes)}.")
        _invalidate_animation(output_dir / f"scene-{number:02d}")
    avatar = GENERATED_ROOT / _slug(influencer_name) / "avatar.png"
    ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
    (output_dir / "short.mp4").unlink(missing_ok=True)

    report: list[dict[str, Any]] = []
    segments: list[Path] = []
    # Trabalho de CPU em paralelo com a GPU (pedido do usuário, 2026-10-09: a GPU parava a cada clipe e a cada cena):
    # os quadros de cada clipe descem e cada cena é montada enquanto a GPU já anima o clipe ou a cena seguinte.
    lock = threading.Lock()

    def report_progress(message: str) -> None:
        if progress:
            with lock:  # o job grava o progresso num arquivo; as threads não podem escrever ao mesmo tempo
                progress(message)

    def compose(index: int, scene: dict[str, Any], scene_dir: Path, frames: list[Path], downloads: list[Future]) -> None:
        for download in downloads:
            download.result()  # os quadros da cena precisam estar no disco
        # Sem legenda gravada no vídeo (decisão de 2026-10-01); BURN_CAPTIONS=1 volta a gravá-la.
        caption = scene["caption"] if os.environ.get("BURN_CAPTIONS") == "1" else ""
        # Sem avatar no canto (decisão do usuário, 2026-10-02); roteiros antigos com "cornerAvatar": true o mantêm.
        corner = avatar if script.get("cornerAvatar") else None
        overlay = caption_overlay(caption, corner, scene_dir / "overlay.png", fmt=fmt)
        compose_scene(frames, scene_dir / "narration.wav", overlay, scene_dir / "segment.mp4", ffmpeg, fmt=fmt)
        report_progress(f"cena {index}/{len(scenes)} montada")

    downloads: list[Future] = []
    compositions: list[Future] = []
    with ThreadPoolExecutor(max_workers=3) as background:
        try:
            for index, scene in enumerate(scenes, 1):
                scene_dir = output_dir / f"scene-{index:02d}"
                keyframe, narration = scene_dir / "keyframe.png", scene_dir / "narration.wav"
                if not keyframe.is_file() or not narration.is_file():
                    raise FileNotFoundError(f"A cena {index} está sem imagem-chave ou narração; prepare as imagens-chave.")
                duration = _audio_duration(ffprobe, narration)
                segment = scene_dir / "segment.mp4"
                pending: list[Future] = []
                frames = animate_scene(
                    {**scene, "outfit": JIUJITSU_OUTFIT if is_jiujitsu(script) else script.get("outfit")}, keyframe,
                    duration, scene_dir, seed=int(scene.get("seed") or 0) + 1000 * index, progress=report_progress,
                    fmt=fmt, profile=profile, yield_gpu=yield_gpu, background=background, pending=pending,
                )
                downloads.extend(pending)
                if not segment.is_file():
                    compositions.append(background.submit(compose, index, scene, scene_dir, frames, pending))
                segments.append(segment)
                report.append({"scene": index, "seconds": round(duration, 2), "frames": len(frames)})
            for download in downloads:
                download.result()  # quadros todos no disco: a GPU não é mais necessária
            if gpu_done:
                gpu_done()  # montagem e FR/EN são CPU: a GPU (local ou remota paga por hora) já fica para o próximo
            for composition in compositions:
                composition.result()
        except BaseException:
            for future in downloads + compositions:
                future.cancel()
            raise

    video, total, review, exported = _join_segments(output_dir, script, segments, report, ffmpeg, ffprobe)
    # Versão em francês (mesmo vídeo, narração trocada) quando a tradução já foi salva.
    # Uma falha aqui não invalida o vídeo em português, que já está pronto.
    try:
        localized = localize_if_ready(output_dir, progress=progress)
        localized_error = None
    except Exception as error:  # noqa: BLE001
        localized, localized_error = None, f"{type(error).__name__}: {error}"
    return {
        "videoPath": str(video),
        "durationSeconds": round(total, 1),
        "resolution": "{}x{}".format(*FORMATS[fmt]["frame"]),
        "format": fmt,
        "reviewSheetPath": str(review),
        "outputPath": str(exported) if exported else None,
        # Só os idiomas ativos em "- idiomas:" aparecem aqui; com "pt" o dicionário fica vazio.
        "localizedOutputPaths": {language: item.get("outputPath") for language, item in (localized or {}).items()},
        "localizedError": localized_error,
        "scenes": report,
    }
