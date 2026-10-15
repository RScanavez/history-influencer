"""Dispara jobs de vídeo com movimento pela linha de comando, sem o Claude Code.

Usa o mesmo ambiente que o .mcp.json repassa ao servidor MCP (variáveis do usuário no Windows).

Uso:
  python scripts/motion_job.py keyframes <batch_id> <roteiro.json> [cenas,separadas,por,vírgula]
  python scripts/motion_job.py animation <batch_id> <slug> [cenas]
  python scripts/motion_job.py status [job_id]

Influenciador: variável INFLUENCER_NAME (padrão "Historiador").
"""
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if os.name == "nt":
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        for name in (
            "COMFYUI_CHECKPOINT", "COMFYUI_SCENE_CHECKPOINT", "COMFYUI_OPENPOSE_CONTROLNET", "PIPER_EXECUTABLE",
            "PIPER_MODEL_PATH", "FFMPEG_EXECUTABLE", "FFPROBE_EXECUTABLE", "KOKORO_PYTHON", "MEDIAPIPE_POSE_MODEL_PATH",
        ):
            try:
                os.environ.setdefault(name, winreg.QueryValueEx(key, name)[0])
            except FileNotFoundError:
                pass
# Valores fixos do .mcp.json (LoRA, voz, URL do ComfyUI) valem aqui também.
# Se o .mcp.json estiver ausente ou sem o servidor, segue só com o ambiente do usuário em vez de travar
# o painel e todos os jobs na importação.
try:
    _mcp_env = json.loads((PROJECT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["influencer-local"]["env"]
except (OSError, ValueError, KeyError) as exc:
    print(f"aviso: .mcp.json sem o servidor influencer-local ({exc!r}); usando só o ambiente do usuário", file=sys.stderr)
    _mcp_env = {}
for name, value in _mcp_env.items():
    if "${" not in value:
        os.environ[name] = value
os.environ["CLAUDE_PROJECT_DIR"] = str(PROJECT)
# Pasta do projeto em cada computador (D: num, C: no outro): o .mcp.json dos lotes do painel a usa.
os.environ.setdefault("INFLUENCER_ROOT", str(PROJECT))
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))
sys.stdout.reconfigure(encoding="utf-8")

from influencer_mcp import motion, motion_jobs  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    kind = sys.argv[1]
    if kind == "run":
        # Usado pelo próprio sistema de jobs (processo criado pelo WMI, fora do Claude Code): executa um job já registrado.
        motion_jobs.run_job(Path(sys.argv[2]))
        return
    if kind == "status":
        jobs = [motion_jobs.job_status(sys.argv[2])] if len(sys.argv) > 2 else motion_jobs.list_jobs()
        for job in jobs:
            last = (job["progress"] or [""])[-1]
            print(f"{job['id']} {job['kind']:9} {job['state']:7} {last}")
            if job.get("error"):
                print("   erro:", job["error"].splitlines()[0])
        return
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    batch_id, target = sys.argv[2:4]
    scenes = [int(number) for number in sys.argv[4].split(",")] if len(sys.argv) > 4 else None
    params = {
        "influencer_name": os.environ.get("INFLUENCER_NAME", "Historiador"),
        "batch_id": batch_id,
        "scene_numbers": scenes,
    }
    if kind == "keyframes":
        script = json.loads(Path(target).read_text(encoding="utf-8"))
        motion.validate_motion_script(script)
        params["script"] = script
    elif kind == "animation":
        params["video_slug"] = target
    else:
        raise SystemExit(__doc__)
    print(motion_jobs.start_job(kind, params)["id"])


if __name__ == "__main__":
    main()
