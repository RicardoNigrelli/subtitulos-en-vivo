#!/bin/sh
# Elige ASR real o modo replay segun si GEMINI_API_KEY esta presente (skill contexto, eje 3 de C3:
# el pipeline entero corre en modo replay sin cuota; skill cuota-gemini: un solo agente -audio-
# pipeline- gasta cuota real, todo lo demas usa casetes grabados).
#
# TODO confirmar con audio-pipeline: flags reales de `worker.run` (sesion, idioma, fuente de audio).
# CASETE_REPLAY apunta al unico casete real que existe al cierre de B1 (fixtures/casetes/b1-en-60s.jsonl,
# corrida de 60s citada en reportes/ops-b1.md); es un default de ops, no confirmado por audio-pipeline.
# Van a aparecer mas casetes (2 completos con GoAway, ~10 min, EN y ES) en bloques siguientes: cuando
# eso pase, actualizar este default o pasar CASETE_REPLAY por variable de entorno.
set -eu

HUB_PORT="${HUB_PORT:-8100}"
CASETE_REPLAY="${CASETE_REPLAY:-fixtures/casetes/b1-en-60s.jsonl}"

if [ -n "${GEMINI_API_KEY:-}" ]; then
    echo "[entrypoint-worker] GEMINI_API_KEY presente -> modo ASR real"
    exec python -m worker.run "$@"
else
    echo "[entrypoint-worker] SIN GEMINI_API_KEY -> modo REPLAY (rotulado; no cumple R17a/R21 por si solo)"
    exec python -m worker.replay "$CASETE_REPLAY" --hub "ws://hub:${HUB_PORT}/ingest" "$@"
fi
