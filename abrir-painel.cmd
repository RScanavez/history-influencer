@echo off
rem Abre o painel local de producao (http://127.0.0.1:8766). Se ja estiver aberto, so abre o navegador.
cd /d "%~dp0"
rem Projeto derivado do Influencer: mesmo canal nao; mesma GPU sim. A trava compartilhada impede os dois de usarem a placa juntos.
set INFLUENCER_NAME=Historiador
set INFLUENCER_GPU_LOCK=D:\Influencer\Influencer\generated\jobs\gpu.lock
set COMFYUI_DIR=D:\Influencer\tools\ComfyUI
set KOKORO_PYTHON=D:\Influencer\tools\tts\venv\Scripts\python.exe
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8766/api/estado | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  rem Janela oculta: o console fica invisivel e os comandos chamados pelo painel nao abrem prompts.
  powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -WorkingDirectory '%~dp0.' -FilePath '.venv\Scripts\python.exe' -ArgumentList 'scripts\painel.py'"
  timeout /t 3 /nobreak >nul
)
start "" http://127.0.0.1:8766
