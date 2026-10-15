"""Abre o painel local de produção em http://127.0.0.1:8766 (só neste PC).

Uso: python scripts/painel.py [--porta 8766]
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CLAUDE_PROJECT_DIR", str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
import motion_job  # noqa: E402,F401  (carrega o ambiente do .mcp.json, como os outros scripts)

from influencer_mcp.painel import serve  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--porta", type=int, default=8766)
serve(parser.parse_args().porta)
