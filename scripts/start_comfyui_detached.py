"""Garante o ComfyUI no ar em segundo plano, independente do terminal ou da sessão que o disparou.

Se já estiver respondendo, não faz nada. Pasta do ComfyUI: variável COMFYUI_DIR (definida pelo install.ps1),
padrão D:\Influencer\tools\ComfyUI. Os jobs de vídeo fazem esta mesma verificação sozinhos antes de usar a GPU.
"""
import os
import sys

from influencer_mcp.comfyui import comfyui_dir, ensure_running

base = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
try:
    already = ensure_running(base, log=print)
except RuntimeError as error:
    sys.exit(str(error))
print("ComfyUI já estava no ar." if already else "ComfyUI no ar.", "Log em", comfyui_dir() / "comfyui.log")
