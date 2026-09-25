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
| R21: dos sesiones con ASR real en simultáneo, ~11 min de audio cada una | cobertura 1,0 y 1,0 (sin huecos); latencia PERCIBIDA p50 ≈ 0,44 s (0,443 / 0,429 s), p95 ≈ 0,60 s (0,615 / 0,585 s) | ver "Reproducir los números de R21" abajo |
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

| Corrida | Lote | Cobertura traducida | Atraso p50 / p95 | Comando |
|---|---|---|---|---|
| 2 × 60 s | 2 ventanas / 5 s | 32/41 = 78 % | 12,3 s / 20,5 s | `worker.medir_traduccion fixtures/casetes/b4-trad-vivo-en.jsonl fixtures/casetes/b4-trad-vivo-es.jsonl` (arriba) |
| 2 × ~11 min, sin cortacircuito | 2 ventanas / 5 s | EN→ES 181/253 = 71,5 %; ES→EN 191/247 = 77,3 % | 9,6 s / 8,8 s | `worker.medir_traduccion` sobre el casete de la corrida larga (ver "Reproducir los números de R21") |
| 2 × 60 s, lotes más chicos | 2 ventanas / 4 s | EN→ES 26/26 = 100 %; ES→EN 22/22 = 100 % | 2,64 s / 2,68 s | `.venv/Scripts/python -m worker.medir_traduccion fixtures/casetes/b8-trad-vivo-en.jsonl fixtures/casetes/b8-trad-vivo-es.jsonl` |

La tercera corrida también coincidió con una latencia mucho mejor del modelo de texto en sí (no sólo
del lote más chico): no se puede atribuir la mejora completa a `TRADUCTOR_LOTE_S=4` con una sola
corrida por configuración. Es el número más reciente, no el "verdadero"; el rango completo es el
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

Levanta dos servicios (`hub` y `worker`) sobre una única imagen (`ops/Dockerfile`). El hub sirve TODO
en el `:8080`: índice de sesiones (`GET /`), la vista por sesión (`/s/<id>?lang=es`) y el panel de
monitoreo (`/panel/`). **El modo replay reproduce sesiones grabadas: no cumple R17a ni R21 por sí
solo** (no es audio en vivo); sirve para mostrar el pipeline completo (hub + vista + panel +
fan-out) sin credenciales ni costo (eje 3 de escalabilidad, ver más abajo).

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

## Cómo escalar a más sesiones (R21, C3) — tres ejes

La fuerza bruta no alcanza: hay tres cuellos de botella distintos y cada uno se resuelve distinto.

1. **Sesiones contra la API de Gemini.** Cada sesión de transcripción en vivo consume el cupo de
   *Transcribe Live* del proyecto de Google Cloud (nivel gratuito: 20 000 TPM). Antes de la
   vibeathon se midieron **7 sesiones concurrentes** con ese cupo; en esta entrega el MVP corrió
   **2 sesiones reales de ~11 min en simultáneo** (tabla de arriba). Para más salas que las que
   entran en el nivel gratuito: pasar a un **nivel pago** de Gemini, o repartir sesiones entre
   **varios proyectos de Google Cloud** (cada uno con su propia `GEMINI_API_KEY` y su propio cupo).
   El segundo modelo que traduce el texto (R19, no es la Live API) comparte cupo entre TODAS las
   sesiones de una misma key: 15 RPM por modelo en el nivel gratuito, con un tope propio de **12
   llamadas por modelo por minuto** repartido entre procesos mediante un archivo de reservas con
   lock (detalle en [`worker/README.md`](worker/README.md#llamadas-al-modelo-de-texto-entre-procesos-cuota-texto-reservasjsonl)).
   Verificado con 2 sesiones reales en paralelo: 26 reservas entre 2 procesos, máximo 12 por modelo
   en cualquier ventana de 60 s (nunca 13); con 3 o más salas por key, este límite —no el de audio—
   es el primero en exigir lotes más grandes o un nivel pago.
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
- [`worker/README.md`](worker/README.md) — CLI completa del worker (`worker.run`, `worker.replay`, importador, cuota, medición de traducción), variables de entorno y formato del casete de tres capas.
- [`docs/costos.md`](docs/costos.md) — estimación de costo por minuto de audio y por sesión.
- [`hub/README.md`](hub/README.md) y [`contracts/README.md`](contracts/README.md) — protocolo y
  contrato de mensajes entre worker, hub y la vista.
- [`fixtures/audio/FUENTES.md`](fixtures/audio/FUENTES.md) — de dónde salen los audios de prueba.
- [`docs/video/`](docs/video/) — script reproducible que compone el video de entrega (R13a/R13b) a
  partir de grabación real + narración + subtítulos exportados con el propio proyecto (R14).

## Licencia

Apache 2.0. Ver [LICENSE](LICENSE). El repositorio es público y Nerdearla puede usarlo, adaptarlo y
forkearlo respetando la licencia (R25).
