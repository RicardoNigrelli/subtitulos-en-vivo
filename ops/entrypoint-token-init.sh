#!/bin/sh
# Genera, UNA sola vez, el HUB_TOKEN compartido entre `hub` y `worker` cuando nadie puso uno propio
# en el entorno ni en ../.env. Corre como servicio aparte porque hub y worker son dos contenedores
# distintos: sin este paso, cada uno generaría un token distinto y no podrían hablarse (pedido
# cruzado de backend, reportes/backend-seguridad.md: "No se levanta" con HUB_TOKEN=dev-token/vacío/
# corto fuera de loopback, y HUB_HOST=0.0.0.0 en este compose).
#
# El archivo vive en el volumen nombrado `hub-token` (montado en /run/vibeathon por los tres
# servicios). Idempotente: si ya existe (el volumen sobrevivió a un `docker compose down` sin
# `-v`), no se toca -> hub y worker mantienen el mismo token entre reinicios sin key propia.
#
# Este script NO decide si hub/worker usan este token generado o uno propio: eso lo resuelve cada
# uno en su propio entrypoint con `HUB_TOKEN="${HUB_TOKEN:-$(cat /run/vibeathon/hub-token)}"`
# (bash/sh: `:-` sólo cae al archivo si HUB_TOKEN no vino del entorno o de ../.env, vacío incluido).
set -eu

TOKEN_FILE=/run/vibeathon/hub-token

if [ -f "$TOKEN_FILE" ]; then
    echo "[token-init] $TOKEN_FILE ya existe (volumen conservado entre 'docker compose up'): no se genera de nuevo"
    exit 0
fi

python -c "import secrets, pathlib; pathlib.Path('$TOKEN_FILE').write_text(secrets.token_urlsafe(24))"
n=$(wc -c < "$TOKEN_FILE")
echo "[token-init] generado $TOKEN_FILE ($n caracteres). Ver el valor completo (para pegarlo en el panel):"
echo "[token-init]   docker compose -f ops/docker-compose.yml exec hub cat /$TOKEN_FILE"
