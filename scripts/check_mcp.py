"""Hook SessionStart: confirma que o servidor MCP influencer-local inicia.

Lê command/args/cwd de .mcp.json, faz o handshake `initialize` e, se falhar,
avisa o usuário via systemMessage. Sempre sai com código 0.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "check_mcp", "version": "1"},
    },
}


def check() -> str | None:
    try:
        cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = cfg["mcpServers"]["influencer-local"]
    except Exception as exc:
        return f".mcp.json sem o servidor influencer-local ({exc})"

    # ${VAR} e ${VAR:-padrao}, como o Claude Code expande o .mcp.json (INFLUENCER_ROOT: pasta do projeto em cada PC).
    env = {"CLAUDE_PROJECT_DIR": str(ROOT), **os.environ}
    expand = lambda s: re.sub(r"[$][{](\w+)(?::-([^}]*))?[}]", lambda m: env.get(m.group(1)) or (m.group(2) or m.group(0)), s)
    command = expand(server["command"])
    if "${" in command or not Path(command).exists():
        return f"executável do MCP não encontrado: {command}"

    try:
        proc = subprocess.run(
            [command, *map(expand, server.get("args", []))],
            input=json.dumps(REQUEST) + "\n",
            capture_output=True,
            text=True,
            timeout=20,
            cwd=ROOT,
            creationflags=0x08000000 if os.name == "nt" else 0,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return "o servidor não respondeu ao initialize em 20s"
    except OSError as exc:
        return f"falha ao iniciar o servidor: {exc}"

    if '"serverInfo"' not in proc.stdout:
        err = (proc.stderr or "").strip().splitlines()[-1:] or ["sem saída"]
        return f"handshake falhou (código {proc.returncode}): {err[0]}"
    return None


if __name__ == "__main__":
    problem = check()
    if problem:
        print(json.dumps({"systemMessage": f"⚠ MCP influencer-local: {problem}. Corrija o .mcp.json e rode /mcp."}))
    sys.exit(0)
