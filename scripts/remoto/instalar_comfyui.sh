#!/usr/bin/env bash
# Instala o ComfyUI no disco persistente do RunPod (/workspace), com as MESMAS versões da instalação local.
# Idempotente: pode rodar de novo; só baixa o que falta. Log em /workspace/instalar.log.
set -euo pipefail
W=/workspace
cd "$W"

clone() {  # url pasta commit
  if [ ! -d "$2/.git" ]; then git clone "$1" "$2"; fi
  git -C "$2" fetch --quiet --all || true
  git -C "$2" checkout --quiet "$3"
}
clone https://github.com/comfyanonymous/ComfyUI ComfyUI 8ff6dc38
mkdir -p ComfyUI/custom_nodes
clone https://github.com/city96/ComfyUI-GGUF.git ComfyUI/custom_nodes/ComfyUI-GGUF 6ea2651
clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation 26545cc
clone https://github.com/cubiq/ComfyUI_IPAdapter_plus ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus a0f451a

# Ambiente Python no disco persistente, reaproveitando o torch da imagem (CUDA 12.4).
[ -x "$W/venv/bin/python" ] || python3 -m venv --system-site-packages "$W/venv"
PY="$W/venv/bin/python"
"$PY" -m pip install --quiet --upgrade pip
# Mesmo PyTorch da instalação local (o ComfyUI 8ff6dc38 não roda no 2.4 da imagem).
"$PY" -m pip install --quiet torch==2.14.0 torchvision==0.29.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu126
"$PY" -m pip install --quiet -r ComfyUI/requirements.txt
"$PY" -m pip install --quiet -r ComfyUI/custom_nodes/ComfyUI-GGUF/requirements.txt
"$PY" -m pip install --quiet -r ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation/requirements-no-cupy.txt
"$PY" -m pip install --quiet "huggingface_hub[hf_transfer]"

M=ComfyUI/models
mkdir -p $M/checkpoints $M/loras $M/controlnet $M/unet $M/diffusion_models $M/text_encoders $M/vae $M/upscale_models $M/ipadapter $M/clip_vision
export HF_HUB_ENABLE_HF_TRANSFER=1
hf_get() {  # repo arquivo destino
  if [ -s "$3" ]; then return; fi
  "$PY" - "$1" "$2" "$3" <<'EOF'
import os, shutil, sys
from huggingface_hub import hf_hub_download
repo, name, dest = sys.argv[1:]
path = hf_hub_download(repo, name, local_dir="/workspace/hf-cache")
shutil.move(path, dest)
EOF
}
hf_get RunDiffusion/Juggernaut-XL-v9 Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors $M/checkpoints/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors
# Wan 2.2 completo em FP8 (a H100 não usa a versão compacta GGUF, apagada do disco em 2026-10-05) e o Fun Control
# (cenas de exercício conduzidas por esqueleto; fun-alto/fun-baixo no gpu_remota.md).
for n in i2v_high i2v_low fun_control_high fun_control_low; do
  f=wan2.2_${n}_noise_14B_fp8_scaled.safetensors
  hf_get Comfy-Org/Wan_2.2_ComfyUI_Repackaged split_files/diffusion_models/$f $M/diffusion_models/$f
done
hf_get Comfy-Org/Wan_2.1_ComfyUI_repackaged split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors $M/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
hf_get Comfy-Org/Wan_2.1_ComfyUI_repackaged split_files/vae/wan_2.1_vae.safetensors $M/vae/wan_2.1_vae.safetensors
hf_get h94/IP-Adapter sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors $M/ipadapter/ip-adapter-plus-face_sdxl_vit-h.safetensors
hf_get h94/IP-Adapter sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors $M/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors
hf_get h94/IP-Adapter models/image_encoder/model.safetensors $M/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors
[ -s $M/upscale_models/RealESRGAN_x2plus.pth ] || wget -q -O $M/upscale_models/RealESRGAN_x2plus.pth \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
rm -rf /workspace/hf-cache

# Inicialização automática a cada vez que a máquina liga (chamado pelo dockerArgs do pod).
cat > "$W/start_comfyui.sh" <<'EOF'
#!/usr/bin/env bash
# Saídas e entradas do ComfyUI no disco TEMPORÁRIO da máquina (/root), não no disco persistente: os quadros de
# cada clipe (~300 MB) encheram os 100 GB do volume em 2026-10-04. O disco temporário é zerado a cada
# desligamento, e arquivos com mais de 30 min (já baixados pelo painel) são apagados no meio da sessão.
rm -rf /workspace/ComfyUI/output/* /workspace/ComfyUI/temp/* 2>/dev/null
mkdir -p /root/comfy/output /root/comfy/input /root/comfy/temp
( while true; do find /root/comfy/output /root/comfy/input /root/comfy/temp -type f -mmin +30 -delete 2>/dev/null; sleep 300; done ) &
cd /workspace/ComfyUI
# Só em 127.0.0.1: o acesso é pelo túnel SSH do painel, nunca exposto na internet.
exec /workspace/venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch \
  --output-directory /root/comfy/output --input-directory /root/comfy/input --temp-directory /root/comfy/temp
EOF
chmod +x "$W/start_comfyui.sh"
echo "INSTALACAO_OK"
