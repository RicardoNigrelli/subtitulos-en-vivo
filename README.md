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
- Live EN→ES / ES→EN translation varies a lot by run: 71–78 % of text blocks translated with 9–12 s
  of lag in two longer/earlier runs, up to 100 % with 2.6–2.7 s of lag in the latest, shorter-batch
  run. Recomputed with `worker.medir_traduccion` over recorded sessions (see "Reproducir los
  números de R19" in the Spanish section for the three runs).
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

- Recibe audio en vivo desde un archivo, una URL/stream (`--fuente url`: HTTP, HLS, RTMP, SRT,
  UDP/mpegts — lo que `ffmpeg` abra) o un micrófono (`--fuente mic`) (R17a; qué está verificado de
  cada fuente en "Fuentes de audio en vivo" más abajo).
- Transcribe en tiempo real el idioma original de la sesión, español o inglés (R18).
- Traduce en tiempo real de inglés a español, y también de español a inglés, con un segundo modelo
  de texto (R19; la Live API no traduce por sí sola).
- Muestra los subtítulos en una vista web por sesión e idioma (R20), con export a SRT/VTT/TXT (R8d).
- Procesa al menos dos sesiones en simultáneo con ASR real (R21) y explica cómo escalar a más, más
  abajo.

**Estado real de esta entrega** — ningún número sin el comando del repo que lo reproduce:

| Qué | Resultado de una corrida real | Cómo reproducirlo |
|---|---|---|
| R21: dos sesiones con ASR real en simultáneo, ~11 min de audio cada una | cobertura 1,0 y 1,0 (sin huecos); latencia PERCIBIDA p50 ≈ 0,44 s (0,443 / 0,429 s), p95 ≈ 0,60 s (0,615 / 0,585 s), medida desde el FIN de cada bloque de voz de ~3 s (`t_emit − t_captured`); desde la primera palabra del bloque, misma corrida: p50 ≈ 3,0 s, p95 ≈ 4,3 s. En corridas cortas del 25/09 con atascos del server el p95 desde el fin llegó a 5–18 s (`docs/evidencia.md`) | ver "Reproducir los números de R21" abajo |
| R19: traducción EN→ES / ES→EN en vivo, en paralelo con la transcripción | **RANGO MEDIDO EN TRES CORRIDAS distintas**, no una cifra única: de 71–78 % de bloques traducidos con atraso p50 9–12 s, hasta 100 % con p50 2,6–2,7 s en la corrida con lotes más chicos | ver "Reproducir los números de R19" abajo |

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

### Reproducir los números de R19

La traducción es un segundo modelo aparte de la Live API (`worker/traductor.py`), por lotes (N
ventanas o S segundos desde el primer bloque pendiente, lo que ocurra primero). El resultado varía
con el tamaño del lote, la hora del día y la latencia del modelo de texto en ese momento — por eso
se muestra el RANGO de tres corridas reales, no una sola cifra:

| Corrida | Lote | Cobertura traducida | Atraso (p50 / p95 por dirección) | Comando |
|---|---|---|---|---|
| 2 × 60 s | 2 ventanas / 5 s | 32/41 = 78 % | ambas direcciones juntas: 12,3 s / 20,5 s | `worker.medir_traduccion fixtures/casetes/b4-trad-vivo-en.jsonl fixtures/casetes/b4-trad-vivo-es.jsonl` (arriba) |
| 2 × ~11 min, sin cortacircuito | 2 ventanas / 5 s | EN→ES 181/253 = 71,5 %; ES→EN 191/247 = 77,3 % | EN→ES 9,6 s / 21,8 s; ES→EN 8,8 s / 20,6 s | `worker.medir_traduccion` sobre el casete de la corrida larga (ver "Reproducir los números de R21") |
| 2 × 60 s, lotes más chicos | 2 ventanas / 4 s | EN→ES 26/26 = 100 %; ES→EN 22/22 = 100 % | EN→ES 2,64 s / 6,35 s; ES→EN 2,68 s / 7,12 s | `.venv/Scripts/python -m worker.medir_traduccion fixtures/casetes/b8-trad-vivo-en.jsonl fixtures/casetes/b8-trad-vivo-es.jsonl` |

La tercera corrida también coincidió con una latencia mucho mejor del modelo de texto en sí (no sólo
del lote más chico): no se puede atribuir la mejora completa a `TRADUCTOR_LOTE_S=4` con una sola
corrida por configuración. Es la corrida de referencia con lotes cortos, no el "verdadero": en las tomas reales del 25/09 (`fixtures/casetes/evidencia-25-09/`, cuatro salas de a una) la misma herramienta da p50 3,7 s y p95 12,8 s con 88/90 líneas traducidas, por reintentos tras 5xx o timeout del modelo de texto; el rango completo es el
dato honesto.

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

Levanta tres servicios sobre una única imagen (`ops/Dockerfile`): `token-init`, `hub` y `worker`. El
hub sirve TODO en el `:8080`: índice de sesiones (`GET /`), la vista por sesión (`/s/<id>?lang=es`) y
el panel de monitoreo (`/panel/`). **El modo replay reproduce sesiones grabadas: no cumple R17a ni
R21 por sí solo** (no es audio en vivo); sirve para mostrar el pipeline completo (hub + vista + panel
+ fan-out) sin credenciales ni costo (eje 3 de escalabilidad, ver más abajo).

**`HUB_TOKEN` (token de ingesta worker→hub y de `/api/metricas` del panel) es OBLIGATORIO fuera de
`127.0.0.1`/`localhost`**: con `HUB_HOST=0.0.0.0` (lo que fija este compose), si el token es
`dev-token`, está vacío o tiene menos de 16 caracteres, **el hub no arranca** (exit 2; ver
`hub/config.py::revisar_token`, `reportes/backend-seguridad.md`). Para no obligar a generarlo a mano
antes de un `docker compose up` de un jurado, el servicio `token-init` genera uno una sola vez
(`secrets.token_urlsafe(24)`) en un volumen compartido si `HUB_TOKEN` no vino ni del entorno ni de
`.env`; `hub` y `worker` lo leen del mismo volumen y quedan con el MISMO valor. Si `.env` sí trae un
`HUB_TOKEN` propio (16+ caracteres, generado con el comando de abajo), ambos usan ese en cambio. En
ningún caso el token viaja por query string (`?token=`): queda en los access logs.

```bash
# generar un token propio (para un evento real; opcional para levantar la demo)
python -c "import secrets;print(secrets.token_urlsafe(24))"   # pegarlo en HUB_TOKEN en .env

# ver el token que terminó usando el compose (el propio o el generado por token-init), para
# pegarlo en el campo de token del panel (http://localhost:8080/panel/). Doble barra en la ruta
# ("//run/...") por Git Bash en Windows: reescribe una barra sola como si fuera una ruta local
# incluso dentro de `docker exec` (en Linux/macOS o PowerShell, una barra alcanza):
docker compose -f ops/docker-compose.yml exec hub cat //run/vibeathon/hub-token

# el arranque de cada contenedor también dice el ORIGEN del token y sus primeros 4 caracteres
# (nunca el valor completo):
docker compose -f ops/docker-compose.yml logs hub worker | grep HUB_TOKEN
```

La imagen (`ops/Dockerfile`) corre hub y worker con un usuario sin privilegios (`app`, uid 10001),
no root. Desde este commit, los servidores de desarrollo (`web/servir.py`, `panel/servir.py`)
bindean `127.0.0.1` por defecto y sólo escuchan en la LAN si se pasa `--host 0.0.0.0` a propósito.

### Manual, con venv

```bash
python -m venv .venv                                       # una vez (Python 3.11+; en Linux/macOS: .venv/bin/python en lo que sigue)
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
| `HUB_TOKEN` | token de ingesta (worker→hub) y de `GET /api/metricas`. **Obligatorio** (16+ caracteres, no `dev-token`) si el hub escucha fuera de `127.0.0.1`/`localhost` (Docker, evento real): si no, el hub no arranca. Con Docker Compose, si queda vacío, `token-init` genera uno y lo comparte con `hub` y `worker` (ver "Con Docker Compose" arriba); generar uno propio: `python -c "import secrets;print(secrets.token_urlsafe(24))"`. Nunca por query string |

## Audios de prueba e importar (R17b)

Tres clips cortos (60–90 s) ya están en el repo en `fixtures/audio/clips/`, citados con URL y minuto
exacto en [`fixtures/audio/FUENTES.md`](fixtures/audio/FUENTES.md) (charlas públicas del canal de
YouTube de Nerdearla). Para importar audio propio (archivo local o URL que entienda `yt-dlp`) y
recortar un clip nuevo:

```bash
.venv/Scripts/python -m worker.importar "https://www.youtube.com/watch?v=<id>" --inicio 300 --duracion 60 --slug mi-charla
# guarda fixtures/audio/clips/mi-charla-300s-60s.wav
```

## Fuentes de audio en vivo (R17a): `--fuente archivo|url|mic`

`worker.run --fuente {archivo,url,mic}` arma el comando de `ffmpeg` correspondiente y siempre entrega
PCM s16le 16 kHz mono al pipeline (detalle de cada una, con más ejemplos, en
[`worker/README.md`](worker/README.md#fuentes-en-vivo-r17a---fuente-archivourlmic)):

| Fuente | Qué es | Estado |
|---|---|---|
| `archivo` (default) | `--archivo x.wav`, con `--inicio`/`--duracion` | **VERIFICADO**: es la que corren R21 y el compose con key |
| `url` | `--url <URL>`: HTTP, HLS, RTMP, SRT, UDP/mpegts — lo que `ffmpeg` abra | **VERIFICADO**, incluida una corrida REAL de 30 s contra Gemini |
| `mic` | `--dispositivo "<nombre>"` (dshow en Windows, `avfoundation`/`pulse` en macOS/Linux) | abre el dispositivo; **NO verificado que entregue audio en esta máquina** |

`url` sin API, un encoder mandando UDP/mpegts local (también probado por HTTP local, `-listen 1`):

```bash
ffmpeg -re -i fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav -t 30 -f mpegts udp://127.0.0.1:9000 &
.venv/Scripts/python -m worker.run --fuente url --url udp://127.0.0.1:9000 --sesion prueba-udp \
  --lang en --duracion 20 --traducir-a none --transporte casete:fixtures/casetes/b1-en-60s.jsonl \
  --casete "$TEMP/prueba-udp.jsonl"
# sin gastar cuota; esa corrida: segundos_enviados 22.8, ventanas 8, textos 8, motivo_fin fin_fuente
```

`url` REAL contra Gemini (mismo UDP, 30 s de audio, ASR + traducción EN→ES en el bus):

```bash
.venv/Scripts/python -m worker.run --fuente url --url udp://127.0.0.1:9000 --sesion b8-url-en \
  --lang en --duracion 30 --tope-envio-s 40 --hub ws://localhost:8100/ingest \
  --casete fixtures/casetes/b8-url-udp-en-30s.jsonl
# esa corrida: 34,8 s enviados, 13 ventanas, 11 textos, 11/11 con traducción ok
```

`mic`: `worker.run --listar-dispositivos` abre y lista los dispositivos de audio (`dshow` en
Windows) sin error. `--fuente mic --dispositivo "<nombre>"` también abre el dispositivo sin error,
pero en la máquina donde se desarrolló esto entregó 0 bytes de PCM en 10–12 s con los tres
micrófonos disponibles (posible causa: el dispositivo estaba tomado por otro programa capturando en
paralelo — no confirmado). **No está verificado que `--fuente mic` entregue audio en ninguna
máquina real**; sólo que abre el dispositivo y arma el comando correcto.

## Glosario automático desde la agenda (R8c): `--agenda`

`fixtures/agenda.json` describe el evento y sus charlas (título, orador, empresa, tecnologías,
términos propios); `worker.glosario` extrae de ahí un vocabulario por charla y `worker.run --agenda
fixtures/agenda.json --charla <id>` lo suma a `--vocab` (`custom_vocabulary` de Gemini Live), sin
tocar código para cada evento nuevo:

```bash
.venv/Scripts/python -m worker.glosario fixtures/agenda.json --charla booch-en
.venv/Scripts/python -m worker.run --agenda fixtures/agenda.json --charla booch-en --sesion sala-1 --lang en ...
```

Indicio A/B (**n=1 por configuración: esto es un indicio, no una conclusión**) sobre nombres propios
reales de una de las charlas (`worker/medir_glosario.py`, cuenta apariciones exactas de una lista de
términos en el texto emitido):

| Corrida | Vocabulario | "Jim Rumbaugh" / "Ivar Jacobson" / "Simula" | Otros efectos |
|---|---|---|---|
| sin agenda | 1 término (`Nerdearla`) | ninguno bien escrito ("Ed Jim Rumba", "Ivor") | un "Nerdearla" suelto sin relación con el audio |
| con agenda | 24 términos | los tres, bien escritos | sin ese "Nerdearla" espurio; en otra corrida con el vocabulario completo hubo 2 reaperturas por mudez de causa no determinada |

Los dos tramos de audio comparados no son idénticos (arrancan en segundos distintos del mismo clip) y
es una sola corrida por configuración: es un indicio a favor del glosario, **no una mejora
demostrada**. Comando: `python -m worker.medir_glosario <casete> "Grady Booch,Booch,Nerdearla,Plato,Jim Rumbaugh,Ivar Jacobson,Simula,Golden Age"`.

## Cómo encaja en una sala

Pensado para el esquema real de un evento (hilo de Discord de la vibeathon, respuestas del staff de
Nerdearla): placa de audio → cable de 3,5 mm → una mini PC junto al escenario → audio al navegador o
al servidor; la captura queda escuchando hasta que alguien la frena, y alguien entra por escritorio
remoto a apretar F5 si una pantalla se traba.

- **Entrada de audio desde la mini PC**, de dos formas posibles:
  - El worker corre EN la mini PC, contra el cable de 3,5 mm como entrada de audio:
    ```bash
    .venv/Scripts/python -m worker.run --fuente mic --dispositivo "<nombre dshow>" --sesion sala-1 --lang es --hub ws://SERVIDOR:8080/ingest
    ```
    Implementado; **entrega de audio NO verificada en la máquina de desarrollo el 24/09 porque OBS
    tenía tomado el dispositivo** (mismo estado que en "Fuentes de audio en vivo" arriba).
  - O la mini PC manda el audio por red y el worker corre en el servidor, con `--fuente url`
    (**VERIFICADO con una corrida real de 30 s por UDP el 24/09**, `reportes/audio-pipeline-b8.md`).
    Ejemplo en la mini PC, mandando el cable de 3,5 mm por UDP:
    ```bash
    ffmpeg -f dshow -i audio="<dispositivo>" -ac 1 -ar 16000 -f mpegts udp://SERVIDOR:9000
    ```
    y en el servidor: `worker.run --fuente url --url udp://SERVIDOR:9000 --sesion sala-1 --lang es
    --hub ws://localhost:8080/ingest` (mismo patrón que en "Fuentes de audio en vivo", cambiando
    `127.0.0.1` por la IP de la mini PC).
- **Pantallas frente al escenario:** `http://SERVIDOR:8080/s/<sala>?lang=es&modo=proyeccion`
  (verificado en navegador el 24/09) — sólo el texto, sin el resto de la interfaz.
- **Celulares del público:** el QR de cada sesión, en el índice (`http://SERVIDOR:8080/`), apunta
  directo a `/s/<sala>?lang=..`; sala e idioma quedan en la URL; sin instalar nada ni loguearse.
- **Stream (R8a, opcional):** fuente de navegador de OBS, o entrada Web Browser de vMix, apuntando a
  `http://SERVIDOR:8080/s/<sala>?lang=en&modo=obs` (fondo transparente, dos líneas abajo).
  **Verificado sobre video en OBS el 25/09**: la fuente de navegador deja ver el video del orador con
  las líneas de subtítulo encima (`reportes/obs-transparencia.md`, captura
  `reportes/obs-transparencia.png`).
- **Operación durante la charla:** la captura (`worker`) y las pantallas (`web`) son procesos
  separados — apagar o recargar una pantalla no toca al worker, que sigue mandando audio y recibiendo
  texto (consecuencia del diseño, no una cifra medida). El worker además reabre solo la sesión con
  Gemini ante un cierre del servidor, un atasco, o cada 240 s de audio enviado (`rotation` en el
  panel, con `audio_lost_s` acumulado; las cinco causas completas están en "Cómo escalar" más abajo).
  Del lado de la pantalla, si el hub se reinicia la vista se reconecta sola y recupera por historial
  lo perdido: 69/69 textos recuperados en la prueba de reconexión (`qa/out/reconexion-b2.log`). Si la
  traducción se atrasa, primero se ve el original y recién a los 120 s sin traducción se marca "sin
  traducir" en pantalla.

### Qué pasa si…

Preguntas que hizo el staff de Nerdearla sobre qué sería un "deal breaker" en una charla real (mismo
hilo de Discord citado arriba). Filas con lo medido en una corrida real o deducido del diseño
(rotuladas "por diseño" cuando no hay una medición puntual para ese caso):

| Situación | Qué hace el sistema | Evidencia |
|---|---|---|
| La traducción se atrasa | La transcripción NO se detiene, sigue en el idioma original. La línea queda gris como pendiente y a los 120 s sin traducción se confirma con la marca "sin traducir" (con el original ya pintado); el panel cuenta las traducciones que llegaron `ok:false`. | `reportes/frontend-b4-pendiente-120s.txt`; `panel/app.js` (contador `traduccionesOkFalse`); corrida real 25/09 en `qa/out/final/` (`04-traduccion.log`: EN→ES 15/17, ES→EN 15/15) |
| Gemini cierra la sesión o se atasca | Reapertura automática con solape, sin F5 ni escritorio remoto: por cierre del servidor, atasco/mudez, preventiva a los 240 s de audio enviado, `GoAway`, o un envío que se traba. El panel muestra `rotation` con `audio_lost_s` acumulado. | `qa/out/smoke-b5r.log` (corrida real, 5 rotaciones y 0 tramos con voz sin texto en 2 × 11 min); `qa/out/watchdog-b3.log` (reapertura por mudez, sin API) |
| Se cae la red entre el worker y el hub, o se reinicia el hub | El worker reconecta y reenvía lo pendiente; la vista se reconecta sola y recupera lo perdido pidiendo `historial?desde=`. | `qa/out/reconexion-b2.log` (backlog 69/69 recuperado); `reportes/frontend-b3.md` (backfill en la vista, 0 perdidas / 0 duplicadas) |
| Se recarga la pantalla de la sala | La captura sigue: worker y pantalla son procesos separados. Al reconectar, la vista pinta las últimas líneas guardadas y pide por historial lo que se perdió. | Por diseño (separación worker/web); `reportes/verificacion-final.md` no tiene una fila que mida específicamente una recarga de pantalla (sí mide la reconexión del hub, ítem 5b) |
| Se pierde audio unos segundos en una rotación | El panel lo muestra acumulado (`audio_lost_s`) y la vista marca, en el lugar exacto, "[tramo sin texto: N s]". | `reportes/frontend-b4-huecos.txt` |
| Gemini deja de cerrar turnos a mitad de sesión (comportamiento del server, observado en corridas reales) | El watchdog de atasco reabre la conexión con solape y reenvía hasta 15 s de audio; la pérdida queda acotada. Medido el 25/09 en cinco corridas reales: 0 s de voz sin texto en tres (incluida la de 2 × 11 min) y 11–15 s en dos cortas. | `qa/out/smoke-b5r.log`, `qa/out/gate2/` (2 × 60 s, 25/09 02:15); comando: `python qa/smoke.py --sesiones 2 --duracion 60 ...` |
| Dos o más salas del mismo proyecto de Google Cloud al mismo tiempo | Funciona (2 × 11 min reales sin huecos, `qa/out/smoke-b5r.log`), pero el 25/09 observamos que los atascos del server son más frecuentes con dos sesiones simultáneas de la misma key que con una (`reportes/video/vivo/`, `reportes/doble/`: cadencia de envío nuestra a tiempo, offset del server sin avanzar). Mitigaciones: escalonar el arranque de las salas 20–30 s, una key o proyecto por sala (ver Cómo escalar), y el watchdog que reabre. | `python -m worker.cadencia <casete>` (mide si el audio salió a tiempo) |
| Se acaba la cuota o falla la key | El worker no falla en silencio: si falta la key corta con un mensaje claro (`GEMINI_API_KEY no esta definida en .env ni en el entorno`); ante un envío o una conexión que fallan, registra el error y, si no logra reabrir, termina. El sistema puede arrancar igual en modo replay, rotulado como tal, para pruebas y demos; para producción, nivel pago de Gemini o repartir sesiones entre varios proyectos (estimación de costo en `docs/costos.md`). Por diseño: no se forzó una cuota agotada real en esta vibeathon. | `worker/gemini.py` (función `api_key`); `worker/session.py` (`_caida`, `_SinReabrir`); `ops/entrypoint-worker.sh` (cae a replay si falta la key); `docs/costos.md` |
| Stream virtual sin traducción propia (idea del staff: vMix) | Una fuente de navegador POR IDIOMA, `?modo=obs&lang=xx`, fondo transparente sobre el video, dos líneas abajo. | `reportes/obs-transparencia.md` y captura `reportes/obs-transparencia.png` (verificado sobre video real en OBS el 25/09) |

### Qué se conserva de la operación actual

De lo que el staff describió que ya funciona bien en la sala real (pantallas frente al escenario, QR,
arranque simple, sin un operador dedicado mirando la mini PC) esto se mantiene o se refuerza:

- **Pantallas frente al escenario:** siguen siendo sólo una URL (`?modo=proyeccion`), texto sin el
  resto de la interfaz — nada nuevo que instalar en esas pantallas.
- **QR para el público:** cada sesión tiene el suyo desde el índice; sala e idioma quedan en la URL,
  sin instalar nada ni loguearse.
- **Arranque en un comando:** `docker compose up` (con `.env` real) o `MODO=replay docker compose up`
  (sin credenciales) levanta hub, vista y panel juntos.
- **Nadie tiene que quedarse mirando la mini PC:** como la sesión con Gemini se reabre sola (fila de
  arriba), no hace falta escritorio remoto ni F5 manual cuando se traba; quien monitorea lo hace desde
  el panel remoto con token (`/panel/`, sección "Panel de monitoreo" más abajo), no parado junto al
  escenario.

**Latencia en producción (nivel pago):** con `TRADUCTOR_RPM`/`TRADUCTOR_TOPE_RPM` en los límites del proyecto y `VENTANA_S=2`, el original llega ~2,3 s después de que el orador empieza la frase (medido) y el traducido ~3,5–4,7 s (estimación con la llamada al modelo medida); detalle en [`docs/evidencia.md`](docs/evidencia.md), sección 1c.

## Cómo escalar a más sesiones (R21, C3)

Pensado para desplegarse con **nivel pago** de Gemini (o Vertex AI): los límites del nivel gratuito
no son el techo de diseño, van como nota al final. Dos cuellos de botella son independientes entre sí
y se atacan por separado: cuántas salas transcriben a la vez, y cuánta gente mira cada sala.

### La receta para N salas: `ops/salas.py`

Un proceso `worker.run` por sala, supervisado:

```bash
python -m ops.salas ops/salas.ejemplo.json --hub ws://localhost:8080/ingest --escalon-s 20
```

Lee un JSON con la lista de salas (id, título, idioma, fuente —archivo/URL/mic—, `key` opcional para
repartir salas entre proyectos de Google Cloud, vocabulario opcional; formato completo en
[`ops/salas.ejemplo.json`](ops/salas.ejemplo.json)) y por cada una lanza un `worker.run` con:

- **arranque escalonado** (`--escalon-s`, 20 s por default: ver "por qué 20 s" abajo);
- **reinicio automático con backoff** (1, 2, 4… s) si un worker muere solo, hasta `--reintentos`
  (default 5) por sala — no reinicia si el worker terminó con `exit 0`;
- **logs por sala** en `--logs-dir/<id>.log`;
- **parada limpia**: Ctrl+C o `SIGTERM` para el supervisor manda la señal a TODOS los workers y
  espera a que cierren antes de salir;
- `--dry-run` imprime los comandos sin ejecutar nada; `--transporte casete:<archivo.jsonl>` (y
  `--traducir-a none`) prueban el supervisor entero SIN gastar cuota (tabla de abajo, fila "10 salas
  sin API").

**Por qué 20 s de escalón.** En los pares de salas que vimos trabarse en esta vibeathon, las dos
arrancaron en el mismo segundo y la traba del server empezó en los primeros segundos de la conexión
(`worker/README.md`, "Varias salas a la vez: cuando el server se traba"); escalonar el arranque evita
que dos sesiones atraviesen ese tramo inicial juntas, a costo cero (la sala siguiente empieza a
transcribir 20 s más tarde). El mismo patrón aparece, de forma independiente, en la evidencia de
terceros de abajo: sesiones creadas a ~1 s de distancia tardaron 10–12 s en la primera respuesta,
contra 4–5 s cuando estaban espaciadas 12 s o más.

Para **decenas de salas**: nivel pago de Gemini o Vertex AI / Firebase AI Logic (límites publicados
abajo), repartir sesiones entre varios proyectos de Google Cloud si hace falta (`worker.run --key
GEMINI_API_KEY_B ...`, el valor es el NOMBRE de la variable en `.env`; ya implementado y pasa por
`ops/salas.py` vía el campo `key` de cada sala) y **una prueba de carga PROPIA antes del evento** con
el modelo y la carga reales: ni el número de Google ni el de un tercero en un foro se sostienen sin
volver a medir con lo que uno va a correr.

### Límites publicados (terceros, no medidos por nosotros)

- **Vertex AI / Firebase AI Logic** (Live API), límites oficiales de Google:
  ["1,000 concurrent sessions per Firebase project"](https://firebase.google.com/docs/ai-logic/live-api/limits-and-specs)
  y "4M tokens per minute" (misma página). La Gemini Developer API (la que usa este proyecto, con
  `GEMINI_API_KEY`) no publica ese número: remite a los límites por nivel de la vista de rate limits
  de AI Studio (ver la nota de nivel gratuito al final).
- **Evidencia de terceros** (foro oficial de Google AI para desarrolladores; no es documentación de
  Google ni una medición nuestra, la citamos porque es la única medición pública de concurrencia real
  contra la Live API que encontramos):
  - [Gemini 3.5 Live: measured concurrency, silent stalls (discuss.ai.google.dev, hilo 180489)](https://discuss.ai.google.dev/t/gemini-3-5-live-translate-preview-in-production-paid-tier-measured-concurrency-dashboard-409s-vs-real-409s-silent-stalls-and-session-birth-degradation-data-6-questions/180489):
    60 sesiones concurrentes en un proyecto durante 10 min; congelamientos silenciosos de 8 a 51 s con
    el socket todavía abierto; sesiones creadas a ~1 s de distancia tardaron 10–12 s en la primera
    respuesta, contra 4–5 s cuando estaban espaciadas 12 s o más (de ahí el escalonado de arriba).
  - [Gemini Live API Tier 2 project still limited to 50 concurrent connections (discuss.ai.google.dev, hilo 94634)](https://discuss.ai.google.dev/t/gemini-live-api-tier-2-project-still-limited-to-50-concurrent-connections-and-billed-as-tier-1/94634):
    un proyecto Tier 2 reporta quedar limitado en la práctica a 50 conexiones concurrentes, facturado
    igual como Tier 1.

### Lo nuestro, medido, con su comando

| Qué | Resultado | Comando |
|---|---|---|
| **5 salas reales** (ASR real, 2 proyectos/keys, 25/09 08:02 AR) | las 5 conexiones Live abrieron y transcribieron (ningún `1011` por cupo); traducción 78 % / 74 % / 50 % en las 3 salas que compartían key, 100 % en las 2 con key propia | [`docs/evidencia.md`](docs/evidencia.md), sección "1b. Cinco salas reales en simultáneo" |
| **10 salas SIN API** (`ops/salas.py`, transporte de casete, sin traducción) | las 10 aparecen en `/api/sesiones`; matar un worker a mano (`Stop-Process`/`kill`) lo reinicia solo con backoff, log de `intento 1` y `intento 2` en su archivo | `python -m ops.salas <10 salas, formato de ops/salas.ejemplo.json> --hub ws://127.0.0.1:PUERTO/ingest --transporte casete:fixtures/casetes/evidencia-25-09/video-vivo-en-050903.jsonl --traducir-a none --escalon-s 2` (CPU/RAM y el log de reinicio en el reporte del bloque, no versionado por tamaño — ver "Verificación" más abajo) |
| **20 sesiones en modo replay** | sin problemas de memoria del hub | `qa/smoke.py --sesiones 20 --replay <lista de .jsonl de fixtures/casetes/ separados por coma> --tag veinte-salas` |
| **1000 espectadores en un hub** | sin pérdidas ni cierres | `.venv/Scripts/python -m hub.carga --hub http://localhost:8100 --clientes 1000` |

El hub hace fan-out por sesión + idioma con una cola propia por cliente (uno lento o colgado no afecta
a los demás: se lo desconecta con `1013` y reconecta solo) y no gasta cupo de Gemini — es tráfico
entre el hub y los navegadores, no llamadas a la API. El **modo replay** (`fixtures/casetes/`, `python
-m worker.replay`, lo mismo que usa `docker compose up` sin credenciales) reproduce sesiones grabadas
con sus tiempos originales para mostrar muchas salas sin tocar la API; siempre rotulado `replay: true`
en `/api/sesiones`, nunca presentado como ASR en vivo.

El hub es **un único proceso Python en un núcleo** (estado en memoria, sin forma de repartirlo entre
CPUs ni entre máquinas): ese, y no la API de Gemini, es el techo de espectadores/salas por hub. Receta
para pasarlo (no implementada en esta entrega): un **hub por grupo de salas** — las salas son
independientes entre sí (cada una es su propio `session_id`), así que repartirlas entre varios hubs
(por ejemplo detrás de un balanceador que rutee por sala) no requiere tocar el protocolo; sólo hace
falta cuando una sola sala pasa de ~1000 espectadores o el número de salas satura un solo proceso.

Además, cada sesión Live se **rota sola, de forma transparente para la audiencia**, por cinco causas
distintas: el servidor cierra la conexión, queda "atascada" (atraso sostenido o muda, sin texto por
`MUDO_S`), preventivamente a los `ROTACION_PREVENTIVA_S` (240 s) de audio enviado (antes de que el
servidor la corte solo), el servidor manda `GoAway`, o un envío de audio se traba más de
`ENVIO_TIMEOUT_S` (5 s: un envío colgado que nunca lanza error también cuenta como conexión muerta).
En los cinco casos el worker reabre una conexión nueva con solape y sigue emitiendo con el mismo
`seq` continuo — manejo del ciclo de vida de la conexión, **nunca presentado como una falla del
proveedor**. Corrida real de referencia (2 × ~11 min, mismo comando citado en "Reproducir los
números de R21"): 5 rotaciones (3 en la sesión EN, 2 en la ES; todas preventiva o cierre en esa
corrida en particular), cobertura 1,0 en las dos sesiones, sin ningún tramo con voz y sin texto.

> **Para probar con el nivel gratuito** (no es el despliegue recomendado, sirve para desarrollar sin
> pagar): esta cuenta midió **7 sesiones Live concurrentes** antes del evento (medición propia, no
> documentada por Google, puede cambiar) y, con el tope propio de 12 llamadas/modelo/60 s del
> traductor, **2 salas por proyecto** traducen completo con los lotes por default (3 con lotes más
> grandes, `TRADUCTOR_LOTE_MAX=3 TRADUCTOR_LOTE_S=6`, ~2 s más de atraso). Los valores concretos del
> nivel gratuito (RPM/RPD/TPM) no están publicados en ninguna documentación: son los vistos en la
> vista de límites de AI Studio de esta cuenta el 24/09/2026. Receta completa (reparto de salas y
> keys por nivel gratuito) en [`worker/README.md`](worker/README.md#cómo-llegar-a-5-y-10-salas).

## Panel de monitoreo (R8e)

`http://localhost:8080/panel/` (con `docker compose` o `python -m hub --web web --panel panel`):
estado por sesión, p50/p95 de latencia (nearest-rank, no promedio), rotaciones por motivo (cierre /
atasco / preventiva / `GoAway`) con el audio perdido acumulado, y "sin texto hace N s". Pide
`Authorization: Bearer <HUB_TOKEN>` contra `GET /api/metricas`: cuando el hub sirve el panel directo
(como en `docker compose`, sin el proxy de desarrollo `panel/servir.py`), la propia página tiene un
campo para pegar el token, que queda guardado en el navegador (`sessionStorage`) y se manda en cada
pedido; sin token el chip queda en "métricas: sin token" en vez de fallar en silencio.

## Export SRT / VTT / TXT (R8d)

```bash
.venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/b1-en-60s-rederivado-trad.jsonl --formato srt
.venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/b1-en-60s-rederivado-trad.jsonl --formato vtt --lang es
.venv/Scripts/python docs/export/exportar.py --hub http://localhost:8080 --sesion sala-1 --formato txt
```

## Verificación

Comprobaciones de punta a punta, cada una con su comando (detalle completo de cada corrida en los
reportes de bloque del equipo, no versionados por tamaño — lo reproducible es el comando):

| Qué | Comando |
|---|---|
| SMOKE + ROTACIÓN (2 sesiones reales ~11 min, 5 rotaciones, cobertura 1,0) | el de "Reproducir los números de R21" arriba (`qa/smoke.py --sesiones 2 --duracion 580 ...`) |
| WATCHDOG (reapertura por mudez, sin API) | `.venv/Scripts/python qa/e2e_watchdog.py` |
| RECONEXIÓN (hub reiniciado, la audiencia recupera el backlog) | `.venv/Scripts/python qa/reconexion.py` |
| CLON LIMPIO (repo público → carpeta nueva, sólo siguiendo este README) | `git clone <repo> carpeta-nueva && cd carpeta-nueva && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt && MODO=replay docker compose -f ops/docker-compose.yml up -d --build` y verificar `/`, `/api/sesiones`, `/panel/` |
| SIN API KEY (replay, sin `.env` en la raíz) | `MODO=replay docker compose -f ops/docker-compose.yml up -d --build` (sección "Cómo levantar") |
| COMPOSE CON KEY (ASR real dentro de Docker) | `.env` con `GEMINI_API_KEY` real + `MODO=real docker compose -f ops/docker-compose.yml up -d --build`; confirmar `replay:false` en `GET /api/sesiones` |

`qa/smoke.py`, `qa/e2e_watchdog.py` y `qa/reconexion.py` tienen su propio `--help`.

## Más documentación

- [`PROMPTS.md`](PROMPTS.md) — cómo se construyó este proyecto con agentes durante la vibeathon,
  qué se investigó antes (permitido, R12) y qué se escribió durante (obligatorio, R11a/R11b); es la
  respuesta preparada a X2.
- [`docs/evidencia.md`](docs/evidencia.md) — las mejores corridas reales (R21 2 × 580 s, R19), las tomas del video y
  las corridas fuera de la media con su causa medida; cada número con su comando. Casetes en `fixtures/casetes/evidencia-25-09/`.
- [`docs/video/r14/`](docs/video/r14/README.md) — subtítulos del video hechos con el propio traductor (R14): scripts, entradas y SRT.
- [`worker/README.md`](worker/README.md) — CLI completa del worker (`worker.run`, `worker.replay`, importador, cuota, medición de traducción), variables de entorno y formato del casete de tres capas.
- `python -m ops.salas --help` y [`ops/salas.ejemplo.json`](ops/salas.ejemplo.json) — supervisor de N salas (ver "Cómo escalar" arriba): arranque escalonado, reinicio con backoff, `--dry-run`, `--transporte casete:...` para probar sin API.
- [`docs/costos.md`](docs/costos.md) — estimación de costo por minuto de audio y por sesión.
- [`hub/README.md`](hub/README.md) y [`contracts/README.md`](contracts/README.md) — protocolo y
  contrato de mensajes entre worker, hub y la vista.
- [`fixtures/audio/FUENTES.md`](fixtures/audio/FUENTES.md) — de dónde salen los audios de prueba.
- [`docs/video/`](docs/video/) — script reproducible que compone el video de entrega (R13a/R13b) a
  partir de grabación real + narración + subtítulos exportados con el propio proyecto (R14).

## Limitaciones conocidas

- El hub no limita todavía clientes por IP ni sesiones por productor autenticado (`hub/README.md`,
  "Seguridad y límites conocidos"); en un evento real ponelo detrás de un proxy inverso con límites
  por IP y TLS (el token viaja en claro por `ws://`). El token débil (`dev-token`, vacío o < 16
  caracteres) ya no es sólo una recomendación: el hub directamente no arranca con él fuera de
  `127.0.0.1`/`localhost` (ver "Con Docker Compose" arriba).
- **Un reinicio del hub pierde las traducciones ya entregadas** (el hub sólo guarda estado en
  memoria): el worker reenvía sus `text` (llegan con `translations: {}`) pero no vuelve a mandar las
  `translation` que ya había entregado antes del reinicio. Quien ya estaba mirando conserva las que
  tenía; quien entra después, hace backfill de ese tramo, o exporta desde el hub (no desde el
  casete), los ve sin traducir (detalle y números medidos en `hub/README.md`, "Seguridad y límites
  conocidos"). No aplica al modo replay reproducido de nuevo, ni a exportar directamente desde el
  casete (`docs/export/exportar.py --casete ...`).
- `hub/tests/test_fanout.py` (200 y 500 clientes) colgaba la noche de la vibeathon por dos defectos del propio test
  (cola de 10 mensajes que atrapaba al cliente vivo; mensajes de 62 KB que el contrato rechaza); corregido el 25/09, la
  suite del hub pasa completa (63, con los 22 nuevos de seguridad).
- Fuente de micrófono implementada pero no verificada en esta máquina (OBS retenía el dispositivo).

## Licencia

Apache 2.0. Ver [LICENSE](LICENSE). El repositorio es público y Nerdearla puede usarlo, adaptarlo y
forkearlo respetando la licencia (R25).
