> **Projeto derivado (2026-10-15):** criado a partir do personal-influencer (canal Taumer Scanavez) para um canal de história.
> **Branch `mac-version`:** versão para macOS (Apple Silicon), com as adaptações do branch `mac-version` do personal-influencer.
> Divide com ele as ferramentas pesadas em `~/Influencer/tools` (ComfyUI, Kokoro, RIFE) e a mesma GPU: a trava `INFLUENCER_GPU_LOCK`
> (em `.mcp.json` e `abrir-painel.command`) aponta para o `gpu.lock` do personal-influencer, para os dois nunca usarem a GPU ao mesmo tempo.
> Painel na porta 8766 (o do personal-influencer usa a 8765). Segredos do YouTube (`secrets/`) são do novo canal: siga `publicacao_youtube.md`.
> O `.mcp.json` assume o projeto em `~/Documents/history-influencer` (mude com a variável `INFLUENCER_ROOT`).

# Produção local de YouTube Shorts com Claude

Esta branch (`mac-version`) roda no macOS com Apple Silicon. Toda a geração de imagens e vídeo usa a GPU do próprio Mac (ComfyUI no Metal/MPS, memória unificada).

## HOW TO

### 1. Instalar (só na primeira vez)

```bash
./install.sh
```

O instalador prepara: Python 3.11 (via `uv`), FFmpeg (Homebrew, se houver; senão um FFmpeg estático), o ambiente do servidor MCP (`.venv`), o modelo MediaPipe Pose, a narração Kokoro, o ComfyUI com os nós GGUF, RIFE e IPAdapter e cerca de 60 GB de modelos. As variáveis de ambiente ficam em `~/.influencer.env` (lido pelo app, pelo painel e pelo servidor MCP). Pode ser executado de novo quantas vezes quiser: o que já estiver pronto é mantido e downloads interrompidos continuam. `./install.sh --skip-models` pula os modelos grandes.

Requisitos: macOS em Apple Silicon (recomendado 32 GB de memória ou mais), cerca de 70 GB livres e uma conta do Claude. O `.mcp.json` assume o projeto em `~/Documents/history-influencer` (mude com `INFLUENCER_ROOT`); as ferramentas pesadas ficam em `~/Influencer/tools` (mude com `INFLUENCER_TOOLS_DIR`).

Painel de produção: `./abrir-painel.command` (http://127.0.0.1:8766, também por duplo clique no Finder).

Perfis de aceleração por placa: `perfis_gpu.json` (o perfil `apple` vale para Apple Silicon; `INFLUENCER_GPU_PROFILE=<perfil>` força um perfil).

### 2. Abrir o Claude Code

Os jobs sobem o ComfyUI sozinhos quando precisam. Para subi-lo à mão e abrir o Claude Code:

```bash
.venv/bin/python scripts/start_comfyui_detached.py
claude
```

Na primeira vez, aprove o servidor `influencer-local` quando o Claude Code pedir. Digite `/mcp` para confirmar que ele aparece como conectado.

### 3. Gerar um lote com `/gerar-lote`

```text
/gerar-lote                              → 1 vídeo, tema escolhido pelo Claude (fora da lista de assuntos)
/gerar-lote 3                            → 3 vídeos com temas distintos
/gerar-lote 2 equilíbrio e prevenção de quedas → 2 vídeos dentro desse tema
```

O comando roda numa única sessão, sem subagentes e sem pesquisa (desde 2026-10-14):

1. Lê `roteiro.md`, `seo.md` e a lista local de assuntos já tratados (`assuntos.md`) e escolhe os temas pelo próprio conhecimento, sem consultar YouTube, Analytics ou a web.
2. Escreve um roteiro por tema (duração e cenas de `describe_format_limits`) com o SEO dentro (objeto `seo`: título, descrição, hashtags, tags e comentário).
3. Envia cada roteiro com `start_motion_keyframes`: o sistema salva `youtube-metadata.txt`, narra (~1 min) e gera as imagens-chave (~10 min por vídeo). A animação (2 a 3 horas por vídeo, um de cada vez) entra na fila quando você aprova as imagens no painel.

As imagens e as animações continuam rodando mesmo se você fechar o Claude Code.

### 4. Acompanhar e revisar

```text
/acompanhar-lote
```

Mostra o progresso e revisa os vídeos que terminaram. Se precisar, refaz só as cenas com problema. Os arquivos ficam em `generated/<influenciador>/<lote>/<video>/`: assista a `short.mp4` e copie os textos de `youtube-metadata.txt` para o YouTube Studio. **A publicação é sempre manual.**

Para ver os jobs sem abrir o Claude Code: `.venv/bin/python scripts/motion_job.py status`.

### Customizações

| O que mudar | Onde |
|---|---|
| Quantidade de vídeos e tema do lote | Argumentos do `/gerar-lote` (veja os exemplos acima) |
| Nome do influenciador e persona | Respondidos na primeira execução. Para usar uma foto sua como avatar, salve-a como `generated/<nome-do-influenciador>/avatar.png`, quadrada. Nos scripts de linha de comando, use a variável `INFLUENCER_NAME` |
| Aparência do personagem nas cenas (idade, roupa, cabelo) | `CHARACTER` em `influencer_mcp/motion.py` |
| Estilo visual (lápis, aquarela, luz) | `STYLE` em `influencer_mcp/motion.py` |
| Intensidade do LoRA de lápis, ou desligá-lo | `COMFYUI_SCENE_LORA_STRENGTH` no `.mcp.json` (0 a 1). Para desligar, deixe `COMFYUI_SCENE_LORA` vazio |
| Voz da narração | `KOKORO_VOICE` no `.mcp.json`: `pm_santa` (padrão), `pm_alex` (masculina) ou `pf_dora` (feminina). Para usar o Piper, `TTS_ENGINE` = `piper` |
| Velocidade da fala | `KOKORO_SPEED` no `.mcp.json` (ex.: `0.9` mais lenta, `1.1` mais rápida) |
| Público, tom, duração e número de cenas | `roteiro.md` e `.claude/commands/gerar-lote.md` (duração e cenas: `describe_format_limits`, fonte única em `motion.py`) |
| Critérios de escolha de tema | `.claude/commands/gerar-lote.md` (passo 2) e `assuntos.md` (o que não pode voltar) |
| Regras de título, descrição e hashtags | `seo.md` |
| Checkpoint, ControlNet e modelos Wan | Variáveis `COMFYUI_SCENE_CHECKPOINT`, `COMFYUI_OPENPOSE_CONTROLNET`, `WAN_HIGH_NOISE_MODEL`, `WAN_LOW_NOISE_MODEL`, `WAN_HIGH_NOISE_LORA` e `WAN_LOW_NOISE_LORA`. O arquivo precisa estar na pasta `models` do ComfyUI |
| Fonte das legendas | Variável `CAPTION_FONT` (padrão: Arial do sistema) |

Depois de mudar o `.mcp.json` ou uma variável de ambiente, feche e abra o Claude Code de novo. Mudanças nos arquivos `.claude/` valem na próxima execução do comando.

---

Servidor MCP para avatar, ilustrações de cenas, narração e montagem de Shorts verticais. O comando `/gerar-lote` roda numa única sessão do Claude Code: temas, roteiros e SEO. A publicação é sempre manual.

## O fluxo

1. O Claude escolhe os temas pelo próprio conhecimento do público 45+, fora da lista local de assuntos já tratados (nenhuma consulta ao YouTube, ao Analytics ou à web).
2. Escreve os roteiros em português do Brasil, concisos e com cenas ilustráveis, cada um com o SEO dentro.
3. `start_motion_keyframes` salva título, descrição, hashtags, tags e comentário sugerido, narra e gera uma imagem-chave por cena (SDXL, com cenário). Depois da aprovação no painel, o Wan 2.2 I2V anima cada cena, com upscale, interpolação e narração Kokoro montados pelo FFmpeg.

Execute `/gerar-lote` no Claude Code (ou pelo painel) quando quiser iniciar um lote. Nada é enviado, agendado ou publicado no YouTube.

## Requisitos

- macOS em Apple Silicon, Python 3.11 ou superior e Claude Code.
- ComfyUI local (instalado pelo `install.sh`) com [checkpoint Juggernaut XL v9](https://huggingface.co/RunDiffusion/Juggernaut-XL-v9), [ControlNet OpenPose SDXL da Xinsir](https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0) e o [Canopus Pencil Art LoRA](https://huggingface.co/prithivMLmods/Canopus-Pencil-Art-LoRA). As licenças declaradas nos cartões são CreativeML OpenRAIL-M, Apache-2.0 e Apache-2.0; o cartão Canopus avisa que o LoRA está inacabado e pode gerar artefatos. Confira os termos do Juggernaut, principalmente para monetização, antes de publicar. O filtro MediaPipe roda localmente na CPU.
- Modelo MediaPipe Pose Landmarker Lite local (`pose_landmarker_lite.task`), apontado por `MEDIAPIPE_POSE_MODEL_PATH`.
- Narração com a Kokoro 82M (Apache 2.0), voz `pm_santa`, num ambiente próprio (`~/Influencer/tools/tts/venv`, apontado por `KOKORO_PYTHON`). O Piper continua disponível com `TTS_ENGINE=piper`.
- FFmpeg e FFprobe (`FFMPEG_EXECUTABLE` e `FFPROBE_EXECUTABLE`).

Não há chaves de API de imagem, voz ou vídeo. Os roteiros usam a instalação do Claude Code; geração de mídia e narração são locais.

## Vídeos com movimento

O modo padrão produz vídeo com movimento real (não imagens paradas). Cada cena do roteiro traz `keyframePrompt` (imagem inicial) e dois `motions` (um por clipe de ~5 s). Duas etapas rodam como jobs em segundo plano, um por vez na GPU, e retomam de onde pararam:

1. `start_motion_keyframes`: narração (Kokoro, voz pm_santa) e imagens-chave (~10 min). Revise `keyframes-sheet.png`.
2. `start_motion_animation`: Wan 2.2 I2V 14B GGUF Q4_K_M + LoRA lightx2v de 4 passos a 480×832, RealESRGAN 2x, RIFE e montagem 1080×1920 a 30 fps. Cerca de 4 a 5 horas por vídeo numa RTX 3070 de 8 GB. Revise `motion-review.png`.

Use `/acompanhar-lote` para ver o progresso, revisar e refazer cenas. Fora do Claude Code, `python scripts/motion_job.py status` lista os jobs e `scripts/motion_job.py keyframes|animation ...` dispara novos. Cada job verifica o ComfyUI antes de usar a GPU e o inicia sozinho se estiver fora (também se ele cair no meio da animação); para subir manualmente, `python scripts/start_comfyui_detached.py` (processo independente do terminal, não duplica um servidor já no ar). Requisitos extras: ComfyUI v0.37.4 ou mais novo com os nós ComfyUI-GGUF e ComfyUI-Frame-Interpolation, e os modelos em `models/unet` (Wan2.2-I2V-A14B High/LowNoise Q4_K_M), `models/text_encoders` (umt5_xxl_fp8_e4m3fn_scaled), `models/vae` (wan_2.1_vae), `models/loras` (lightx2v 4 passos) e `models/upscale_models` (RealESRGAN_x2plus). As imagens estáticas continuam disponíveis com `render_youtube_short`.

## Escolha de temas

Desde 2026-10-14 não há pesquisa de tendências: o conector não consulta YouTube, Google Trends, Analytics nem a web. Os temas vêm do conhecimento do Claude e da lista local `assuntos.md` (problemas já tratados, que nunca voltam). Escolha de tema e metadados não garantem audiência nem aprovação editorial.

## Arquivos

A pasta `output/` recebe só o resultado final de cada vídeo: `<Título>.mp4` e `<Título>.txt` (título, descrição com hashtags, tags e comentário sugerido), com o título sugerido pelo SEO como nome. A cópia é automática quando o vídeo e os metadados estão prontos.

Cada execução grava seus resultados em `generated/<influenciador>/<lote>/<video>/`: `script.json`, imagens de cena e guias OpenPose, `pose-validation.json`, áudios WAV, segmentos de montagem, `contact-sheet.png` (gerada automaticamente com todas as cenas e legendas), `short.mp4` e `youtube-metadata.json`. Renderizar de novo o mesmo lote e slug apaga antes as cenas, tentativas, segmentos e metadados antigos daquele vídeo. O MediaPipe exige uma única pessoa com cabeça detectável e geometria compatível com a pose; uma cena reprovada é gerada novamente até três vezes; o relatório registra cada candidato e qual imagem foi selecionada. O avatar do canal fica em `generated/<influenciador>/avatar.png` e aparece como retrato recorrente nas cenas do vídeo. Os identificadores de lote separam execuções e evitam sobrescrever semanas anteriores. Esses arquivos não são incluídos no controle de versão.

As cenas usam Juggernaut XL v9 com ControlNet OpenPose SDXL, proporções humanas naturais e fundo branco simples. O prompt pede acabamento de grafite, mas sem LoRA o resultado pode parecer uma ilustração sombreada/renderizada, não um desenho claramente feito a lápis. O Canopus Pencil Art LoRA (Apache-2.0, marcado inacabado) é opcional e fica desativado por padrão porque os testes ficaram mais caricaturais. O fluxo usa 35 passos, CFG 5 e sampler DPM++ 2M Karras; poses verticais são geradas em 768×1344 e prancha/flexão em canvas paisagem 1344×768. A composição preserva a imagem inteira em vez de recortar corpos horizontais. O ControlNet orienta a pose, mas não garante mãos, anatomia detalhada, roupa ou objetos corretos; o MediaPipe não detecta todas as deformações visuais. Revise cada imagem manualmente junto do texto e não publique cenas incompatíveis. Não são demonstrações filmadas, e conteúdo automatizado não substitui avaliação profissional individual.

## Revisar e publicar manualmente

Use `short.mp4` para assistir ao vídeo completo; a `contact-sheet.png` de cada pasta ajuda a comparar as três cenas com `script.json`. Confira rosto e corpo, mãos e pés, roupa, objetos de apoio, se a posição demonstra o movimento descrito e se áudio e legendas coincidem. O MediaPipe só é uma verificação geométrica auxiliar: uma cena aprovada ainda pode estar visualmente errada. Se algo não corresponder, não publique; corrija o roteiro ou a descrição visual e gere a cena novamente.

Para publicar, abra o YouTube Studio, carregue manualmente o `short.mp4` escolhido, copie título, descrição e tags de `youtube-metadata.json`, revise o público, as legendas, a miniatura e a visibilidade e só então decida se publica ou agenda. O comentário em `pinned_comment` é apenas uma sugestão; fixe-o manualmente se desejar. Pesquisa de tendências, metadados e aprovação técnica não garantem audiência nem aprovação editorial. Este projeto não autentica nem publica no YouTube.

O lote de exemplo em `generated/historiador/2026-09-28-0127/` reúne sete Shorts e os respectivos roteiros, metadados e folhas de contato. É material de revisão, não uma declaração de que as ilustrações estão aprovadas para publicação. Consulte `final-production-report.json` para durações e verificações técnicas.
