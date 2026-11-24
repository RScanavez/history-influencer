#!/usr/bin/env bash
# Instalador para macOS (equivalente enxuto do install.ps1). Pode rodar de novo sem problemas.
# Instala: Python 3.11 (via uv), FFmpeg (via Homebrew, se houver), o .venv do servidor MCP, o modelo MediaPipe Pose
# e a narração Kokoro. Mac não tem GPU NVIDIA: ComfyUI e modelos locais NÃO são instalados; os vídeos usam a GPU
# remota (RunPod, veja gpu_remota.md). As variáveis ficam em ~/.influencer.env (lido pelo app e pelo painel).
set -euo pipefail
cd "$(dirname "$0")"
PROJECT="$PWD"
TOOLS="${INFLUENCER_TOOLS_DIR:-$HOME/Influencer/tools}"
ENV_FILE="$HOME/.influencer.env"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

step() { printf '\n==> %s\n' "$1"; }

step "1/4 Pré-requisitos"
if ! command -v uv >/dev/null; then
  echo "    instalando uv (gerenciador de Python, instalador oficial da Astral)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
FFMPEG_BIN=""; FFPROBE_BIN=""
if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then
  FFMPEG_BIN="$(command -v ffmpeg)"; FFPROBE_BIN="$(command -v ffprobe)"
elif command -v brew >/dev/null; then
  brew install ffmpeg; FFMPEG_BIN="$(command -v ffmpeg)"; FFPROBE_BIN="$(command -v ffprobe)"
else
  echo "    sem Homebrew: baixando FFmpeg estático (pacote static-ffmpeg) para $TOOLS/ffmpeg..."
  mkdir -p "$TOOLS"
  [ -x "$TOOLS/ffmpeg-venv/bin/python" ] || uv venv --python 3.11 "$TOOLS/ffmpeg-venv"
  uv pip install --python "$TOOLS/ffmpeg-venv/bin/python" static-ffmpeg
  # o pacote imprime o progresso do download na saída padrão; o resultado é a última linha
  read -r FFMPEG_BIN FFPROBE_BIN < <("$TOOLS/ffmpeg-venv/bin/python" -c \
    "from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise as g; print(*g())" | tail -n 1)
fi
command -v claude >/dev/null || echo "    aviso: Claude Code não encontrado (https://claude.ai/install.sh)."

step "2/4 Servidor MCP (.venv) e modelo MediaPipe"
[ -x .venv/bin/python ] || uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
POSE="$PROJECT/generated/models/pose_landmarker_lite.task"
mkdir -p "$(dirname "$POSE")"
[ -s "$POSE" ] || curl -fL --retry 5 -o "$POSE" \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task

step "3/4 Narração Kokoro (CPU/Apple Silicon)"
KOKORO_VENV="$TOOLS/tts/venv"
mkdir -p "$TOOLS/tts"
[ -x "$KOKORO_VENV/bin/python" ] || uv venv --python 3.11 "$KOKORO_VENV"
uv pip install --python "$KOKORO_VENV/bin/python" torch torchaudio kokoro soundfile num2words phonemizer-fork espeakng-loader

step "4/4 Variáveis em $ENV_FILE"
set_var() { grep -v "^$1=" "$ENV_FILE" 2>/dev/null > "$ENV_FILE.tmp" || true; echo "$1=$2" >> "$ENV_FILE.tmp"; mv "$ENV_FILE.tmp" "$ENV_FILE"; }
touch "$ENV_FILE"
set_var MEDIAPIPE_POSE_MODEL_PATH "$POSE"
set_var FFMPEG_EXECUTABLE "$FFMPEG_BIN"
set_var FFPROBE_EXECUTABLE "$FFPROBE_BIN"
set_var KOKORO_PYTHON "$KOKORO_VENV/bin/python"

cat <<MSG

Pronto. Próximos passos:
  - GPU remota: coloque a chave do RunPod em secrets/runpod.txt e crie a chave SSH (veja o painel > ambiente).
  - Abrir o painel: ./abrir-painel.command
MSG
