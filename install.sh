#!/usr/bin/env bash
# Instalador para macOS (Apple Silicon). Pode rodar de novo sem problemas: o que já existe é mantido e downloads
# interrompidos continuam de onde pararam.
# Instala: Python 3.11 (via uv), FFmpeg, o .venv do servidor MCP, o modelo MediaPipe Pose, a narração Kokoro e o
# ComfyUI (GPU Metal/MPS) com os nós e ~60 GB de modelos. As variáveis ficam em ~/.influencer.env.
# Opções: --skip-models (não baixa os modelos grandes; rode de novo sem a opção antes do primeiro lote).
set -euo pipefail
SKIP_MODELS=0
for arg in "$@"; do [ "$arg" = "--skip-models" ] && SKIP_MODELS=1; done
cd "$(dirname "$0")"
PROJECT="$PWD"
TOOLS="${INFLUENCER_TOOLS_DIR:-$HOME/Influencer/tools}"
ENV_FILE="$HOME/.influencer.env"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

step() { printf '\n==> %s\n' "$1"; }

step "1/6 Pré-requisitos"
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

step "2/6 Servidor MCP (.venv) e modelo MediaPipe"
[ -x .venv/bin/python ] || uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
POSE="$PROJECT/generated/models/pose_landmarker_lite.task"
mkdir -p "$(dirname "$POSE")"
[ -s "$POSE" ] || curl -fL --retry 5 -o "$POSE" \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task

step "3/6 Narração Kokoro"
KOKORO_VENV="$TOOLS/tts/venv"
mkdir -p "$TOOLS/tts"
[ -x "$KOKORO_VENV/bin/python" ] || uv venv --python 3.11 "$KOKORO_VENV"
uv pip install --python "$KOKORO_VENV/bin/python" torch torchaudio kokoro soundfile num2words phonemizer-fork espeakng-loader

step "4/6 ComfyUI e nós personalizados"
COMFY="$TOOLS/ComfyUI"
clone() { [ -d "$2/.git" ] || git clone --depth 1 "$1" "$2"; }
mkdir -p "$TOOLS"
clone https://github.com/comfyanonymous/ComfyUI.git "$COMFY"
[ -x "$COMFY/venv/bin/python" ] || uv venv --python 3.11 "$COMFY/venv"
CPY="$COMFY/venv/bin/python"
"$CPY" -c "import torch" 2>/dev/null || uv pip install --python "$CPY" torch torchvision torchaudio
uv pip install --python "$CPY" -r "$COMFY/requirements.txt"
clone https://github.com/city96/ComfyUI-GGUF.git "$COMFY/custom_nodes/ComfyUI-GGUF"
uv pip install --python "$CPY" -r "$COMFY/custom_nodes/ComfyUI-GGUF/requirements.txt"
clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git "$COMFY/custom_nodes/ComfyUI-Frame-Interpolation"
uv pip install --python "$CPY" -r "$COMFY/custom_nodes/ComfyUI-Frame-Interpolation/requirements-no-cupy.txt"
clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git "$COMFY/custom_nodes/ComfyUI_IPAdapter_plus"

step "5/6 Modelos (~60 GB; downloads interrompidos são retomados)"
HF=https://huggingface.co
MODELS=(
  "$HF/RunDiffusion/Juggernaut-XL-v9/resolve/main/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors checkpoints/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
  "$HF/xinsir/controlnet-openpose-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors controlnet/xinsir-openpose-sdxl.safetensors"
  "$HF/prithivMLmods/Canopus-Pencil-Art-LoRA/resolve/main/Canopus-Pencil-Art-LoRA.safetensors loras/Canopus-Pencil-Art-LoRA.safetensors"
  "$HF/prithivMLmods/Canopus-Pixar-Art/resolve/main/Canopus-Pixar-Art.safetensors loras/Canopus-Pixar-Art.safetensors"
  "$HF/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf unet/Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf"
  "$HF/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf unet/Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf"
  "$HF/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  "$HF/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors vae/wan_2.1_vae.safetensors"
  "$HF/lightx2v/Wan2.2-Distill-Loras/resolve/main/wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors loras/wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"
  "$HF/lightx2v/Wan2.2-Distill-Loras/resolve/main/wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors loras/wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"
  "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth upscale_models/RealESRGAN_x2plus.pth"
  "$HF/blink7630/graphic-novel-illustration/resolve/main/Graphic_Novel_Illustration-000007.safetensors loras/Graphic-Novel-Illustration-XL.safetensors"
  "$HF/QuantStack/Wan2.2-Fun-A14B-Control-GGUF/resolve/main/HighNoise/Wan2.2-Fun-A14B-Control_HighNoise-Q4_K_M.gguf unet/Wan2.2-Fun-A14B-Control_HighNoise-Q4_K_M.gguf"
  "$HF/QuantStack/Wan2.2-Fun-A14B-Control-GGUF/resolve/main/LowNoise/Wan2.2-Fun-A14B-Control_LowNoise-Q4_K_M.gguf unet/Wan2.2-Fun-A14B-Control_LowNoise-Q4_K_M.gguf"
  "$HF/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors ipadapter/ip-adapter-plus-face_sdxl_vit-h.safetensors"
  "$HF/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors"
  "$HF/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
)
if [ "$SKIP_MODELS" = 1 ]; then
  echo "    pulando os modelos (--skip-models). Rode ./install.sh de novo antes do primeiro lote."
else
  for entry in "${MODELS[@]}"; do
    url="${entry%% *}"; dest="$COMFY/models/${entry#* }"
    [ -s "$dest" ] && [ ! -e "$dest.part" ] && continue
    mkdir -p "$(dirname "$dest")"
    echo "    baixando $(basename "$dest")"
    curl -fL --retry 5 --retry-delay 5 -C - -o "$dest.part" "$url"
    mv "$dest.part" "$dest"
  done
fi

step "6/6 Variáveis em $ENV_FILE"
set_var() { grep -v "^$1=" "$ENV_FILE" 2>/dev/null > "$ENV_FILE.tmp" || true; echo "$1=$2" >> "$ENV_FILE.tmp"; mv "$ENV_FILE.tmp" "$ENV_FILE"; }
touch "$ENV_FILE"
set_var COMFYUI_DIR "$COMFY"
set_var MEDIAPIPE_POSE_MODEL_PATH "$POSE"
set_var FFMPEG_EXECUTABLE "$FFMPEG_BIN"
set_var FFPROBE_EXECUTABLE "$FFPROBE_BIN"
set_var KOKORO_PYTHON "$KOKORO_VENV/bin/python"

cat <<MSG

Pronto. Abrir o painel: ./abrir-painel.command
MSG
