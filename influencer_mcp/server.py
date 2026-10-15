"""Servidor MCP local. Sem pesquisa de tendências desde 2026-10-14 (pedido do usuário): os temas saem do conhecimento do
Claude do lote e da lista local de assuntos (research_topic_history); nada consulta YouTube, Google ou Analytics."""
from __future__ import annotations

import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import motion, motion_jobs
from .localize import localize_if_ready, save_translation
from .media import create_avatar, render_short, rerender_scenes, save_publish_metadata, save_script_seo
from .motion import motion_output_dir

server = FastMCP("influencer-local")


@server.tool()
def describe_format_limits() -> str:
    """Limites oficiais por formato (short e video): duração em segundos, número de cenas e palavras de narração totais
    por voz. É a única fonte desses números; o sistema recusa o roteiro que fugir deles."""
    from .motion import format_limits

    return json.dumps(format_limits(), ensure_ascii=False, indent=1)


@server.tool()
def describe_movements() -> str:
    """Catálogo de movimentos escritos a partir de manuais (NSCA, CrossFit, cinesiologia): id, nome, pontos de execução,
    cinesiologia e cuidados. O roteirista usa o id em "movement" nas cenas de exercício."""
    from .movimentos import catalog

    return json.dumps(catalog(), ensure_ascii=False, indent=1)


@server.tool()
def research_topic_history() -> str:
    """Todos os problemas do dia a dia que o canal já tratou (assuntos.md: canal inteiro + em produção). NUNCA proponha
    um problema da lista, nem com outra solução ou outras palavras: o sistema recusa o roteiro antes de gerar imagens."""
    from .historico import history

    rows = history()
    return json.dumps({"total": len(rows), "problemas": [row["problem"] for row in rows]}, ensure_ascii=False, indent=1)


@server.tool()
def create_influencer_avatar(
    influencer_name: str,
    persona: str,
    visual_brief: str,
    overwrite: bool = False,
) -> str:
    """Preserva o avatar existente ou gera um avatar quadrado via ComfyUI local."""
    if not influencer_name.strip() or len(influencer_name) > 80:
        raise ValueError("influencer_name deve ter entre 1 e 80 caracteres.")
    if not 10 <= len(persona.strip()) <= 600 or not 10 <= len(visual_brief.strip()) <= 1000:
        raise ValueError("persona deve ter 10-600 caracteres e visual_brief, 10-1.000 caracteres.")
    output_path = create_avatar(
        influencer_name.strip(),
        persona.strip(),
        visual_brief.strip(),
        overwrite=overwrite,
    )
    return f"Avatar pronto (gerado ou existente mantido): {output_path}"


@server.tool()
def render_youtube_short(
    influencer_name: str,
    batch_id: str,
    script_json: str,
) -> str:
    """Recebe o roteiro JSON completo, salva-o e gera um Short vertical de até 3 minutos (meta de ~2min30)."""
    if len(script_json) > 20_000:
        raise ValueError("O roteiro excede o limite de 20.000 caracteres.")
    try:
        script = json.loads(script_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"script_json não contém JSON válido: {error}") from error
    if not isinstance(script, dict):
        raise ValueError("script_json deve ser um objeto JSON de roteiro.")
    result = render_short(
        influencer_name=influencer_name,
        batch_id=batch_id,
        script=script,
    )
    result["scriptPath"] = str(Path(str(result["assetsDirectory"])) / "script.json")
    return json.dumps(result, ensure_ascii=False, indent=2)


@server.tool()
def rerender_short_scenes(
    influencer_name: str,
    batch_id: str,
    video_slug: str,
    scene_numbers: list[int],
) -> str:
    """Gera novamente só as cenas reprovadas na revisão visual e remonta o Short, reaproveitando a narração."""
    result = rerender_scenes(
        influencer_name=influencer_name,
        batch_id=batch_id,
        video_slug=video_slug,
        scene_numbers=scene_numbers,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


def _parse_script(script_json: str) -> dict:
    if len(script_json) > 40_000:
        raise ValueError("O roteiro excede o limite de 40.000 caracteres.")
    try:
        script = json.loads(script_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"script_json não contém JSON válido: {error}") from error
    if not isinstance(script, dict):
        raise ValueError("script_json deve ser um objeto JSON de roteiro.")
    return script


@server.tool()
def start_motion_keyframes(
    influencer_name: str,
    batch_id: str,
    script_json: str,
    scene_numbers: list[int] | None = None,
) -> str:
    """Vídeo com movimento, etapa 1: narra AGORA (~1 min, na CPU) e confere a duração; roteiro longo ou curto
    demais volta com erro na hora, dizendo quantas palavras tirar ou pôr, e nada entra na fila: corrija e envie de
    novo. Aceito, a imagem-chave de cada cena é gerada em segundo plano (~10 min). Cada cena precisa de narration, caption, keyframePrompt (inglês)
    e motions (1 a 3 descrições de movimento em inglês); pose é opcional. Vídeo novo precisa do objeto "seo" no
    roteiro (title, description, hashtags, tags, pinnedComment, regras em seo.md): o sistema salva os metadados ao
    aceitar o roteiro. Para refazer imagens-chave, envie o roteiro (com prompts ajustados, se quiser) e os números
    das cenas em scene_numbers. Consulte com motion_job_status e revise keyframeSheetPath antes de animar."""
    script = _parse_script(script_json)
    motion.validate_motion_script(script)
    new = not (motion.motion_output_dir(influencer_name, batch_id, script["slug"]) / "motion-script.json").is_file()
    if new:
        # Vídeo novo: tema repetido, pausa de exercícios e número de cenas recusados na hora, sem esperar a fila.
        motion.check_new_video(script, batch_id)
    params = {"influencer_name": influencer_name, "batch_id": batch_id, "script": script,
              "scene_numbers": scene_numbers}
    # Duração medida na narração real antes de entrar na fila (2026-10-12): medida só dentro do job, a falha chegava
    # depois que o lote já tinha seguido em frente, e 21 vídeos ficaram parados sem correção.
    try:
        # SEO dentro do roteiro (2026-10-14, menos IA): salvo pelo código antes da narração, em vez de um segundo
        # Claude por vídeo. Obrigatório em vídeo novo; em reenvio, só se vier.
        if new or script.get("seo") is not None:
            save_script_seo(influencer_name, batch_id, script)
        motion_jobs.narrate_on_cpu(params)
    except ValueError:
        if new:
            from .historico import unregister_subject

            unregister_subject(batch_id, script["slug"])  # recusado: o assunto não fica preso a um vídeo que não existe
        raise
    job = motion_jobs.start_job("keyframes", params)
    return json.dumps({"jobId": job["id"], "state": job["state"]}, ensure_ascii=False)


@server.tool()
def start_motion_animation(
    influencer_name: str,
    batch_id: str,
    video_slug: str,
    scene_numbers: list[int] | None = None,
) -> str:
    """Vídeo com movimento, etapa 2 (4-5 horas por vídeo, em segundo plano, um job por vez na GPU): anima as
    imagens-chave aprovadas com o Wan 2.2 e monta o Short. Retoma de onde parou se for reiniciado. Para refazer
    só algumas cenas depois da revisão, informe scene_numbers. Ao terminar, revise reviewSheetPath."""
    job = motion_jobs.start_job("animation", {
        "influencer_name": influencer_name, "batch_id": batch_id, "video_slug": video_slug,
        "scene_numbers": scene_numbers,
    })
    return json.dumps({"jobId": job["id"], "state": job["state"]}, ensure_ascii=False)


@server.tool()
def motion_job_status(job_id: str = "", wait_seconds: int = 0) -> str:
    """Estado de um job de vídeo com movimento (queued, running, done, failed), progresso e resultado.
    Com wait_seconds (até 600), espera o job terminar antes de responder. Sem job_id, lista todos os jobs."""
    if job_id:
        deadline = time.monotonic() + max(0, min(wait_seconds, 600))
        job = motion_jobs.job_status(job_id)
        while job["state"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(10)
            job = motion_jobs.job_status(job_id)
        return json.dumps(job, ensure_ascii=False, indent=2)
    summary = [
        {key: job.get(key) for key in ("id", "kind", "state", "createdAt", "finishedAt")}
        | {"slug": job["params"].get("video_slug") or job["params"].get("script", {}).get("slug"),
           "lastProgress": (job["progress"] or [None])[-1]}
        for job in motion_jobs.list_jobs()
    ]
    return json.dumps(summary, ensure_ascii=False, indent=2)


@server.tool()
def save_youtube_metadata(
    influencer_name: str,
    video_slug: str,
    batch_id: str,
    title: str,
    description: str,
    hashtags: list[str],
    tags: list[str],
    pinned_comment: str,
) -> str:
    """Salva título, descrição, hashtags, tags e comentário sugerido junto ao vídeo para publicação manual."""
    output_path = save_publish_metadata(
        influencer_name=influencer_name,
        video_slug=video_slug,
        batch_id=batch_id,
        title=title,
        description=description,
        hashtags=hashtags,
        tags=tags,
        pinned_comment=pinned_comment,
    )
    return f"Pacote de metadados salvo: {output_path}"


def _save_version(
    language: str, label: str, influencer_name: str, video_slug: str, batch_id: str, title: str,
    description: str, hashtags: list[str], tags: list[str], pinned_comment: str, scenes: list[dict],
    thumbnail_text: str | None = None,
) -> str:
    video_dir = motion_output_dir(influencer_name, batch_id, video_slug)
    translation = {
        "language": language, "title": title, "description": description, "hashtags": hashtags,
        "tags": tags, "pinned_comment": pinned_comment, "scenes": scenes,
    }
    if thumbnail_text:
        translation["thumbnail_text"] = thumbnail_text
    saved = save_translation(video_dir, translation)
    built = localize_if_ready(video_dir, languages=[language])
    if built:
        return f"Tradução salva em {saved}. Versão {label} pronta: {built[language]['outputPath']}"
    return f"Tradução salva em {saved}. A versão {label} será montada quando a animação terminar."


@server.tool()
def save_french_version(
    influencer_name: str,
    video_slug: str,
    batch_id: str,
    title: str,
    description: str,
    hashtags: list[str],
    tags: list[str],
    pinned_comment: str,
    scenes: list[dict],
    thumbnail_text: str | None = None,
) -> str:
    """Salva a tradução para francês (França) de um vídeo: metadados e narração de cada cena.

    scenes: [{"scene": número da cena no roteiro, "narration": texto em francês}], uma por cena do vídeo.
    O vídeo é o mesmo; só a voz muda. Se o short.mp4 já existir, a versão _FRA é montada agora
    (alguns minutos); senão, é montada automaticamente quando a animação terminar.
    thumbnail_text: só para vídeos horizontais (formato "video"); tradução curta do thumbnailText
    do roteiro, usada na capa dessa versão.
    """
    return _save_version(
        "fr", "francesa", influencer_name, video_slug, batch_id, title, description, hashtags, tags,
        pinned_comment, scenes, thumbnail_text,
    )


@server.tool()
def save_english_version(
    influencer_name: str,
    video_slug: str,
    batch_id: str,
    title: str,
    description: str,
    hashtags: list[str],
    tags: list[str],
    pinned_comment: str,
    scenes: list[dict],
    thumbnail_text: str | None = None,
) -> str:
    """Salva a tradução para inglês americano de um vídeo: metadados e narração de cada cena.

    scenes: [{"scene": número da cena no roteiro, "narration": texto em inglês}], uma por cena do vídeo.
    O vídeo é o mesmo; só a voz muda. Se o short.mp4 já existir, a versão _ENG é montada agora
    (alguns minutos); senão, é montada automaticamente quando a animação terminar.
    thumbnail_text: só para vídeos horizontais (formato "video"); tradução curta do thumbnailText
    do roteiro, usada na capa dessa versão.
    """
    return _save_version(
        "en", "em inglês", influencer_name, video_slug, batch_id, title, description, hashtags, tags,
        pinned_comment, scenes, thumbnail_text,
    )


def main() -> None:
    server.run(transport="stdio")
