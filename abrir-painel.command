#!/usr/bin/env bash
# Abre o painel local de produção do Historiador (http://127.0.0.1:8766; o do personal-influencer usa a 8765).
# Se já estiver aberto, só abre o navegador. Dê dois cliques no Finder (ou rode ./abrir-painel.command).
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
# Mesma GPU do personal-influencer: a trava é o gpu.lock dele, para os dois nunca usarem a GPU ao mesmo tempo.
export INFLUENCER_NAME=Historiador
export INFLUENCER_GPU_LOCK="${INFLUENCER_GPU_LOCK:-$HOME/Documents/personal-influencer/generated/jobs/gpu.lock}"
if ! curl -fs -m 2 http://127.0.0.1:8766/api/estado >/dev/null; then
  nohup "$PY" scripts/painel.py >/dev/null 2>&1 &
  sleep 3
fi
open http://127.0.0.1:8766
