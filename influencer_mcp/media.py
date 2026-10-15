from __future__ import annotations

import io
import json
import os
import random
import re
import shutil
import subprocess
import time
import uuid
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .illustration import POSES, render_openpose_guide
from .pose_validation import validate_pose_image
from .winproc import quiet

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())).resolve()
GENERATED_ROOT = PROJECT_ROOT / "generated"
# Limite de duração de um YouTube Short; a meta editorial é ~150 s.
MAX_VIDEO_SECONDS = 180
MIN_SCENES = 3
MAX_SCENES = 14
# Velocidade medida da narração: Kokoro pm_santa ~2,9 palavras/s (Piper faber ~3,3).
NARRATION_WORDS_PER_SECOND = 2.9
MAX_POSE_GENERATION_ATTEMPTS = 3
# Assinatura fixa de toda descrição, acrescentada pelo sistema no FIM (a primeira linha fica com a palavra-chave).
# Placeholder do projeto Historiador (2026-10-15): troque pelo texto do canal quando ele existir.
DESCRIPTION_INTRO = (
    "Canal Historiador: um episódio da história por dia, contado em um minuto.\n"
    "Inscreva-se para não perder o próximo."
)
MAX_TITLE_LENGTH = 65
FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920


def _slug(value: str) -> str:
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    if not slug:
        raise ValueError("O nome deve conter ao menos uma letra ou número.")
    return slug[:80]


def _comfyui_url(*, require_avatar_checkpoint: bool = True) -> str:
    base = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("COMFYUI_URL deve apontar para um servidor HTTP local (localhost).")
    if require_avatar_checkpoint and not os.environ.get("COMFYUI_CHECKPOINT"):
        raise RuntimeError("Configure COMFYUI_CHECKPOINT com o nome do checkpoint instalado no ComfyUI.")
    return base


def _request_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as error:
        raise RuntimeError(f"Falha ao comunicar com o ComfyUI em {url}: {error}") from error


def _run_checked(
    command: list[str],
    *,
    timeout: int,
    input_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            input=input_text,
            # Sem texto de entrada, desliga o stdin: dentro do servidor MCP ele é o canal JSON-RPC, e o ffmpeg
            # fica preso lendo dali (espera a tecla "q") em vez de encerrar.
            stdin=subprocess.DEVNULL if input_text is None else None,
            check=True,
            capture_output=True,
            text=True,
            # UTF-8 explícito nos dois lados: o padrão do Windows (cp1252) corrompe acentos como "ç".
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **(extra_env or {})},
            timeout=timeout,
            **quiet(),
        )
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "O programa não retornou detalhes.")[-3000:]
        raise RuntimeError(
            f"{Path(command[0]).name} falhou com código {error.returncode}: {details}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f"{Path(command[0]).name} excedeu o limite de {timeout} segundos.") from error


def narration_voice(voice: str | None = None) -> str:
    """Identifica a voz configurada; narrações feitas com outra voz são regeneradas."""
    engine = os.environ.get("TTS_ENGINE", "kokoro").strip().lower()
    if engine == "kokoro":
        return f"kokoro:{voice or os.environ.get('KOKORO_VOICE', 'pm_santa')}:{os.environ.get('KOKORO_SPEED', '1.0')}"
    return f"{engine}:{Path(os.environ.get('PIPER_MODEL_PATH', '')).name}"


def synthesize_narration(
    text: str, destination: Path, *, lang: str = "p", speed: float | None = None, voice: str | None = None,
) -> Path:
    """Gera a narração de uma cena. Padrão: Kokoro, voz pm_santa. TTS_ENGINE=piper usa o Piper.

    lang e speed valem só para a Kokoro (lang "f" = francês, "a" = inglês americano; speed padrão vem de KOKORO_SPEED).
    """
    engine = os.environ.get("TTS_ENGINE", "kokoro").strip().lower()
    if lang != "p" and engine != "kokoro":
        raise ValueError("Narração em outro idioma só está disponível com a Kokoro.")
    if engine == "kokoro":
        python = _get_executable("KOKORO_PYTHON", r"D:\Influencer\tools\tts\venv\Scripts\python.exe")
        command = [
            python, str(PROJECT_ROOT / "scripts" / "kokoro_tts.py"),
            "--voice", voice or os.environ.get("KOKORO_VOICE", "pm_santa"),
            "--speed", str(speed) if speed is not None else os.environ.get("KOKORO_SPEED", "1.0"),
            "--lang", lang,
            "--output_file", str(destination),
        ]
    elif engine == "piper":
        model = os.environ.get("PIPER_MODEL_PATH", "").strip()
        if not model or not Path(model).is_file():
            raise RuntimeError("Configure PIPER_MODEL_PATH com o caminho do modelo de voz Piper instalado.")
        command = [_get_executable("PIPER_EXECUTABLE", "piper"), "--model", model, "--output_file", str(destination)]
    else:
        raise ValueError(f"TTS_ENGINE desconhecido: {engine!r} (use kokoro ou piper).")
    # Kokoro sempre na CPU e com 4 núcleos (2026-10-10): na GPU disputava a memória com o ComfyUI, que mantém os modelos
    # carregados ("cuDNN ... ALLOCATION_FAILED"), e vários lotes narrando juntos, cada um usando todos os núcleos,
    # estouravam os 300 s. A Kokoro 82M é rápida na CPU; a narração antecipada da fila já rodava assim.
    cpu_only = {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"} if engine == "kokoro" else None
    _run_checked(command, input_text=text, timeout=300, extra_env=cpu_only)
    return destination


# 5 hashtags por vídeo e por idioma (decisão do usuário, 2026-10-04, após pesquisa: listas longas parecem spam e
# só as 3 primeiras aparecem acima do título). Antes eram 20. O YouTube ignora TODAS acima de 60.
MAX_HASHTAGS = 5


_TOOL_TAGS = re.compile(r"</?(?:description|invoke|parameter|antml:[a-z_]+)[^>]*>", re.IGNORECASE)


def clean_youtube_text(text: str, field: str) -> str:
    """Tira restos de chamada de ferramenta que vazaram no texto e recusa < ou >, que o YouTube rejeita
    (invalidDescription em 2026-10-10: '</description></invoke>' foi parar na descrição)."""
    text = _TOOL_TAGS.sub("", text).strip()
    if "<" in text or ">" in text:
        raise ValueError(f"{field} não pode conter os caracteres < ou > (o YouTube rejeita).")
    return text


def clean_hashtags(hashtags: list[str]) -> list[str]:
    """Valida e devolve as hashtags no formato "#tag" (sem espaços, sem repetição, no máximo MAX_HASHTAGS)."""
    clean = [tag.strip().removeprefix("#").strip() for tag in hashtags]
    if any(not tag or len(tag) > 50 for tag in clean):
        raise ValueError("Cada hashtag deve conter entre 1 e 50 caracteres.")
    if any(any(char.isspace() or char == "#" for char in tag) for tag in clean):
        raise ValueError("Hashtag não pode ter espaço nem # no meio (o YouTube corta a hashtag no espaço).")
    if len({tag.casefold() for tag in clean}) != len(clean):
        raise ValueError("Há hashtags repetidas.")
    if len(clean) > MAX_HASHTAGS:
        raise ValueError(f"Use no máximo {MAX_HASHTAGS} hashtags.")
    return [f"#{tag}" for tag in clean]


def _comfy_workflow(prompt: str, *, square: bool) -> dict[str, Any]:
    width, height = (768, 768) if square else (576, 1024)
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": os.environ["COMFYUI_CHECKPOINT"]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": (
                    "High-quality colorful hand-drawn comic-book illustration, polished editorial "
                    "cartoon artwork, crisp expressive ink outlines, rich saturated colors, "
                    "clean cel shading, clear visual storytelling, fully clothed adult characters "
                    "in modest opaque clothing, family-friendly educational content. "
                    + prompt
                ),
                "clip": ["1", 1],
            },
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": (
                    "photo, photorealistic, nude, nudity, naked, erotic, sexual content, "
                    "lingerie, underwear, explicit, suggestive, gore, blood, graphic violence, "
                    "text, watermark, logo, multiple people, duplicate person, extra head, extra "
                    "face, extra torso, extra arms, extra legs, duplicated limbs, fused limbs, "
                    "extra fingers, deformed hands, malformed body, incorrect anatomy, blurry, "
                    "low quality"
                ),
                "clip": ["1", 1],
            },
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": random.SystemRandom().randrange(0, 2**32),
                "steps": 20,
                "cfg": 6.5,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "influencer", "images": ["6", 0]}},
    }


def _controlled_scene_workflow(
    *,
    prompt: str,
    pose_name: str,
    pose_image: str,
    seed: int,
) -> dict[str, Any]:
    checkpoint = os.environ.get("COMFYUI_SCENE_CHECKPOINT") or "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
    controlnet = os.environ.get("COMFYUI_OPENPOSE_CONTROLNET") or "xinsir-openpose-sdxl.safetensors"
    scene_lora = os.environ.get("COMFYUI_SCENE_LORA", "").strip()
    lora_strength = float(os.environ.get("COMFYUI_SCENE_LORA_STRENGTH", "0.6"))
    if not 0 <= lora_strength <= 1:
        raise ValueError("COMFYUI_SCENE_LORA_STRENGTH precisa estar entre 0 e 1.")
    positive = (
        "One fully dressed adult man (wearing an opaque navy short-sleeved crew-neck T-shirt:1.4) covering chest, "
        "charcoal sweatpants, sports shoes. (Detailed face looking straight ahead:1.3), head level. "
        f"{prompt.strip()}. "
        + ("Pencil Art. " if scene_lora else "")
        + "Average natural build; (hand-drawn graphite pencil sketch on paper:1.4), cross-hatching, muted color; "
        "full body, hands and shoes visible, plain white background."
    )
    negative = (
        "nudity, naked, topless, (shirtless:1.7), (bare chest:1.7), (bare torso:1.7), tank top, "
        "sleeveless shirt, sleeveless vest, exposed chest, bare abdomen, headless, missing head, "
        "missing face, blank face, faceless, featureless face, (hands in pockets:1.5), (looking up:1.4), head tilted back, chin up, "
        "detached head, duplicate person, jumping, floating, photograph, photorealistic, "
        "extra limbs, malformed anatomy, "
        "distorted body, oversized head, exaggerated proportions, cartoon, comic, cropped body, text, watermark"
    )
    horizontal_pose = pose_name in {"plank", "push_up"}
    width, height = (1344, 768) if horizontal_pose else (768, 1344)
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": controlnet},
        },
        "5": {
            "class_type": "LoadImage",
            "inputs": {"image": pose_image},
        },
        "6": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["2", 0],
                "negative": ["3", 0],
                "control_net": ["4", 0],
                "image": ["5", 0],
                "strength": 1.0,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
        },
        "7": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 35,
                "cfg": 5.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["6", 0],
                "negative": ["6", 1],
                "latent_image": ["7", 0],
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "influencer-comic", "images": ["9", 0]}},
    }
    if scene_lora:
        workflow["11"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": scene_lora,
                "strength_model": lora_strength,
                "strength_clip": lora_strength,
            },
        }
        workflow["2"]["inputs"]["clip"] = ["11", 1]
        workflow["3"]["inputs"]["clip"] = ["11", 1]
        workflow["8"]["inputs"]["model"] = ["11", 0]
    return workflow


def _upload_comfy_image(base: str, image_path: Path, content_type: str = "image/png") -> str:
    boundary = uuid.uuid4().hex
    image_data = image_path.read_bytes()
    body = b"".join((
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        image_data,
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput".encode(),
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue".encode(),
        f"\r\n--{boundary}--\r\n".encode(),
    ))
    request = urllib.request.Request(
        f"{base}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
    except Exception as error:
        raise RuntimeError(f"Falha ao enviar o guia OpenPose ao ComfyUI: {error}") from error
    name = result.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("ComfyUI não retornou o nome do guia OpenPose enviado.")
    return name


def _generate_image(prompt: str, destination: Path, *, square: bool) -> None:
    base = _comfyui_url()
    result = _request_json(f"{base}/prompt", {"prompt": _comfy_workflow(prompt, square=square)}, timeout=30)
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str):
        raise RuntimeError("ComfyUI não retornou o identificador esperado para o trabalho de imagem.")

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        history = _request_json(f"{base}/history/{urllib.parse.quote(prompt_id)}", timeout=20)
        record = history.get(prompt_id, {})
        outputs = record.get("outputs", {})
        images = [
            item
            for node in outputs.values()
            for item in node.get("images", [])
            if isinstance(item, dict) and item.get("filename")
        ]
        if images:
            image = images[0]
            query = urllib.parse.urlencode({
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            })
            request = urllib.request.Request(f"{base}/view?{query}")
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read(25_000_001)
            if len(content) > 25_000_000:
                raise RuntimeError("A imagem gerada excede o limite de tamanho permitido.")
            destination.write_bytes(content)
            return
        if record.get("status", {}).get("status_str") == "error":
            messages = record.get("status", {}).get("messages", [])
            details = "; ".join(
                str(message[1])
                for message in messages
                if isinstance(message, list) and len(message) > 1
            )
            raise RuntimeError(
                "O ComfyUI informou um erro ao gerar a imagem."
                + (f" Detalhes: {details[:2000]}" if details else "")
            )
        time.sleep(2)
    raise TimeoutError("O ComfyUI não concluiu a geração da imagem em 10 minutos.")


def _generate_controlled_scene(prompt: str, pose_name: str, destination: Path) -> None:
    base = _comfyui_url(require_avatar_checkpoint=False)
    guide_path = destination.with_name(f"{destination.stem}-pose.png")
    render_openpose_guide(pose_name=pose_name, destination=guide_path)
    uploaded_name = _upload_comfy_image(base, guide_path)
    workflow = _controlled_scene_workflow(
        prompt=prompt,
        pose_name=pose_name,
        pose_image=uploaded_name,
        seed=random.SystemRandom().randrange(0, 2**32),
    )
    result = _request_json(f"{base}/prompt", {"prompt": workflow}, timeout=30)
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str):
        raise RuntimeError("ComfyUI não retornou o identificador esperado para a cena controlada.")

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        history = _request_json(f"{base}/history/{urllib.parse.quote(prompt_id)}", timeout=20)
        record = history.get(prompt_id, {})
        outputs = record.get("outputs", {})
        images = [
            item
            for node in outputs.values()
            for item in node.get("images", [])
            if isinstance(item, dict) and item.get("filename")
        ]
        if images:
            image = images[0]
            query = urllib.parse.urlencode({
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            })
            request = urllib.request.Request(f"{base}/view?{query}")
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    content = response.read(25_000_001)
            except Exception as error:
                raise RuntimeError(f"Falha ao obter a imagem gerada pelo ComfyUI: {error}") from error
            if len(content) > 25_000_000:
                raise RuntimeError("A imagem gerada excede o limite de tamanho permitido.")
            try:
                with Image.open(io.BytesIO(content)) as generated:
                    final = generated.convert("RGB")
            except Exception as error:
                raise RuntimeError(f"ComfyUI retornou uma imagem inválida para a pose {pose_name!r}.") from error
            destination.parent.mkdir(parents=True, exist_ok=True)
            final.save(destination, format="PNG", optimize=True)
            return
        if record.get("status", {}).get("status_str") == "error":
            messages = record.get("status", {}).get("messages", [])
            details = "; ".join(
                str(message[1])
                for message in messages
                if isinstance(message, list) and len(message) > 1
            )
            raise RuntimeError(
                "O ComfyUI falhou ao gerar a cena controlada."
                + (f" Detalhes: {details[:2000]}" if details else "")
            )
        time.sleep(2)
    raise TimeoutError("O ComfyUI não concluiu a cena controlada em 10 minutos.")


def check_influencer(name: str) -> str:
    """O canal é um só (Historiador). Lotes confundiram o canal com o personagem e gravaram vídeos em
    generated/aquarela-mulher e generated/homem-60 (2026-10-04): agora o nome errado é recusado com instrução."""
    expected = os.environ.get("INFLUENCER_NAME", "Historiador")
    if _slug(name or "") != _slug(expected):
        raise ValueError(
            f"influencer_name deve ser exatamente {expected!r} (o canal), não {name!r}. O personagem do vídeo "
            "(Mulher 50+ / Homem 50+) vai no campo \"visualProfile\" do roteiro (ids em personagens.md)."
        )
    return expected


def create_avatar(influencer_name: str, persona: str, visual_brief: str, *, overwrite: bool = False) -> Path:
    check_influencer(influencer_name)
    output_dir = GENERATED_ROOT / _slug(influencer_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "avatar.png"
    if destination.is_file() and not overwrite:
        return destination
    prompt = (
        f"Original friendly digital fitness educator avatar, {persona}. "
        f"Appearance and visual direction: {visual_brief}. "
        "Professional editorial portrait, centered head and shoulders, warm approachable expression, "
        "clean distinct background, square profile image, no text, no lettering, no logos."
    )
    _generate_image(prompt, destination, square=True)
    return destination


def _get_executable(env_name: str, default: str) -> str:
    configured = (os.environ.get(env_name) or default).strip()
    path = Path(configured)
    if path.is_file():
        return str(path)
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    raise RuntimeError(f"Não foi encontrado {configured}. Configure {env_name} ou instale o programa.")


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _captioned_image(source: Path, destination: Path, caption: str, avatar_path: Path) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    image = ImageOps.contain(image, (FRAME_WIDTH, FRAME_HEIGHT))
    canvas = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), "white")
    canvas.paste(image, ((FRAME_WIDTH - image.width) // 2, (FRAME_HEIGHT - image.height) // 2))
    image = canvas
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = Path(os.environ.get("CAPTION_FONT", r"C:\Windows\Fonts\arial.ttf"))
    if font_path.is_file():
        font = ImageFont.truetype(str(font_path), 52)
    else:
        font = ImageFont.load_default()
    lines = _wrap_text(draw, caption, font, FRAME_WIDTH - 120)[:4]
    line_height = 68
    panel_height = max(130, len(lines) * line_height + 64)
    top = FRAME_HEIGHT - panel_height - 110
    draw.rounded_rectangle(
        (45, top, FRAME_WIDTH - 45, FRAME_HEIGHT - 70),
        radius=28,
        fill=(0, 0, 0, 178),
    )
    y = top + 32
    for line in lines:
        draw.text((FRAME_WIDTH / 2, y), line, font=font, fill="white", anchor="mt")
        y += line_height
    if avatar_path.is_file():
        with Image.open(avatar_path) as opened_avatar:
            avatar = ImageOps.fit(opened_avatar.convert("RGBA"), (176, 176))
        mask = Image.new("L", avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 175, 175), fill=255)
        avatar.putalpha(mask)
        overlay.alpha_composite(avatar, (FRAME_WIDTH - 216, 56))
    Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB").save(destination, quality=94)


STALE_RENDER_PATTERNS = (
    "scene-*.png",
    "scene-*.wav",
    "segment-*.mp4",
    "segments.txt",
    "short.mp4",
    "pose-validation.json",
    "contact-sheet.png",
    "youtube-metadata.json",
)


def _remove_stale_render_files(output_dir: Path) -> None:
    for pattern in STALE_RENDER_PATTERNS:
        for stale in output_dir.glob(pattern):
            if stale.is_file():
                stale.unlink()


def _contact_sheet(
    title: str, scenes: list[dict[str, Any]], images: list[Path], destination: Path, *, landscape: bool = False,
) -> None:
    thumb_width, thumb_height = (640, 360) if landscape else (360, 640)
    gap, header, footer = 24, 110, 190
    columns = min(len(images), 4 if landscape else 6)
    rows = -(-len(images) // columns)
    row_height = thumb_height + footer
    width = gap + columns * (thumb_width + gap)
    sheet = Image.new("RGB", (width, header + rows * row_height), "white")
    draw = ImageDraw.Draw(sheet)
    font_path = Path(os.environ.get("CAPTION_FONT", r"C:\Windows\Fonts\arial.ttf"))
    if font_path.is_file():
        title_font = ImageFont.truetype(str(font_path), 40)
        text_font = ImageFont.truetype(str(font_path), 22)
    else:
        title_font = text_font = ImageFont.load_default()
    draw.text((gap, 34), title, font=title_font, fill=(20, 20, 20))
    for index, (scene, image_path) in enumerate(zip(scenes, images)):
        left = gap + (index % columns) * (thumb_width + gap)
        top = header + (index // columns) * row_height
        with Image.open(image_path) as opened:
            thumb = ImageOps.contain(opened.convert("RGB"), (thumb_width, thumb_height))
        sheet.paste(thumb, (left + (thumb_width - thumb.width) // 2, top + (thumb_height - thumb.height) // 2))
        draw.rectangle((left, top, left + thumb_width, top + thumb_height), outline=(180, 180, 180))
        label = f"{index + 1}. [{scene['pose']}] {scene.get('caption', '').strip() or scene['narration']}"
        y = top + thumb_height + 14
        for line in _wrap_text(draw, label, text_font, thumb_width)[:6]:
            draw.text((left, y), line, font=text_font, fill=(40, 40, 40))
            y += 28
    sheet.save(destination)


def _audio_duration(ffprobe: str, audio_path: Path) -> float:
    result = _run_checked(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        timeout=30,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(f"Não foi possível determinar a duração de {audio_path.name}.") from error


def render_short(
    *,
    influencer_name: str,
    batch_id: str,
    script: dict[str, Any],
) -> dict[str, object]:
    video_slug = script.get("slug")
    scenes = script.get("scenes")
    if not isinstance(video_slug, str) or not video_slug.strip():
        raise ValueError("O roteiro precisa ter um slug válido.")
    if not isinstance(scenes, list) or any(
        not isinstance(scene, dict)
        or any(not isinstance(scene.get(field), str) for field in ("narration", "visualPrompt", "pose"))
        for scene in scenes
    ):
        raise ValueError("Cada cena precisa conter narration, visualPrompt e uma pose ilustrável.")
    if not MIN_SCENES <= len(scenes) <= MAX_SCENES:
        raise ValueError(f"Cada Short precisa de {MIN_SCENES} a {MAX_SCENES} cenas ilustradas.")
    if not influencer_name.strip() or len(influencer_name) > 80:
        raise ValueError("influencer_name deve ter entre 1 e 80 caracteres.")
    if not video_slug.strip() or not batch_id.strip():
        raise ValueError("video_slug e batch_id não podem ficar vazios.")
    for index, scene in enumerate(scenes, 1):
        if not scene.get("narration", "").strip() or not scene.get("visualPrompt", "").strip():
            raise ValueError(f"A cena {index} precisa de narration e visualPrompt.")
        if scene["pose"] not in POSES:
            raise ValueError(f"A pose da cena {index} não é compatível com o guia OpenPose: {scene['pose']!r}.")
        if len(scene["narration"]) > 900 or len(scene["visualPrompt"]) > 200:
            raise ValueError(f"O texto da cena {index} excede o limite permitido.")
        caption = scene.get("caption", "")
        if not isinstance(caption, str) or len(caption) > 120:
            raise ValueError(f"A legenda da cena {index} precisa ser texto de até 120 caracteres.")

    estimated_seconds = sum(len(scene["narration"].split()) / NARRATION_WORDS_PER_SECOND for scene in scenes)
    if estimated_seconds > MAX_VIDEO_SECONDS + 5:
        raise ValueError(
            f"A narração estimada passa de {MAX_VIDEO_SECONDS} segundos ({estimated_seconds:.0f}s). "
            "Encurte o roteiro e tente novamente."
        )

    avatar_path = GENERATED_ROOT / _slug(influencer_name) / "avatar.png"
    if not avatar_path.is_file():
        raise FileNotFoundError(f"Gere ou selecione o avatar do canal antes dos vídeos: {avatar_path}")
    output_dir = GENERATED_ROOT / _slug(influencer_name) / _slug(batch_id) / _slug(video_slug)
    output_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_render_files(output_dir)
    (output_dir / "script.json").write_text(
        f"{json.dumps(script, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")

    pose_assessments: list[dict[str, Any]] = []
    pose_validation_path = output_dir / "pose-validation.json"
    for index, scene in enumerate(scenes, 1):
        pose_assessments.append({"scene": index, "attempts": []})
        _generate_validated_scene(scene, index, output_dir, pose_assessments, pose_validation_path)
        _captioned_image(
            output_dir / f"scene-{index}.png",
            output_dir / f"scene-{index}-captioned.png",
            scene.get("caption", "").strip() or scene["narration"],
            avatar_path,
        )
        synthesize_narration(scene["narration"], output_dir / f"scene-{index}.wav")

    return _assemble_short(output_dir, script, ffmpeg, ffprobe)


def _generate_validated_scene(
    scene: dict[str, Any],
    index: int,
    output_dir: Path,
    pose_assessments: list[dict[str, Any]],
    pose_validation_path: Path,
) -> None:
    """Gera candidatos até um passar no MediaPipe; numera tentativas após as já registradas."""
    image_path = output_dir / f"scene-{index}.png"
    scene_assessment = pose_assessments[index - 1]
    attempts: list[dict[str, Any]] = scene_assessment.setdefault("attempts", [])
    first_attempt = len(attempts) + 1
    assessment = None
    for attempt_number in range(first_attempt, first_attempt + MAX_POSE_GENERATION_ATTEMPTS):
        candidate_path = output_dir / f"scene-{index}-attempt-{attempt_number}.png"
        _generate_controlled_scene(scene["visualPrompt"], scene["pose"], candidate_path)
        assessment = validate_pose_image(candidate_path, scene["pose"])
        attempts.append({
            "attempt": attempt_number,
            "image": candidate_path.name,
            **assessment.to_dict(),
        })
        if assessment.passed:
            shutil.copyfile(candidate_path, image_path)
        scene_assessment.update({
            "scene": index,
            "image": image_path.name if assessment.passed else candidate_path.name,
            "selected_image": image_path.name if assessment.passed else None,
            **assessment.to_dict(),
            "attempts": attempts,
        })
        pose_validation_path.write_text(
            f"{json.dumps(pose_assessments, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        if assessment.passed:
            break
    if assessment is None:
        raise RuntimeError(f"A cena {index} não gerou nenhuma imagem para validação.")
    if not assessment.passed:
        raise RuntimeError(
            f"A cena {index} foi rejeitada pelo filtro de pose MediaPipe após "
            f"{len(attempts) - first_attempt + 1} tentativas ({scene['pose']}): {assessment.reason}. "
            f"Revise {pose_validation_path} antes de tentar novamente."
        )


def _assemble_short(output_dir: Path, script: dict[str, Any], ffmpeg: str, ffprobe: str) -> dict[str, object]:
    scenes = script["scenes"]
    scene_assets = [
        (output_dir / f"scene-{index}-captioned.png", output_dir / f"scene-{index}.wav")
        for index in range(1, len(scenes) + 1)
    ]
    contact_sheet_path = output_dir / "contact-sheet.png"
    _contact_sheet(
        str(script.get("topic") or script["slug"]),
        scenes,
        [output_dir / f"scene-{index}.png" for index in range(1, len(scenes) + 1)],
        contact_sheet_path,
    )

    durations = [_audio_duration(ffprobe, audio) for _, audio in scene_assets]
    total_duration = sum(durations)
    if total_duration > MAX_VIDEO_SECONDS:
        raise ValueError(
            f"O áudio gerado tem {total_duration:.1f}s. Shorts devem ter no máximo "
            f"{MAX_VIDEO_SECONDS}s nesta ferramenta; encurte o roteiro e tente novamente."
        )

    segment_paths: list[Path] = []
    for index, ((image, audio), duration) in enumerate(zip(scene_assets, durations), 1):
        segment = output_dir / f"segment-{index}.mp4"
        _run_checked(
            [
                ffmpeg, "-y", "-loop", "1", "-framerate", "30", "-i", str(image),
                "-i", str(audio), "-t", f"{duration:.3f}",
                "-vf", (
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,setsar=1,"
                    "zoompan=z='min(zoom+0.0008,1.08)':d=1:s=1080x1920:fps=30"
                ),
                "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-shortest", str(segment),
            ],
            timeout=600,
        )
        segment_paths.append(segment)

    concat_manifest = output_dir / "segments.txt"
    concat_manifest.write_text(
        "".join(f"file '{segment.as_posix()}'\n" for segment in segment_paths),
        encoding="utf-8",
    )
    output_path = output_dir / "short.mp4"
    _run_checked(
        [
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_manifest),
            "-c", "copy", "-movflags", "+faststart", str(output_path),
        ],
        timeout=600,
    )
    rendered_duration = _audio_duration(ffprobe, output_path)
    if rendered_duration > MAX_VIDEO_SECONDS:
        raise ValueError(
            f"O arquivo final ficou com {rendered_duration:.1f}s, acima do limite de "
            f"{MAX_VIDEO_SECONDS}s. Encurte o roteiro e tente novamente."
        )
    return {
        "videoPath": str(output_path),
        "durationSeconds": round(rendered_duration, 1),
        "resolution": "1080x1920",
        "sceneCount": len(scenes),
        "assetsDirectory": str(output_dir),
        "poseValidationPath": str(output_dir / "pose-validation.json"),
        "contactSheetPath": str(contact_sheet_path),
        "outputPath": str(export_to_output(output_dir) or ""),
    }


def rerender_scenes(
    *,
    influencer_name: str,
    batch_id: str,
    video_slug: str,
    scene_numbers: list[int],
) -> dict[str, object]:
    """Gera novamente só as cenas indicadas, reaproveita os áudios e remonta o Short."""
    output_dir = GENERATED_ROOT / _slug(influencer_name) / _slug(batch_id) / _slug(video_slug)
    script_path = output_dir / "script.json"
    if not script_path.is_file():
        raise FileNotFoundError(f"O vídeo ainda não foi renderizado: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    scenes = script["scenes"]
    selected = sorted(set(scene_numbers))
    if not selected or any(not 1 <= number <= len(scenes) for number in selected):
        raise ValueError(f"Informe números de cena entre 1 e {len(scenes)}.")
    avatar_path = GENERATED_ROOT / _slug(influencer_name) / "avatar.png"
    if not avatar_path.is_file():
        raise FileNotFoundError(f"Gere ou selecione o avatar do canal antes dos vídeos: {avatar_path}")
    missing_audio = [
        index for index in range(1, len(scenes) + 1) if not (output_dir / f"scene-{index}.wav").is_file()
    ]
    if missing_audio:
        raise FileNotFoundError(f"Faltam áudios das cenas {missing_audio}; renderize o vídeo completo.")
    ffmpeg = _get_executable("FFMPEG_EXECUTABLE", "ffmpeg")
    ffprobe = _get_executable("FFPROBE_EXECUTABLE", "ffprobe")

    pose_validation_path = output_dir / "pose-validation.json"
    pose_assessments: list[dict[str, Any]] = (
        json.loads(pose_validation_path.read_text(encoding="utf-8")) if pose_validation_path.is_file() else []
    )
    while len(pose_assessments) < len(scenes):
        pose_assessments.append({"scene": len(pose_assessments) + 1, "attempts": []})
    for index in selected:
        previous_attempts = pose_assessments[index - 1].get("attempts") or [{}]
        rejected = previous_attempts[-1].get("image")
        pose_assessments[index - 1].setdefault("rejectedInReview", []).append(rejected)
        _generate_validated_scene(scenes[index - 1], index, output_dir, pose_assessments, pose_validation_path)
        _captioned_image(
            output_dir / f"scene-{index}.png",
            output_dir / f"scene-{index}-captioned.png",
            scenes[index - 1].get("caption", "").strip() or scenes[index - 1]["narration"],
            avatar_path,
        )
    result = _assemble_short(output_dir, script, ffmpeg, ffprobe)
    result["rerenderedScenes"] = selected
    return result


_INVALID_FILENAME = str.maketrans({'"': "'", "*": "", "?": "", "<": "", ">": "", "|": "-", "/": "-", "\\": "-"})


def _title_filename(title: str) -> str:
    """Título do vídeo como nome de arquivo válido no Windows ("A: B" vira "A - B")."""
    name = title.replace(":", " -").translate(_INVALID_FILENAME)
    name = " ".join(name.split()).strip(" .")
    return name[:150] or "video"


def export_to_output(video_dir: Path) -> Path | None:
    """Copia short.mp4 e o TXT de metadados para output/, com o título como nome.

    Só exporta quando os dois existem. Se o título mudar ou o vídeo for remontado, substitui os arquivos
    exportados anteriormente por este mesmo vídeo.
    """
    video = video_dir / "short.mp4"
    metadata_path = video_dir / "youtube-metadata.json"
    txt = video_dir / "youtube-metadata.txt"
    if not (video.is_file() and metadata_path.is_file() and txt.is_file()):
        return None
    title = json.loads(metadata_path.read_text(encoding="utf-8"))["title"]
    # output/shorts ou output/videos, conforme o formato do roteiro (padrão: short).
    from .formats import of_folder

    from .output_ok import export_files

    marker = video_dir / "output-export.json"
    base = _title_filename(title)
    previous = json.loads(marker.read_text(encoding="utf-8")).get("base") if marker.is_file() else None
    files = {".mp4": video, ".txt": txt}
    if (video_dir / "thumbnail.jpg").is_file():
        files[".jpg"] = video_dir / "thumbnail.jpg"
    # Já marcado "OK - " (mesmo arquivado): a cópia nova mantém a marca e o lugar.
    exported = export_files(of_folder(video_dir), base, previous, files)
    marker.write_text(json.dumps({"base": base}, ensure_ascii=False) + "\n", encoding="utf-8")
    return exported


def write_metadata_txt(output_dir: Path, metadata: dict[str, Any]) -> Path:
    """Versão em texto dos metadados, para copiar e colar no YouTube Studio."""
    video_ready = (output_dir / "short.mp4").is_file()
    destination = output_dir / "youtube-metadata.txt"
    destination.write_text(
        "\n".join([
            "TÍTULO",
            metadata["title"],
            "",
            "DESCRIÇÃO (já com as hashtags)",
            f"{metadata.get('descriptionForUpload') or metadata['description']}\n\n{DESCRIPTION_INTRO}",
            "",
            "TAGS (separadas por vírgula)",
            ", ".join(metadata.get("tags", [])),
            "",
            "COMENTÁRIO PARA FIXAR (sugestão)",
            metadata.get("pinnedCommentSuggestion", ""),
            "",
            "ARQUIVO DE VÍDEO",
            "short.mp4" + ("" if video_ready else " (ainda em produção)"),
            "",
            "Publicação manual no YouTube Studio.",
            "",
        ]),
        encoding="utf-8-sig",  # BOM para o Bloco de Notas abrir os acentos corretamente
    )
    return destination


def save_publish_metadata(
    *,
    influencer_name: str,
    video_slug: str,
    batch_id: str,
    title: str,
    description: str,
    hashtags: list[str],
    tags: list[str],
    pinned_comment: str,
) -> Path:
    formatted_hashtags = clean_hashtags(hashtags)
    title = clean_youtube_text(title, "O título")
    description = clean_youtube_text(description, "A descrição")
    pinned_comment = clean_youtube_text(pinned_comment, "O comentário fixado")
    clean_tags = [clean_youtube_text(tag, "Cada tag") for tag in tags]
    # 65 (não os 100 do YouTube): o celular corta por volta de 60 a 70, e 84 títulos publicados passavam de 70 (2026-10-14).
    if not title.strip() or len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"O título deve ter entre 1 e {MAX_TITLE_LENGTH} caracteres (tem {len(title)}); encurte "
                         "mantendo a palavra-chave no início.")
    if not description.strip() or len(description) > 5000:
        raise ValueError("A descrição deve ter entre 1 e 5.000 caracteres.")
    if any(not tag or len(tag) > 100 for tag in clean_tags):
        raise ValueError("Cada tag deve conter entre 1 e 100 caracteres.")
    if sum(len(tag) + 1 for tag in clean_tags) > 500:
        raise ValueError("O conjunto de tags excede o limite de 500 caracteres.")
    if len(pinned_comment.strip()) > 1000:
        raise ValueError("O comentário fixado sugerido não pode exceder 1.000 caracteres.")

    if not influencer_name.strip() or len(influencer_name) > 80:
        raise ValueError("influencer_name deve ter entre 1 e 80 caracteres.")
    if not video_slug.strip() or not batch_id.strip():
        raise ValueError("video_slug e batch_id não podem ficar vazios.")
    check_influencer(influencer_name)
    output_dir = GENERATED_ROOT / _slug(influencer_name) / _slug(batch_id) / _slug(video_slug)
    video_ready = (output_dir / "short.mp4").is_file()
    # O SEO pode ser salvo logo que o roteiro existe, mesmo com as imagens-chave ainda na fila da GPU
    # (antes a pasta só existia quando o job começava, e lotes terminavam sem SEO; 2026-10-03).
    output_dir.mkdir(parents=True, exist_ok=True)
    upload_description = description.strip()
    if formatted_hashtags:
        upload_description = f"{upload_description}\n\n{' '.join(formatted_hashtags)}"
    if len(upload_description) > 5000:
        raise ValueError("A descrição e as hashtags juntas excedem 5.000 caracteres.")
    metadata = {
        "title": title.strip(),
        "description": description.strip(),
        "descriptionForUpload": upload_description,
        "hashtags": formatted_hashtags,
        "tags": clean_tags,
        "pinnedCommentSuggestion": pinned_comment.strip(),
        "videoFile": "short.mp4",
        "videoReady": video_ready,
        "publication": "manual",
    }
    output_path = output_dir / "youtube-metadata.json"
    output_path.write_text(f"{json.dumps(metadata, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    write_metadata_txt(output_dir, metadata)
    export_to_output(output_dir)
    return output_path


def save_script_seo(influencer_name: str, batch_id: str, script: dict[str, Any]) -> Path:
    """Metadados que vêm dentro do roteiro (objeto "seo", desde 2026-10-14): o mesmo Claude que escreve o roteiro
    escreve título, descrição, hashtags, tags e comentário fixado, e o sistema salva tudo ao aceitar o roteiro, sem
    um segundo Claude por vídeo."""
    seo = script.get("seo")
    if not isinstance(seo, dict):
        raise ValueError('O roteiro precisa do objeto "seo" (title, description, hashtags, tags, pinnedComment), '
                         "seguindo seo.md. Nenhuma imagem foi gerada.")

    def text(key: str) -> str:
        value = seo.get(key)
        return value if isinstance(value, str) else ""

    def items(key: str) -> list[str]:
        value = seo.get(key)
        return [str(item) for item in value] if isinstance(value, list) else []

    return save_publish_metadata(
        influencer_name=influencer_name, video_slug=script["slug"], batch_id=batch_id, title=text("title"),
        description=text("description"), hashtags=items("hashtags"), tags=items("tags"),
        pinned_comment=text("pinnedComment") or text("pinned_comment"),
    )
