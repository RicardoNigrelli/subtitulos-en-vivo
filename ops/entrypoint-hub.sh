#!/bin/sh
# Arranca el hub en Docker con el HUB_TOKEN compartido con el worker (ops/docker-compose.yml,
# servicio token-init). Si HUB_TOKEN llega por el entorno de quien invoca `docker compose` o por
# ../.env (env_file de este servicio), se usa TAL CUAL (mismo criterio que ../.env para el worker,
# así los dos quedan con el mismo valor sin pasar por el archivo). Si no vino de ninguno de los dos
# (vacío incluido, como .env.example), se toma el que token-init generó en el volumen compartido.
#
# hub/config.py y hub/__main__.py (backend, no se tocan desde ops) ya loguean el ORIGEN del token
# (entorno/.env/default) y SU LARGO, nunca el valor. Acá, antes del exec, se agrega una línea propia
# con el origen tal como lo ve este entrypoint y sus PRIMEROS 4 caracteres (nunca el token entero),
# para poder cotejarlo a ojo con lo que ve el worker o con `docker compose exec hub cat
# /run/vibeathon/hub-token` (ver README, "Cómo levantar").
set -eu

TOKEN_FILE=/run/vibeathon/hub-token

if [ -n "${HUB_TOKEN:-}" ]; then
    ORIGEN="entorno-o-.env (mismo valor que ve el worker por env_file)"
else
    HUB_TOKEN="$(cat "$TOKEN_FILE")"
    ORIGEN="generado por token-init ($TOKEN_FILE)"
fi
export HUB_TOKEN

PREFIJO=$(printf '%s' "$HUB_TOKEN" | cut -c1-4)
echo "[entrypoint-hub] HUB_TOKEN origen=$ORIGEN primeros4=${PREFIJO}... (nunca se imprime entero;"
echo "[entrypoint-hub]   ver completo: docker compose -f ops/docker-compose.yml exec hub cat /$TOKEN_FILE)"

exec python -m hub --web web --panel panel
