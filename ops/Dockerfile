# Imagen única para hub, worker, web y panel. El servicio que corre cada contenedor lo decide el
# `command` (o el `entrypoint`, para el worker) en ops/docker-compose.yml, no este Dockerfile.
FROM python:3.13-slim

# ffmpeg: decodificación de audio en la ingesta del worker (audio-pipeline, skill ingesta).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Capa de dependencias primero: se cachea mientras requirements.txt no cambie.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Resto del repo. .dockerignore deja afuera .env, .git, .venv, reportes/, qa/out/,
# fixtures/audio/full/ y .claude/: la key real NUNCA queda dentro de la imagen.
COPY . .

# Usuario sin privilegios (hallazgo MEDIO #3 de reportes/seguridad.md: hub, worker, web y panel
# corrían como root). El hub no escribe nada dentro del contenedor; el worker en modo replay sí
# escribe (casetes de salida bajo fixtures/casetes/ si se usa CASETE_REPLAY de escritura, y logs
# propios bajo reportes/). reportes/ está en .dockerignore y no llega con el COPY de arriba: se
# crea acá a mano para que exista con dueño correcto antes del chown.
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -M -s /usr/sbin/nologin app \
    && mkdir -p /app/reportes /app/fixtures/casetes \
    && chown -R app:app /app

USER app

# Default si alguien corre la imagen suelta sin `command`: hub. worker/web/panel lo pisan desde compose.
CMD ["python", "-m", "hub"]
