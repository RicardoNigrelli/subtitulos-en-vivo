# Subtítulos en vivo para conferencias — Nerdearla Vibeathon 2026

## English summary (for judges who don't read Spanish)

Open source **live transcription + translation for conferences**: live audio in → real-time
subtitles (original language + English→Spanish), several sessions in parallel, a web view where
each viewer picks a session and a language. Built for Nerdearla Vibeathon 2026 (Argentina) with
Google Gemini (Live API for speech-to-text, Flash-Lite for text translation).

**Real, reproducible status** (each number below has the exact command that reproduces it; see the
Spanish sections for the full detail):

- Two **simultaneous real ASR** sessions (English + Spanish), ~11 minutes of audio each: transcript
  coverage 1.0 (no gaps), perceived latency p50 ≈ 0.44 s. Reproducible at smaller scale with
  `qa/smoke.py` and the audio clips shipped in the repo.
- Live EN→ES / ES→EN translation is **partial**: 72–77 % of text blocks translated, roughly 9 s
  behind the transcript. Recomputed with `worker.medir_traduccion` over a recorded session.
- Runs fully in **replay mode** too (recorded sessions, no API key, no cost) — always labeled
  `replay: true` on screen; replay by itself does **not** satisfy "live audio" or "two simultaneous
  sessions".

Quickest start, no API key, no cost (replay mode):

```bash
MODO=replay docker compose -f ops/docker-compose.yml up -d --build
# http://localhost:8080/          session index
# http://localhost:8080/panel/    monitoring panel
docker compose -f ops/docker-compose.yml down
```

With a real `GEMINI_API_KEY` in `.env` (see "Credenciales y modelos" below), the same `docker
compose ... up` runs real ASR instead. Everything else (how to run manually, credentials, test
audio, how this scales, cost estimation, how it was built with AI agents) is documented in Spanish
below — this is a Spanish-first hackathon project, but every command is copy-pasteable regardless of
language. License: Apache 2.0 (`LICENSE`).

---

Transcripción y traducción simultánea, open source, para conferencias con varias salas en paralelo.
Audio en vivo → subtítulos en tiempo real (idioma original + inglés→español) → vista web donde cada
persona elige la sesión y el idioma.

## Qué hace (R17a–R21)

- Recibe audio en vivo desde un archivo, una URL (`yt-dlp`) o cualquier fuente que entienda `ffmpeg`
  (R17a).
- Transcribe en tiempo real el idioma original de la sesión, español o inglés (R18).
- Traduce en tiempo real de inglés a español, y también de español a inglés, con un segundo modelo
  de texto (R19; la Live API no traduce por sí sola).
- Muestra los subtítulos en una vista web por sesión e idioma (R20), con export a SRT/VTT/TXT (R8d).
- Procesa al menos dos sesiones en simultáneo con ASR real (R21) y explica cómo escalar a más, más
  abajo.

**Estado real de esta entrega** — ningún número sin el comando del repo que lo reproduce:

| Qué | Resultado de una corrida real | Cómo reproducirlo |
|---|---|---|
| R21: dos sesiones con ASR real en simultáneo, ~11 min de audio cada una | cobertura 1,0 y 1,0 (sin huecos); latencia PERCIBIDA p50 ≈ 0,44 s (0,443 / 0,429 s), p95 ≈ 0,60 s (0,615 / 0,585 s) | ver "Reproducir los números de R21" abajo |
| R19: traducción EN→ES / ES→EN en vivo, en paralelo con la transcripción | **PARCIAL**: 72–77 % de los bloques traducidos (181/253 EN→ES, 191/247 ES→EN), atraso p50 ≈ 9 s (9,6 s EN→ES, 8,8 s ES→EN) respecto del texto original | `.venv/Scripts/python -m worker.medir_traduccion <casete>.jsonl` sobre un casete real (recalcula ok/fallidas y el atraso desde los timestamps grabados) |

Esto es cobertura, latencia percibida y proporción de bloques traducidos —no una cifra de
facturación ni de precisión de reconocimiento— todo reproducible desde archivos de este repo (skill
`anti-alucinacion`).

### Reproducir los números de R21

La corrida citada arriba (2 × ~11 min) usó las charlas completas descargadas en
`fixtures/audio/full/` (no se versionan por tamaño, ver "Audios de prueba" abajo) y gastó cupo real
de Gemini. Comando exacto (requiere `GEMINI_API_KEY` y haber bajado antes los archivos completos):

```bash
.venv/Scripts/python qa/smoke.py --sesiones 2 --duracion 580 --inicio 300 --api \
  --hub-externo --puerto 8100 --tope-envio-s 708 \
  --clips fixtures/audio/full/nerdearla-en-booch.m4a,fixtures/audio/full/nerdearla-es-paez.m4a \
  --langs en,es --sids qa-b5r-en,qa-b5r-es --tag b5r
```

Con sólo lo que YA está en el repo (los clips cortos de 60 s de `fixtures/audio/clips/`) se puede
reproducir el mismo mecanismo a menor escala, también con ASR real (gasta cupo real, no correrlo sin
necesidad — skill `cuota-gemini`):

```bash
.venv/Scripts/python qa/smoke.py --sesiones 2 --duracion 60 --api \
  --clips fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav,fixtures/audio/clips/nerdearla-es-paez-300s-60s.wav
```

Y sin ninguna cuota, contra un casete ya grabado (replay):

```bash
.venv/Scripts/python -m worker.medir_traduccion fixtures/casetes/b4-trad-vivo-en.jsonl fixtures/casetes/b4-trad-vivo-es.jsonl
```

## Cómo levantar

Puerto del proyecto: **8080** (verificar que esté libre antes: `netstat -ano | findstr :8080`; en
Windows el bind no falla si el puerto está tomado, se lo lleva el primer server que lo abrió).

### Con Docker Compose

Parado desde la **raíz** del repo:

```bash
# con .env real (GEMINI_API_KEY completa) -> ASR real
docker compose -f ops/docker-compose.yml up -d --build

# SIN API key / sin gastar cuota -> modo replay, rotulado `replay: true` en /api/sesiones.
# Override DURO: fuerza replay aunque el .env tenga una key real.
MODO=replay docker compose -f ops/docker-compose.yml up -d --build

# sin ningún .env en la raíz: también cae solo a replay (ver ops/entrypoint-worker.sh)

docker compose -f ops/docker-compose.yml down
```

Levanta dos servicios (`hub` y `worker`) sobre una única imagen (`ops/Dockerfile`). El hub sirve TODO
en el `:8080`: índice de sesiones (`GET /`), la vista por sesión (`/s/<id>?lang=es`) y el panel de
monitoreo (`/panel/`). **El modo replay reproduce sesiones grabadas: no cumple R17a ni R21 por sí
solo** (no es audio en vivo); sirve para mostrar el pipeline completo (hub + vista + panel +
fan-out) sin credenciales ni costo (eje 3 de escalabilidad, ver más abajo).

### Manual, con venv

```bash
.venv/Scripts/python -m pip install -r requirements.txt   # una vez

# hub + vista de audiencia + panel, los tres en el puerto 8080
HUB_HOST=localhost HUB_PORT=8080 .venv/Scripts/python -m hub --web web --panel panel
```

En otra terminal, una sesión con ASR real desde un archivo:

```bash
.venv/Scripts/python -m worker.run --archivo fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav \
    --sesion sala-1 --lang en --hub ws://localhost:8080/ingest
```

O una sesión en modo replay, sin API, desde un casete ya grabado:

```bash
.venv/Scripts/python -m worker.replay fixtures/casetes/b4-trad-vivo-en.jsonl --hub ws://localhost:8080/ingest
```

Con el hub arriba, abrir `http://localhost:8080/` (índice), `http://localhost:8080/s/sala-1?lang=es`
(subtítulos) o `http://localhost:8080/panel/` (monitoreo).

## Credenciales y modelos

Copiar `.env.example` a `.env` en la raíz. **Nunca se commitea** (`git check-ignore .env` → `.env`).
Detalle completo de cada variable, comentado, en `.env.example`; resumen:

| Variable | Para qué |
|---|---|
| `GEMINI_API_KEY` | obligatoria para ASR real; sin ella (o con `MODO=replay`) el worker cae a modo replay |
| `GEMINI_API_KEY_RESERVA` | opcional: segunda key de OTRO proyecto de Google Cloud, cupo propio; sólo como reserva de emergencia para el video, no hace falta para levantar el proyecto |
| `GEMINI_LIVE_MODEL` | `gemini-3.5-transcribe-live` — transcripción en vivo (R18) |
| `GEMINI_TEXT_MODEL` / `GEMINI_TEXT_MODEL_ALT` | `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` — traducción de texto EN↔ES (R19); la Live API no traduce, hace falta un segundo paso con un modelo de texto |
| `HUB_TOKEN` | token de ingesta (worker→hub) y de `GET /api/metricas`; cambiar el default `dev-token` en un evento real |

## Audios de prueba e importar (R17b)

Tres clips cortos (60–90 s) ya están en el repo en `fixtures/audio/clips/`, citados con URL y minuto
exacto en [`fixtures/audio/FUENTES.md`](fixtures/audio/FUENTES.md) (charlas públicas del canal de
YouTube de Nerdearla). Para importar audio propio (archivo local o URL que entienda `yt-dlp`) y
recortar un clip nuevo:

```bash
.venv/Scripts/python -m worker.importar "https://www.youtube.com/watch?v=<id>" --inicio 300 --duracion 60 --slug mi-charla
# guarda fixtures/audio/clips/mi-charla-300s-60s.wav
```

## Cómo escalar a más sesiones (R21, C3) — tres ejes

La fuerza bruta no alcanza: hay tres cuellos de botella distintos y cada uno se resuelve distinto.

1. **Sesiones contra la API de Gemini.** Cada sesión de transcripción en vivo consume el cupo de
   *Transcribe Live* del proyecto de Google Cloud (nivel gratuito: 20 000 TPM). Antes de la
   vibeathon se midieron **7 sesiones concurrentes** con ese cupo; en esta entrega el MVP corrió
   **2 sesiones reales de ~11 min en simultáneo** (tabla de arriba). Para más salas que las que
   entran en el nivel gratuito: pasar a un **nivel pago** de Gemini, o repartir sesiones entre
   **varios proyectos de Google Cloud** (cada uno con su propia `GEMINI_API_KEY` y su propio cupo).
2. **Espectadores por sesión.** El hub hace fan-out por sesión + idioma, con una cola propia por
   cliente: uno lento o colgado no afecta a los demás (se lo desconecta con `1013` y reconecta
   solo). No gasta cupo de Gemini — es tráfico entre el hub y los navegadores. Medido con clientes
   sintéticos:
   ```bash
   .venv/Scripts/python -m hub.carga --hub http://localhost:8100 --clientes 200
   .venv/Scripts/python -m hub.carga --hub http://localhost:8100 --clientes 500
   ```
3. **Modo replay para demos sin cupo.** El mismo hub y la misma vista reproducen sesiones grabadas
   (`fixtures/casetes/`, `python -m worker.replay`) con sus tiempos originales: se pueden mostrar
   muchas salas en paralelo sin tocar la API. Es lo que permite `docker compose up` sin ninguna
   credencial. Siempre rotulado `replay: true` en `/api/sesiones`, nunca presentado como ASR en
   vivo (eje 3 de C3).

Además, cada sesión Live se **rota sola, de forma transparente para la audiencia**: al cerrarse la
conexión del servidor, si queda "atascada" (atraso sostenido o muda), preventivamente a los 240 s de
audio (antes de que el servidor la corte solo), o si el servidor manda `GoAway`, el worker reabre una
conexión nueva con solape y sigue emitiendo con el mismo `seq` continuo. Es manejo del ciclo de vida
de la conexión — **nunca se presenta como una falla del proveedor**.

## Panel de monitoreo (R8e)

`http://localhost:8080/panel/` (con `docker compose` o `python -m hub --web web --panel panel`):
estado por sesión, p50/p95 de latencia, rotaciones con motivo (cierre / atasco / preventiva /
`GoAway`) y "sin texto hace N s". Pide `Authorization: Bearer <HUB_TOKEN>` contra `GET
/api/metricas` (el panel lo agrega solo cuando se lo sirve desde el propio hub).

## Export SRT / VTT / TXT (R8d)

```bash
.venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/b1-en-60s-rederivado-trad.jsonl --formato srt
.venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/b1-en-60s-rederivado-trad.jsonl --formato vtt --lang es
.venv/Scripts/python docs/export/exportar.py --hub http://localhost:8080 --sesion sala-1 --formato txt
```

## Más documentación

- [`PROMPTS.md`](PROMPTS.md) — cómo se construyó este proyecto con agentes durante la vibeathon,
  qué se investigó antes (permitido, R12) y qué se escribió durante (obligatorio, R11a/R11b); es la
  respuesta preparada a X2.
- [`docs/costos.md`](docs/costos.md) — estimación de costo por minuto de audio y por sesión.
- [`hub/README.md`](hub/README.md) y [`contracts/README.md`](contracts/README.md) — protocolo y
  contrato de mensajes entre worker, hub y la vista.
- [`fixtures/audio/FUENTES.md`](fixtures/audio/FUENTES.md) — de dónde salen los audios de prueba.

## Licencia

Apache 2.0. Ver [LICENSE](LICENSE). El repositorio es público y Nerdearla puede usarlo, adaptarlo y
forkearlo respetando la licencia (R25).
