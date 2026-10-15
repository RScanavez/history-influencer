<#
.SYNOPSIS
  Instala tudo o que os agentes de /gerar-lote precisam, inclusive numa máquina nova.

.DESCRIPTION
  Pode ser executado quantas vezes quiser: o que já estiver instalado ou baixado é mantido.
  Etapas:
    1. Pré-requisitos (winget): Python 3.11, Git, FFmpeg e Claude Code.
    2. Ambiente do servidor MCP (.venv) e modelo MediaPipe Pose.
    3. Narração Kokoro em ambiente próprio (<ToolsDir>\tts\venv).
    4. ComfyUI com os nós ComfyUI-GGUF, ComfyUI-Frame-Interpolation e ComfyUI_IPAdapter_plus (<ToolsDir>\ComfyUI).
    5. Modelos: Juggernaut XL v9, ControlNet OpenPose, Canopus LoRA, Wan 2.2 I2V e Fun Control, IP-Adapter, RealESRGAN (~60 GB).
    6. Variáveis de ambiente do usuário lidas pelo .mcp.json.
    7. Verificação final (servidor MCP, FFmpeg, Kokoro e ComfyUI com os nós e modelos).

  Sem GPU NVIDIA (ou com -Gpu remota) instala só o necessário para usar a GPU remota (RunPod): pula o ComfyUI e
  os ~60 GB de modelos, usa o PyTorch de CPU na narração e, no fim, mostra o que falta para a GPU remota
  (chaves em secrets\) em vez de falhar.

.PARAMETER Gpu
  auto (padrão: local se houver GPU NVIDIA, senão remota), local ou remota.

.PARAMETER ToolsDir
  Pasta das ferramentas pesadas (ComfyUI, modelos, Kokoro). Padrão: D:\Influencer\tools se existir o
  drive D:, senão C:\Influencer\tools.

.PARAMETER Torch
  Build do PyTorch: cu126 (padrão com GPU local, NVIDIA com driver 525 ou mais novo), cu128 ou cpu (padrão sem GPU).

.PARAMETER SkipModels
  Não baixa os modelos do ComfyUI (útil para instalar primeiro e baixar depois).

.PARAMETER SkipVerify
  Não inicia o ComfyUI nem executa a verificação final.

.EXAMPLE
  .\install.cmd
  .\install.ps1 -ToolsDir E:\IA\tools
#>
[CmdletBinding()]
param(
    [string]$ToolsDir,
    [ValidateSet("auto", "local", "remota")]
    [string]$Gpu = "auto",
    [ValidateSet("", "cu126", "cu128", "cpu")]
    [string]$Torch = "",
    [switch]$SkipModels,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
# O PowerShell 5.1 envia texto a programas externos em ASCII; a narração de teste tem acentos.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$ProjectDir = $PSScriptRoot

# GPU local ou só remota (pedido do usuário, 2026-10-04: o painel precisa rodar em qualquer computador; sem GPU,
# os vídeos vão para a GPU remota e o instalador não pode exigir GPU, ComfyUI nem os modelos).
$HasNvidia = $false
$CudaDevice = $null
$Blackwell = $false
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $GpuInfo = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null) -join "; "
    $HasNvidia = ($LASTEXITCODE -eq 0) -and [bool]$GpuInfo
    if ($HasNvidia) {
        # Com mais de uma GPU, o ComfyUI usa a de mais memória (COMFYUI_CUDA_DEVICE); antes usava sempre a primeira.
        $Gpus = @(& nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader,nounits 2>$null | ForEach-Object {
            $p = $_ -split ",\s*"; [pscustomobject]@{ Index = [int]$p[0]; Name = $p[1]; MemoryMB = [int]$p[2] } })
        $Best = $Gpus | Sort-Object MemoryMB -Descending | Select-Object -First 1
        if ($Gpus.Count -gt 1) { $CudaDevice = $Best.Index }
        # RTX 50 (Blackwell) só roda com o PyTorch de CUDA 12.8 (2026-10-06, segundo computador com RTX 5070).
        $Blackwell = $Best.Name -match "RTX 50\d\d|RTX PRO|Blackwell|B200|GB20"
    }
}
if ($Gpu -eq "auto") { if ($HasNvidia) { $Gpu = "local" } else { $Gpu = "remota" } }
$RemoteOnly = ($Gpu -eq "remota")
if (-not $Torch) { if ($RemoteOnly) { $Torch = "cpu" } elseif ($Blackwell) { $Torch = "cu128" } else { $Torch = "cu126" } }
if ($RemoteOnly) { $MinFreeGB = 8 } else { $MinFreeGB = 90 }

if (-not $ToolsDir) {
    if (Test-Path "D:\") { $ToolsDir = "D:\Influencer\tools" } else { $ToolsDir = "C:\Influencer\tools" }
}
$ComfyDir = Join-Path $ToolsDir "ComfyUI"
$ComfyPython = Join-Path $ComfyDir "venv\Scripts\python.exe"
$KokoroPython = Join-Path $ToolsDir "tts\venv\Scripts\python.exe"
$ProjectPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$TorchIndex = "https://download.pytorch.org/whl/$Torch"

# ---------------------------------------------------------------- utilitários

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    [ok] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "    [aviso] $Text" -ForegroundColor Yellow }

# Função simples (sem param) para repassar argumentos como -m e --index-url sem que o PowerShell os interprete.
function Invoke-Native {
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "Falhou (código $LASTEXITCODE): $exe $($rest -join ' ')" }
}

# Executa um programa só para testar o código de saída, sem que mensagens em stderr interrompam o script.
function Test-Native {
    $ErrorActionPreference = "Continue"
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Install-WingetPackage([string]$Id, [string]$Name) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget não encontrado. Instale '$Name' manualmente ou instale o App Installer pela Microsoft Store."
    }
    Write-Host "    instalando $Name pelo winget..."
    & winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
    # -1978335189 = já instalado e sem atualização disponível
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { throw "winget não conseguiu instalar $Name." }
    Update-SessionPath
}

function Set-UserVariable([string]$Name, [string]$Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Set-Item -Path "env:$Name" -Value $Value
    Write-Ok "$Name = $Value"
}

function Get-RemoteFile([string]$Url, [string]$Destination) {
    if ((Test-Path $Destination) -and (Get-Item $Destination).Length -gt 0) {
        Write-Ok "já existe: $(Split-Path $Destination -Leaf)"
        return
    }
    New-Item -ItemType Directory -Force (Split-Path $Destination -Parent) | Out-Null
    $partial = "$Destination.part"
    Write-Host "    baixando $(Split-Path $Destination -Leaf)..."
    # -C - retoma um download interrompido a partir do arquivo .part
    & curl.exe --fail --location --retry 5 --retry-delay 5 -C - --output $partial $Url
    if ($LASTEXITCODE -ne 0) { throw "Download falhou: $Url (execute o instalador de novo para retomar)." }
    Move-Item -Force $partial $Destination
    Write-Ok "baixado: $(Split-Path $Destination -Leaf)"
}

function Sync-GitRepo([string]$Url, [string]$Destination) {
    if (Test-Path (Join-Path $Destination ".git")) {
        Write-Ok "já existe: $Destination"
        return
    }
    Invoke-Native git clone --depth 1 $Url $Destination
}

function New-Venv([string]$Path) {
    if (Test-Path (Join-Path $Path "Scripts\python.exe")) { return }
    Invoke-Native py -3.11 -m venv $Path
    Invoke-Native (Join-Path $Path "Scripts\python.exe") -m pip install --upgrade pip
}

function Test-PipPackage([string]$Python, [string]$Package) {
    return (Test-Native $Python -m pip show --quiet $Package)
}

# ---------------------------------------------------------------- 0. verificações iniciais

Write-Host "Instalação do Influencer local" -ForegroundColor White
Write-Host "  Projeto:     $ProjectDir"
Write-Host "  Ferramentas: $ToolsDir"
Write-Host "  PyTorch:     $Torch"
if ($RemoteOnly) { Write-Host "  GPU:         só remota (RunPod); ComfyUI e modelos locais não são instalados" }
else { Write-Host "  GPU:         local (NVIDIA) e remota" }

Write-Step "Verificando a máquina"
$drive = Get-PSDrive -Name ($ToolsDir.Substring(0, 1))
$freeGB = [math]::Round($drive.Free / 1GB)
if ($freeGB -lt $MinFreeGB -and -not $SkipModels) {
    throw "Espaço livre em $($drive.Name): é de $freeGB GB; são necessários cerca de $MinFreeGB GB. Use -ToolsDir para outro disco."
}
Write-Ok "$freeGB GB livres em $($drive.Name):"
if ($HasNvidia) {
    Write-Ok "GPU NVIDIA: $GpuInfo"
} else {
    Write-Ok "sem GPU NVIDIA: as imagens e animações serão feitas na GPU remota (RunPod)."
}
if (Get-Command ssh -ErrorAction SilentlyContinue) { Write-Ok "cliente SSH (túnel da GPU remota)" }
else { Write-Warn "cliente SSH não encontrado: ative em Configurações > Sistema > Recursos opcionais > Cliente OpenSSH (usado pela GPU remota)." }

# ---------------------------------------------------------------- 1. pré-requisitos

Write-Step "1/7 Pré-requisitos (Python 3.11, Git, FFmpeg, Claude Code)"
Update-SessionPath

if (-not (Get-Command py -ErrorAction SilentlyContinue) -or -not (Test-Native py -3.11 --version)) { Install-WingetPackage "Python.Python.3.11" "Python 3.11" }
Write-Ok "Python 3.11"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Install-WingetPackage "Git.Git" "Git" }
Write-Ok "Git"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-WingetPackage "Gyan.FFmpeg" "FFmpeg" }
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg -or -not $ffprobe) { throw "FFmpeg instalado, mas não encontrado no PATH. Abra um novo PowerShell e execute de novo." }
Write-Ok "FFmpeg: $ffmpeg"

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "    instalando o Claude Code (instalador oficial)..."
    Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    Update-SessionPath
}
if (Get-Command claude -ErrorAction SilentlyContinue) { Write-Ok "Claude Code" }
else { Write-Warn "Claude Code instalado; abra um novo PowerShell para usar o comando 'claude'." }

# ---------------------------------------------------------------- 2. servidor MCP

Write-Step "2/7 Servidor MCP do projeto (.venv) e modelo MediaPipe"
New-Venv (Join-Path $ProjectDir ".venv")
Invoke-Native $ProjectPython -m pip install --quiet -e $ProjectDir
Write-Ok "influencer_mcp instalado em .venv"
$poseModel = Join-Path $ProjectDir "generated\models\pose_landmarker_lite.task"
Get-RemoteFile "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task" $poseModel

# ---------------------------------------------------------------- 3. Kokoro

Write-Step "3/7 Narração Kokoro (vozes do personagens.md; roda na CPU se não houver GPU)"
New-Venv (Join-Path $ToolsDir "tts\venv")
if (-not (Test-PipPackage $KokoroPython "torch")) {
    Invoke-Native $KokoroPython -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url $TorchIndex
}
if (-not (Test-PipPackage $KokoroPython "kokoro")) {
    Invoke-Native $KokoroPython -m pip install kokoro soundfile num2words phonemizer-fork espeakng-loader
}
Write-Ok "Kokoro instalada em $KokoroPython"

# ---------------------------------------------------------------- 4. ComfyUI

$downloads = @()
if ($RemoteOnly) {
    Write-Step "4/7 e 5/7 ComfyUI e modelos: pulados (sem GPU local; a máquina remota tem os seus)"
} else {
Write-Step "4/7 ComfyUI e nós personalizados"
Sync-GitRepo "https://github.com/comfyanonymous/ComfyUI.git" $ComfyDir
New-Venv (Join-Path $ComfyDir "venv")
if (-not (Test-PipPackage $ComfyPython "torch")) {
    Invoke-Native $ComfyPython -m pip install torch torchvision torchaudio --index-url $TorchIndex
}
Invoke-Native $ComfyPython -m pip install --quiet -r (Join-Path $ComfyDir "requirements.txt")
Write-Ok "ComfyUI em $ComfyDir"

$gguf = Join-Path $ComfyDir "custom_nodes\ComfyUI-GGUF"
Sync-GitRepo "https://github.com/city96/ComfyUI-GGUF.git" $gguf
Invoke-Native $ComfyPython -m pip install --quiet -r (Join-Path $gguf "requirements.txt")
Write-Ok "ComfyUI-GGUF"

$rife = Join-Path $ComfyDir "custom_nodes\ComfyUI-Frame-Interpolation"
Sync-GitRepo "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git" $rife
Invoke-Native $ComfyPython -m pip install --quiet -r (Join-Path $rife "requirements-no-cupy.txt")
Write-Ok "ComfyUI-Frame-Interpolation (o modelo rife49.pth é baixado no primeiro uso)"

# Rosto fixo da personagem entre as cenas (IP-Adapter; mesma versão da GPU remota).
$ipadapter = Join-Path $ComfyDir "custom_nodes\ComfyUI_IPAdapter_plus"
Sync-GitRepo "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git" $ipadapter
Write-Ok "ComfyUI_IPAdapter_plus"

# ---------------------------------------------------------------- 5. modelos

Write-Step "5/7 Modelos (~60 GB; downloads interrompidos são retomados na próxima execução)"
$models = Join-Path $ComfyDir "models"
$hf = "https://huggingface.co"
$downloads = @(
    @("$hf/RunDiffusion/Juggernaut-XL-v9/resolve/main/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors", "checkpoints\Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"),
    @("$hf/xinsir/controlnet-openpose-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors", "controlnet\xinsir-openpose-sdxl.safetensors"),
    @("$hf/prithivMLmods/Canopus-Pencil-Art-LoRA/resolve/main/Canopus-Pencil-Art-LoRA.safetensors", "loras\Canopus-Pencil-Art-LoRA.safetensors"),
    @("$hf/prithivMLmods/Canopus-Pixar-Art/resolve/main/Canopus-Pixar-Art.safetensors", "loras\Canopus-Pixar-Art.safetensors"),
    @("$hf/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf", "unet\Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf"),
    @("$hf/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf", "unet\Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf"),
    @("$hf/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
    @("$hf/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors", "vae\wan_2.1_vae.safetensors"),
    @("$hf/lightx2v/Wan2.2-Distill-Loras/resolve/main/wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors", "loras\wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"),
    @("$hf/lightx2v/Wan2.2-Distill-Loras/resolve/main/wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors", "loras\wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"),
    @("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth", "upscale_models\RealESRGAN_x2plus.pth"),
    # Identidade visual Graphic novel (2026-10-13).
    @("$hf/blink7630/graphic-novel-illustration/resolve/main/Graphic_Novel_Illustration-000007.safetensors", "loras\Graphic-Novel-Illustration-XL.safetensors"),
    # Cenas de exercício conduzidas por esqueleto (Wan 2.2 Fun Control).
    @("$hf/QuantStack/Wan2.2-Fun-A14B-Control-GGUF/resolve/main/HighNoise/Wan2.2-Fun-A14B-Control_HighNoise-Q4_K_M.gguf", "unet\Wan2.2-Fun-A14B-Control_HighNoise-Q4_K_M.gguf"),
    @("$hf/QuantStack/Wan2.2-Fun-A14B-Control-GGUF/resolve/main/LowNoise/Wan2.2-Fun-A14B-Control_LowNoise-Q4_K_M.gguf", "unet\Wan2.2-Fun-A14B-Control_LowNoise-Q4_K_M.gguf"),
    # Rosto fixo da personagem (IP-Adapter Plus Face e Plus, com o codificador de imagem ViT-H).
    @("$hf/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors", "ipadapter\ip-adapter-plus-face_sdxl_vit-h.safetensors"),
    @("$hf/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors", "ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors"),
    @("$hf/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors", "clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")
)
if ($SkipModels) {
    Write-Warn "pulando os modelos (-SkipModels). Execute o instalador de novo sem essa opção antes do primeiro lote."
} else {
    foreach ($item in $downloads) { Get-RemoteFile $item[0] (Join-Path $models $item[1]) }
}

# Segunda GPU (ex.: AMD Radeon integrada ao lado da NVIDIA): o RIFE dos clipes roda nela pelo rife-ncnn-vulkan
# (influencer_mcp/gpu_auxiliar.py). Máquina com uma GPU só não baixa nada e continua com o RIFE no ComfyUI.
$secondGpu = Get-CimInstance Win32_VideoController |
    Where-Object { $_.Name -notmatch "NVIDIA|Microsoft|Virtual|Remote|Parsec|Citrix" } | Select-Object -First 1
if ($secondGpu) {
    $rifeDir = Join-Path $ToolsDir "rife-ncnn-vulkan"
    if (-not (Test-Path (Join-Path $rifeDir "rife-ncnn-vulkan.exe"))) {
        $rifeZip = Join-Path $ToolsDir "rife-ncnn-vulkan.zip"
        Get-RemoteFile "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip" $rifeZip
        $rifeTemp = Join-Path $ToolsDir "rife-ncnn-vulkan-extraindo"
        Expand-Archive -Force $rifeZip $rifeTemp
        Move-Item (Join-Path $rifeTemp "rife-ncnn-vulkan-20221029-windows") $rifeDir
        Remove-Item -Recurse -Force $rifeTemp, $rifeZip
    }
    Write-Ok "segunda GPU ($($secondGpu.Name)): RIFE dos clipes nela (rife-ncnn-vulkan em $rifeDir)"
} else {
    Write-Ok "uma GPU só: o RIFE continua no ComfyUI"
}
}

# ---------------------------------------------------------------- 6. variáveis

Write-Step "6/7 Variáveis de ambiente do usuário (lidas pelo .mcp.json)"
if (-not $RemoteOnly) { Set-UserVariable "COMFYUI_DIR" $ComfyDir }
if ($null -ne $CudaDevice) { Set-UserVariable "COMFYUI_CUDA_DEVICE" "$CudaDevice" }
Set-UserVariable "COMFYUI_CHECKPOINT" "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
Set-UserVariable "COMFYUI_SCENE_CHECKPOINT" "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
Set-UserVariable "COMFYUI_OPENPOSE_CONTROLNET" "xinsir-openpose-sdxl.safetensors"
Set-UserVariable "MEDIAPIPE_POSE_MODEL_PATH" $poseModel
Set-UserVariable "KOKORO_PYTHON" $KokoroPython
# Pasta do projeto neste computador (D: num, C: no outro); o .mcp.json usa ${INFLUENCER_ROOT}.
Set-UserVariable "INFLUENCER_ROOT" $PSScriptRoot
Set-UserVariable "FFMPEG_EXECUTABLE" $ffmpeg
Set-UserVariable "FFPROBE_EXECUTABLE" $ffprobe

# ---------------------------------------------------------------- 7. verificação

if ($SkipVerify) {
    Write-Warn "verificação final pulada (-SkipVerify)."
    exit 0
}

Write-Step "7/7 Verificação"
$failures = @()

if (Test-Native $ProjectPython -c "import influencer_mcp.server") { Write-Ok "servidor MCP importa sem erros" } else { $failures += "servidor MCP (.venv)" }

if (Test-Native $ffmpeg -hide_banner -version) { Write-Ok "FFmpeg responde" } else { $failures += "FFmpeg" }

Write-Host "    testando a Kokoro (o primeiro uso baixa o modelo de voz, ~330 MB)..."
$sample = Join-Path $env:TEMP "influencer-kokoro-teste.wav"
$ErrorActionPreference = "Continue"
"Olá! A instalação da narração funcionou." | & $KokoroPython (Join-Path $ProjectDir "scripts\kokoro_tts.py") --voice pm_santa --output_file $sample
$kokoroOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if ($kokoroOk -and (Test-Path $sample)) { Write-Ok "Kokoro gerou áudio de teste: $sample" } else { $failures += "Kokoro" }

if ($RemoteOnly) {
    # Sem GPU local o ComfyUI não é verificado aqui; mostra o que falta para a GPU remota (não é falha da instalação:
    # as chaves ficam em secrets\, fora do git, e são copiadas do outro computador ou criadas de novo).
    $remote = & $ProjectPython -c "import json; from influencer_mcp import ambiente; print(json.dumps(ambiente.remote_checks()))" | ConvertFrom-Json
    $pending = @($remote | Where-Object { -not $_.ok })
    if ($pending.Count -eq 0) { Write-Ok "GPU remota pronta para uso" }
    else {
        Write-Warn "para usar a GPU remota falta:"
        foreach ($item in $pending) { Write-Host "      - $($item.label): $($item.help)" -ForegroundColor Yellow }
        Write-Host "      Atalho: copie a pasta secrets\ inteira do computador principal para $ProjectDir\secrets\" -ForegroundColor Yellow
        Write-Host "      (chaves do RunPod, SSH e YouTube)." -ForegroundColor Yellow
    }
}
$comfyUrl = "http://127.0.0.1:8188"
function Test-Comfy { try { Invoke-RestMethod "$comfyUrl/system_stats" -TimeoutSec 3 | Out-Null; return $true } catch { return $false } }
if ($RemoteOnly) {
    Write-Ok "ComfyUI local não verificado (modo só GPU remota)"
} elseif (-not (Test-Comfy)) {
    Write-Host "    iniciando o ComfyUI em segundo plano..."
    Invoke-Native $ProjectPython (Join-Path $ProjectDir "scripts\start_comfyui_detached.py")
    $deadline = (Get-Date).AddMinutes(4)
    while (-not (Test-Comfy) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
}
if ($RemoteOnly) {
} elseif (Test-Comfy) {
    Write-Ok "ComfyUI respondendo em $comfyUrl"
    foreach ($node in @("UnetLoaderGGUF", "RIFE VFI", "WanImageToVideo")) {
        try {
            $info = Invoke-RestMethod "$comfyUrl/object_info/$([uri]::EscapeDataString($node))" -TimeoutSec 30
            if ($info.PSObject.Properties.Name -contains $node) { Write-Ok "nó $node" } else { $failures += "nó $node" }
        } catch { $failures += "nó $node" }
    }
    if (-not $SkipModels) {
        foreach ($item in $downloads) {
            $folder, $file = $item[1] -split "\\", 2
            # O ComfyUI-GGUF lista os .gguf numa pasta virtual própria.
            if ($file -like "*.gguf") { $folder = "unet_gguf" }
            try { $list = Invoke-RestMethod "$comfyUrl/models/$folder" -TimeoutSec 30 } catch { $list = @() }
            if ($list -contains $file) { Write-Ok "modelo $file" } else { $failures += "modelo $folder/$file" }
        }
    }
} else {
    $failures += "ComfyUI (veja $ComfyDir\comfyui.log)"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "Instalação concluída com pendências:" -ForegroundColor Yellow
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    Write-Host "Corrija e execute o instalador de novo; o que já está pronto é mantido."
    exit 1
}
Write-Host "Tudo pronto." -ForegroundColor Green
Write-Host "Feche este terminal, abra um novo PowerShell nesta pasta e execute:  claude"
Write-Host "No Claude Code, aprove o servidor 'influencer-local', confira com /mcp e rode /gerar-lote."
