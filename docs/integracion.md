# Guía de integración: probar la transcripción y conectarla con tus servicios

## English summary (for judges)

This project turns live audio into live captions: `worker.run` opens an audio source (mic, file, or
network stream), sends it to Gemini Live for real-time transcription, translates the final lines
(EN→ES, and ES→EN for free) with a text model, and streams contract messages (JSON, one per line) to
a `hub` process over WebSocket. The hub fans those messages out to: a web view per session+language
(`/s/<id>?lang=es`), a monitoring panel (`/panel/`), a public WebSocket (`/ws/<id>?lang=xx`), and an
HTTP history endpoint (`/api/sesiones/<id>/historial`). Everything below was run for real against a
local hub on port 8189 (0 minutes of Gemini quota spent: the ingest was a replayed recorded session)
— command, output and exit code for each step are in `reportes/demo-integracion.md`. Two entry points
to try it in 5 minutes: (a) `MODO=replay docker compose` — no credentials, replays recorded sessions;
(b) your own microphone with a Gemini API key. Five ways to consume the captions are documented below:
the web view, an OBS/vMix transparent overlay, the WebSocket API (Python tested, Node equivalent), the
HTTP API (`curl`, tested), and file export to SRT/VTT/TXT (tested, validated with `ffmpeg`).

---

Todo lo de abajo cita R#/comando; nada es un número inventado (skill `anti-alucinacion`). El detalle
completo (fuente, README y líneas citadas, comandos con salida y exit code) está en
`reportes/demo-integracion.md`.

## 1. Probarlo en 5 minutos

### a) Sin credenciales (modo replay)

```bash
MODO=replay docker compose -f ops/docker-compose.yml up -d --build
```

Abrí `http://localhost:8080/`, elegí una sala y un idioma: las sesiones vienen rotuladas
`replay: true` en `/api/sesiones` (no es ASR en vivo, no cumple R17a/R21 por sí solo, pero muestra
hub + vista + panel + fan-out completos sin costo ni key). No re-corrí Docker en este bloque (el
brief me pide no tocar 8100–8107 ni Docker/OBS de la sesión de video en curso); la verificación de un
clon limpio del repo público siguiendo sólo el README está en `README.md` §"Verificación", fila
CLON LIMPIO, y en `ESTADO.md` (fila "CLON LIMPIO 1"). [SUPUESTO: vigente; verificado el 25/09 por
ops/qa, no re-ejecutado por mí hoy].

### b) Con tu propia voz

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt   # una vez
cp .env.example .env   # y completar GEMINI_API_KEY
.venv/Scripts/python -m hub --web web --panel panel
.venv/Scripts/python -m worker.run --listar-dispositivos                 # ver nombres de micrófono
.venv/Scripts/python -m worker.run --fuente mic --dispositivo "<nombre>" --sesion mi-sala --lang es --hub ws://localhost:8100/ingest
```

Abrí `http://localhost:8100/s/mi-sala?lang=en` en el celular (misma red: servir la vista con
`--host 0.0.0.0`, o usar el QR del índice `http://localhost:8100/`). Medir que el micrófono entrega
señal: `worker/README.md` §"Fuentes en vivo" (RMS por ventana queda en el casete, líneas
`client`/`ventana`). Documentación existente (`README.md` §"Cómo levantar", §"Fuentes de audio en
vivo", §"Cómo encaja en una sala": "VERIFICADO de punta a punta el 25/09"); no la re-corrí porque
tocaría el hub 8100 que usa la sesión de video.

## 2. Conectar el audio de tu evento (entrada)

Diagrama (`worker/README.md` §"Cómo encaja en una sala"; `ops/salas.py` para varias salas):

```
consola/OBS/vMix o placa de audio        (una sala = un proceso worker.run)
        |  (cable 3,5mm o stream UDP/HTTP/RTMP/SRT)
        v
   worker.run --ASR + traduccion (Gemini)--> hub (WS /ingest) --fan-out--> audiencia / OBS / API
        |
        +-- casete .jsonl (graba todo lo que paso)
```

- **Placa/mic junto al escenario:** `worker.run --fuente mic --dispositivo "<nombre>"` — README
  §"Cómo encaja en una sala": **VERIFICADO de punta a punta el 25/09** (transcripción + traducción
  reales).
- **Desde OBS/vMix/una consola que emite un stream:** `--fuente url --url udp://...` — **UDP
  verificado con una corrida real de 30 s** (`reportes/audio-pipeline-b8.md`, citado en
  `worker/README.md` §"Fuentes en vivo"). SRT y RTMP: ffmpeg los soporta por el mismo camino
  (`worker/ingesta.py::opciones_fuente`), pero **no están en la evidencia de esta entrega** — marcalos
  como no verificados si los usás en tu evento.
- **Un video o transmisión existente:** `--fuente archivo` con una URL, o `--fuente url`
  (`README.md` §"Fuentes de audio en vivo").
- **Varias salas:** `python -m ops.salas ops/salas.ejemplo.json --hub ws://localhost:8080/ingest
  --escalon-s 20` (arranque escalonado, reinicio con backoff; flags leídas hoy de `ops/salas.py`
  líneas 269-279). Sin gastar cuota: agregar `--transporte casete:<archivo.jsonl> --traducir-a none`
  (tabla "10 salas SIN API" en `README.md` §"Lo nuestro, medido, con su comando").

## 3. Usar los subtítulos en tus servicios (salida)

Todo lo de esta sección lo corrí HOY (25/09) contra un hub PROPIO en el puerto **8189** (no toqué
8100-8107 ni Docker), con `HUB_TOKEN=tok-integracion-8189-prueba`, alimentado por un **replay** de un
casete grabado (`fixtures/casetes/evidencia-25-09/simple-en-053454.jsonl`, sesión `demo-en`; **0 min
de cuota de Gemini**, `replay:true`). Comando, salida completa y exit code de cada prueba están en
`reportes/demo-integracion.md`.

### Vista web por sala e idioma

`http://<host>/s/<sala>?lang=es` (link y QR desde el índice `http://<host>/`).
`?modo=proyeccion`: sólo texto, para la pantalla de la sala (`web/app.js`, sección activada
únicamente por ese parámetro de URL).

### Overlay en OBS/vMix

Fuente de navegador apuntando a `http://<host>/s/<sala>?lang=en&modo=obs` (fondo transparente, dos
líneas abajo; modo aparte de `proyeccion`, pedido R8a). Tamaño sugerido: el mismo de la escena/video.
Verificado sobre video real en OBS el 25/09 (`reportes/obs-transparencia.md` + `.png`, citado en
`README.md` §"Cómo encaja en una sala").

### API en vivo por WebSocket

`ws://<host>/ws/<sala>?lang=es`: el primer frame es `init` (últimas 10 líneas en `lines`, más
`state`, `last_seq`...), después mensajes en vivo (`text`, `partial`, `translation`, `rotation`...) y
un latido cada 1 s (`hub/README.md`; `hub/nucleo.py::suscribir`, `init.lines`).

Probado HOY contra `ws://127.0.0.1:8189/ws/demo-en?lang=es` (la sesión ya había terminado el replay,
por eso el único frame que llegó fue `init` con las últimas líneas guardadas; en una sesión en vivo
seguirían llegando `text`/`translation` después):

```python
import asyncio, json, sys
import websockets

async def main(url):
    async with websockets.connect(url) as ws:
        raw = await ws.recv()
        init = json.loads(raw)
        assert init["type"] == "init"
        print(f"init: session={init['session_id']} lang={init['lang']} state={init['state']} last_seq={init['last_seq']}")
        for line in init["lines"][-5:]:
            es = line.get("translations", {}).get("es", {}).get("text")
            print(f"[{line['seq']}] {line['text']!r} -> es: {es!r}")

asyncio.run(main(sys.argv[1]))
```

Salida real (`python ws_cliente.py "ws://127.0.0.1:8189/ws/demo-en?lang=es"`, exit 0):

```
init: session=demo-en lang=es state=ended last_seq=26
[21] 'engineering was to optimize the needs for the machine' -> es: 'engineering fue optimizar las necesidades para la máquina'
[22] 'not the humans themselves.' -> es: 'no los humanos en sí.'
[23] 'Well, with the advent of micro' -> es: 'Bueno, con la llegada de micro'
[24] 'For miniaturization, we had from the mainframes' -> es: 'Para la miniaturización, pasamos de los mainframes'
[25] 'we suddenly had minicomputers like' -> es: 'de repente tuvimos minicomputadoras como'
```

Snippet Node (Node 22 trae `WebSocket` global; mismo protocolo que el probado arriba en Python; no
re-ejecutado en Node en este bloque):

```js
const ws = new WebSocket("ws://<host>/ws/<sala>?lang=es");
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.type === "init") {
    for (const l of msg.lines) console.log(l.seq, l.text, "->", l.translations?.es?.text);
  } else if (msg.type === "text" || msg.type === "translation") {
    console.log(msg);
  }
};
```

### HTTP: índice e historial

```bash
curl -s http://<host>/api/sesiones
curl -s "http://<host>/api/sesiones/<id>/historial?desde=0&limit=1000"
```

Probado hoy (hub 8189, sesión `demo-en`), exit 0:

```
$ curl -s http://127.0.0.1:8189/api/sesiones
[{"session_id":"demo-en","lang":"en","title":"REPLAY - booch-295-405","replay":true,"last_seq":26,
"state":"ended","source":{"file":"reportes/video/vivo/booch-295-405.wav", ...},"test":false,
"translations_langs":["es"]}]

$ curl -s "http://127.0.0.1:8189/api/sesiones/demo-en/historial?desde=0&limit=3"
[{"v":1,"type":"text","session_id":"demo-en","seq":2,"lang":"en",
"text":"Ottaviani, Tom DeMarco and the like.",
"translations":{"es":{"text":"Ottaviani, Tom DeMarco y similares.","ok":true}}, ...}, ...]
```

(salida completa de los dos curl en `reportes/demo-integracion.md`).

### Archivos: export SRT/VTT/TXT

```bash
.venv/Scripts/python docs/export/exportar.py --hub http://<host> --sesion <id> --formato srt
.venv/Scripts/python docs/export/exportar.py --hub http://<host> --sesion <id> --formato vtt --lang es
.venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/x.jsonl --formato txt
```

Probado hoy contra el hub 8189, sesión `demo-en`: `srt` (24 líneas), `vtt --lang es` (24 líneas) y
`txt` (24 líneas), los tres `exit 0`. Validación de formato (comando de `README.md` §"Verificación"):

```bash
ffmpeg -i salida.srt -map 0:s -c:s copy -f null -   # exit 0 (probado hoy con el .srt de arriba)
ffmpeg -i salida.vtt -map 0:s -c:s copy -f null -   # exit 0 (probado hoy con el .vtt de arriba)
```

Ojo: `ffmpeg -i x.srt -f null -` SIN `-map 0:s -c:s copy` da "Output file does not contain any
stream" y sale con error (lo probé hoy también); usar siempre el comando completo de arriba, que es
el mismo que documenta el README.

### Panel de monitoreo

`http://<host>/panel/`. Pide `Authorization: Bearer <HUB_TOKEN>` contra `GET /api/metricas`. En
local, manual (`python -m hub`), el token es el que pusiste en el entorno/`.env` (o `dev-token` si no
pusiste nada — sólo válido en `127.0.0.1`/`localhost`, el hub no arranca con `dev-token` fuera de ahí).
Con Docker Compose, si `.env` no trae `HUB_TOKEN`, `token-init` genera uno y lo comparte con
`hub`/`worker`; para verlo: `docker compose -f ops/docker-compose.yml logs hub worker | grep
HUB_TOKEN` (`README.md` §"Con Docker Compose"). Probado hoy contra el hub 8189: `GET /api/metricas`
sin `Authorization` → `401`; con `Authorization: Bearer tok-integracion-8189-prueba` → `200` con los
contadores por sesión.

## 4. Seguridad al conectarlo

- **Token obligatorio para publicar (productores) y para métricas:** `/ingest` (worker→hub) y
  `GET /api/metricas` piden `HUB_TOKEN`; fuera de `127.0.0.1`/`localhost` el hub directamente **no
  arranca** si el token es `dev-token`, está vacío o tiene menos de 16 caracteres
  (`hub/config.py::revisar_token`, citado en `README.md` §"Con Docker Compose" y `hub/README.md`).
  Probado hoy: sin `Authorization`, `/api/metricas` devuelve `401`.
- **Lectura pública por diseño (audiencia):** `GET /api/sesiones`, `GET /.../historial` y
  `WS /ws/<id>` no piden token (`hub/README.md` §"Rutas").
- **Detrás de HTTPS si se expone a internet:** el token viaja en claro por `ws://`; en un evento real
  ponerlo detrás de un proxy inverso (nginx, Caddy, Cloudflare Tunnel) con TLS (`README.md` §"Uso en
  producción").
- **Nunca por query string:** ni `/ingest` ni el panel aceptan el token como `?token=...` (queda en
  los access logs); siempre en la cabecera `Authorization: Bearer <token>` (`README.md` §"Con Docker
  Compose"; `hub/README.md` §"Seguridad y límites conocidos").

## Qué no probé en este bloque (por las reglas del brief)

- No re-corrí `MODO=replay docker compose` (no tocar Docker/8080 mientras graba la sesión de video;
  cito la verificación existente del README/ESTADO en vez de repetirla).
- No probé SRT ni RTMP como fuente real (sólo UDP está en evidencia, ver arriba).
- No corrí el snippet de Node (mismo protocolo WebSocket que el de Python, ya probado arriba con
  salida real).
