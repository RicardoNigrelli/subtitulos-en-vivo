# worker/ — ingesta, ASR en vivo, traducción y casetes

Una sala = un proceso `worker.run`: audio (archivo, URL o stream que ffmpeg entienda) → PCM s16le
16 kHz mono en chunks → ventanas con turnos manuales → Gemini Live (ASR) → mensajes del contrato
(`contracts/`) al hub por WebSocket, con `seq` propio. El texto final se traduce con un modelo de
texto (EN→ES y ES→EN). Todo lo que pasa queda en un **casete** JSONL.

Todos los comandos, desde la raíz del repo y con el venv (`.venv/Scripts/python` en Windows,
`.venv/bin/python` en Linux). Las ayudas de abajo son la salida de `python -m <módulo> --help`.

## Qué es real y qué es replay

| Camino | Habla con Gemini | Rótulo en el bus | Sirve para |
|---|---|---|---|
| `worker.run` (transporte `gemini`, el default) | **sí** (ASR + traducción) | `replay: false` | R17a, R18, R19, R21 |
| `worker.run --transporte casete:<x.jsonl>` | no | `replay: true`, `meta.source: "transporte-casete"`, título con `[TEST] ` | probar rotación / watchdog / dedup sin API |
| `worker.replay <casete>` | no | `replay: true`, título con `REPLAY` | demo y carga sin key; NO cuenta para R17a ni R21 |
| `worker.traducir_casete` | sí (sólo texto) | `translation.meta.source.offline: true` | agregar traducciones reales a un casete grabado |

Verificar el rótulo: `grep -n "SOURCE_REPLAY\|\[TEST\] \|REPLAY" worker/transporte_casete.py worker/session.py worker/replay.py`.

## `python -m worker.run` — una sala en vivo

```
--archivo ARCHIVO     archivo de audio (obligatorio con --fuente archivo, el default)
--fuente F            archivo | url | mic (ver "Fuentes en vivo")
--dispositivo NOMBRE  dispositivo de captura para --fuente mic (dshow en Windows)
--listar-dispositivos lista los dispositivos de audio de dshow y sale
--agenda AGENDA       agenda JSON (R8c): el glosario de la charla se SUMA a --vocab
--charla ID           id de la charla en la agenda (default: --sesion)
--sesion SESION       session_id público de la sala (obligatorio)
--lang {en,es}        idioma de origen (obligatorio)
--inicio INICIO       segundo de la fuente donde arranca (default 0)
--duracion DURACION   segundos de fuente a enviar (default: toda)
--hub HUB             ws://host:puerto/ingest (sin --hub no publica; igual graba el casete)
--casete CASETE       ruta del casete JSONL a grabar
--titulo TITULO       título de la sala (session_start.meta.title)
--url URL             con --fuente url: la entrada (stream); si no, sólo se anota en el casete
--vocab VOCAB         custom_vocabulary, separado por comas (default "Nerdearla")
--modelo MODELO       modelo Live (default: GEMINI_LIVE_MODEL)
--tope-envio-s S      máximo de segundos de audio a enviar en esta corrida
--key KEY             NOMBRE de la variable con la key (default GEMINI_API_KEY)
--ventana-s S         ventana objetivo del cortador
--gap-s S             gap entre activity_end y el siguiente activity_start
--drenaje-s S         espera de turnos pendientes al fin de la fuente
--traducir-a X        es | en | none (auto: en→es, es→en)
--timeout-trad-s S    timeout por llamada del traductor
--vad-auto            VAD automático del server (sin turnos manuales; sólo para el A/B)
--sin-reabrir         desactiva la reapertura con solape (una sola conexión)
--transporte T        gemini | casete:<archivo.jsonl>[:mudo=S]  (test, rotulado, sin API)
```

Defaults exactos: `python -m worker.run --help` y `grep -n "add_argument" worker/run.py`.

Ejemplo real (gasta cuota de audio):

```
.venv/Scripts/python -m worker.run --archivo fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav \
  --sesion demo-en --lang en --duracion 60 --casete fixtures/casetes/demo-en.jsonl \
  --hub ws://localhost:8100/ingest
```

**Presupuesto de audio.** Con el transporte `gemini`, antes de arrancar `worker.run` suma los
segundos de `CUOTA_LOG` desde `CUOTA_BLOQUE_DESDE` y los compara con `CUOTA_BLOQUE_S`: si no queda
saldo sale con exit 4 sin conectar; si queda, el tope de envío es el menor entre `--tope-envio-s` y
el saldo. Al terminar apende una línea `fecha-hora | sesión | segundos_enviados | archivo` a
`CUOTA_LOG`. Con `--transporte casete:` no se mira ni se registra cuota. Ver
`sed -n '66,80p;140,150p' worker/run.py`. Para abrir el presupuesto de un bloque nuevo:

```
CUOTA_BLOQUE_DESDE="2026-09-25 06:00:00" CUOTA_BLOQUE_S=600 .venv/Scripts/python -m worker.run ...
```

**Transporte de casete (test, sin API).** `--transporte casete:<archivo.jsonl>` reproduce las líneas
`server` de un casete real sincronizadas con el audio que el worker va enviando (ver el docstring de
`worker/transporte_casete.py`). `:mudo=S` deja muda a la conexión activa al cruzar S segundos de
audio enviado, para ejercitar el watchdog. Ejemplo:

```
.venv/Scripts/python -m worker.run --archivo fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav \
  --sesion prueba-mudo --lang en --duracion 60 --traducir-a none \
  --transporte casete:fixtures/casetes/b1-en-60s.jsonl:mudo=20 --casete $TEMP/prueba-mudo.jsonl
```

Con `--hub ws://localhost:8100/ingest` se ve en la vista, rotulada `[TEST] ` (el índice la oculta).

## Fuentes en vivo (R17a): `--fuente archivo|url|mic`

`worker/ingesta.py` (`opciones_fuente`) arma el comando de ffmpeg; siempre sale PCM s16le 16 kHz mono
en chunks de 100 ms, sin normalizar. El comando exacto queda en el stderr (`[run] fuente ...`) y la
fuente en el casete y en `session_start.meta.source` (`kind`, `device`, `agenda`).

| Fuente | Entrada | Qué agrega el worker |
|---|---|---|
| `archivo` (default) | `--archivo x.wav` | `-ss --inicio`, `-t --duracion`; ritmo real simulado |
| `url` | `--url <URL>` (o `--archivo <URL>`): HTTP, HLS (`.m3u8`), RTMP, SRT, UDP/mpegts, lo que ffmpeg abra | HTTP(S): `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5`. Un stream en vivo ya llega a ritmo real; un archivo por HTTP se pauta igual que un archivo |
| `mic` | `--dispositivo "<nombre>"` | Windows: `-f dshow -audio_buffer_size 50 -i audio=<nombre>`; macOS `avfoundation`; Linux `pulse` |

```
# stream: un encoder (OBS, mezcladora, ffmpeg) manda mpegts por UDP y el worker lo toma
ffmpeg -re -i charla.wav -f mpegts udp://127.0.0.1:9000 &
.venv/Scripts/python -m worker.run --fuente url --url udp://127.0.0.1:9000 --sesion sala-1 --lang en --hub ws://localhost:8100/ingest
# HTTP: ffmpeg -re -i charla.wav -listen 1 -f wav http://127.0.0.1:9001   y   --url http://127.0.0.1:9001
# micrófono (Windows):
.venv/Scripts/python -m worker.run --listar-dispositivos
.venv/Scripts/python -m worker.run --fuente mic --dispositivo "Micrófono (HD Pro Webcam C920)" --sesion sala-1 --lang es
```

Probar sólo la ingesta, sin API: agregar `--transporte casete:fixtures/casetes/b1-en-60s.jsonl
--traducir-a none` (el resumen JSON final trae `segundos_enviados` y `ventanas`; el casete trae el
RMS de cada ventana en las líneas `client`/`ventana`).

## Glosario desde la agenda (R8c): `--agenda`

`fixtures/agenda.json` (formato completo en el docstring de `worker/glosario.py`):

```
{"evento": "...", "terminos_evento": ["Nerdearla"],
 "sesiones": [{"id": "booch-en", "titulo": "...", "orador": "Grady Booch", "empresa": "IBM",
               "idioma": "en", "url": "...", "resumen": "...", "tecnologias": ["UML", ...],
               "terminos": ["Jim Rumbaugh", ...], "nota_terminos": "..."}]}
```

`python -m worker.glosario fixtures/agenda.json [--charla ID]` imprime el vocabulario por charla:
términos del evento, orador (completo y apellido), empresa, tecnologías, términos, y nombres propios
del título y del resumen (siglas, CamelCase, secuencias Capitalizadas), sin repetir, hasta 40.
`worker.run --agenda fixtures/agenda.json --charla booch-en` lo manda como `custom_vocabulary` al
conectar (sumado a `--vocab`; queda en la cabecera del casete, `config`).

Medir un A/B: `python -m worker.medir_glosario CASETE "Término 1,Término 2"` cuenta, sobre los `text`
emitidos, apariciones exactas (con mayúsculas y límites de palabra) y sin distinguir mayúsculas.
Exit 0 = medido; 1 = casete sin textos; 2 = uso.

## `python -m worker.replay` — modo replay rotulado

```
python -m worker.replay CASETE [--hub URL|none] [--sesion ID] [--velocidad X] [--salida x.jsonl]
```

Publica en el hub las líneas `emit` del casete con sus tiempos originales (divididos por
`--velocidad`). `--hub` por defecto: `HUB_URL`. `seq` = último `seq` que el hub tiene para esa
sesión + `seq` original (un segundo replay no rebobina). Exit 0 = todo entregado; 1 = quedó backlog
sin entregar; 2 = casete inválido. No genera texto propio: todo sale del casete.

## `python -m worker.traducir_casete` — traducción offline de un casete

```
python -m worker.traducir_casete CASETE --a {es,en} [--salida X] [--timeout-s S]
                                 [--sin-parciales] [--reintentos N]
```

Lee los `text` del casete, arma los lotes con la misma regla que el worker en vivo
(`worker.traductor.Lotes`) y hace llamadas reales al modelo de texto (cuentan en
`CUOTA_TEXTO_LOG`). Escribe `<nombre>-trad.jsonl` = original + `translation` intercalados con
`meta.source.offline: true`. Exit 0 = escrito; 2 = casete sin textos.

## `python -m worker.importar` — audios de prueba (R17b)

```
python -m worker.importar FUENTE [--slug X] [--inicio S] [--duracion D] [--format-id ID]
                          [--listar-formatos] [--clips-dir DIR] [--full-dir DIR]
```

`FUENTE` = URL (yt-dlp como módulo del venv, pista "original (default)") o archivo local. Corta
`[inicio, inicio+duración]` a WAV PCM s16le 16 kHz mono en `fixtures/audio/clips/`, sin normalizar.
Una URL ya bajada no se vuelve a bajar. Exit 0 = clip escrito; 1 = falló la descarga o el corte;
2 = uso.

## `python -m worker.cuota` — minutos de audio gastados

```
python -m worker.cuota [--desde "AAAA-MM-DD HH:MM:SS"]
```

Suma `CUOTA_LOG` desde `--desde` (default `CUOTA_BLOQUE_DESDE`), total y por sesión. Los segundos
son chunks enviados × duración del chunk, e incluyen el reenvío de solape: es lo que se manda a la
API.

## `python -m worker.medir_traduccion` — R19 desde casetes

```
python -m worker.medir_traduccion CASETE [CASETE ...]
```

Por casete: `text` emitidos, cuántos tienen una traducción `ok:true`, atraso p50/p95/máx (rango
más cercano) entre el `text` y su primera traducción ok, e intentos por estado. Exit 0 = calculado;
1 = algún casete sin textos; 2 = sin argumentos.

## Variables de entorno

Defaults verificables con `grep -nE "environ|_env_f\(|_env_num\(" worker/*.py`. El `.env` de la
raíz lo cargan `worker/gemini.py` (al pedir la key), `worker/emisor.py` y `worker/traductor.py`.

| Variable | Default | Qué controla |
|---|---|---|
| `GEMINI_API_KEY` | (obligatoria para el camino real) | key de Gemini; `--key` cambia el NOMBRE de la variable (p.ej. `GEMINI_API_KEY_RESERVA`) |
| `GEMINI_LIVE_MODEL` | `gemini-3.5-transcribe-live` | modelo de ASR |
| `GEMINI_TEXT_MODEL` / `GEMINI_TEXT_MODEL_ALT` | `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` | modelos de traducción (rotan entre sí) |
| `GEMINI_SSL_RELAX` | no definida | `1` relaja SÓLO `VERIFY_X509_STRICT` (Avast + Python 3.13); la cadena se sigue verificando |
| `HUB_TOKEN` | `dev-token` | token de ingesta worker→hub (cambiarlo en un evento) |
| `HUB_URL` | `ws://localhost:8100/ingest` | hub por defecto de `worker.replay` |
| `CUOTA_LOG` | `reportes/cuota-audio.log` | registro de audio enviado |
| `CUOTA_BLOQUE_S` | `1800` | presupuesto de audio del bloque, en segundos |
| `CUOTA_BLOQUE_DESDE` | `2026-09-24 13:00:00` | desde cuándo se suma el bloque |
| `CUOTA_TEXTO_LOG` | `reportes/cuota-texto.log` | una línea por llamada al modelo de texto |
| `CUOTA_TEXTO_RESERVAS` | `reportes/cuota-texto-reservas.jsonl` | reservas de llamadas al modelo de texto, compartidas entre procesos (ver abajo) |
| `TRADUCTOR_LOTE_MAX` / `TRADUCTOR_LOTE_S` | `2` / `4` | lote del traductor: N ventanas o S segundos desde la primera pendiente |
| `TRADUCTOR_RPD_TOPE` | `480` | tope diario de llamadas por modelo de texto |
| `TRADUCTOR_FALLAS_CORTE` | `3` | fallas seguidas (5xx, timeout, 429) que sacan a un modelo de la rotación |
| `TRADUCTOR_CORTE_S` / `TRADUCTOR_CORTE_MAX_S` | `60` / `480` | duración del primer corte y tope de la duplicación |
| `ATASCO_UMBRAL_S` / `ATASCO_SOSTENIDO_S` | `22` / `8` | reapertura por atraso del server |
| `MUDO_S` | `10` | reapertura por ventanas con voz sin ningún texto (watchdog) |
| `ROTACION_PREVENTIVA_S` | `240` | reapertura preventiva por audio enviado a una conexión |
| `DRENAJE_VIEJA_S` | `20` | cuánto drena la conexión vieja tras reabrir |
| `REENVIO_MAX_S` | `15` | tope de audio que se reenvía a la conexión nueva |
| `ENVIO_TIMEOUT_S` | `5` | un envío trabado más que esto da la conexión por muerta y reabre |

## Llamadas al modelo de texto entre procesos (`cuota-texto-reservas.jsonl`)

Cada sala es un proceso; el cupo de texto (15 RPM por modelo) es por proyecto. Antes de CADA
llamada, el limitador (`worker.traductor.Reservas`) toma un lock de archivo
(`cuota-texto-reservas.jsonl.lock`: `msvcrt` en Windows, `fcntl` en Linux), cuenta las reservas de ese
modelo con `t` en los últimos 60 s (de todos los procesos) y, si hay menos que el límite (12, o 14 con
un solo modelo sano), apenda una línea y suelta el lock; si no, prueba el otro modelo o espera. Una
línea por llamada INICIADA (termine bien o mal):

```
{"t": 1790294443.926, "hora": "2026-09-24 21:00:43", "modelo": "gemini-3.1-flash-lite", "pid": 31936,
 "etiqueta": "", "en_ventana": 5, "limite": 12}
```

`en_ventana` = reservas de ese modelo en los 60 s previos, contando ésta. `reportes/cuota-texto.log`
no cambia (una línea por llamada TERMINADA: `hora | modelo | items | estado | ms`, más las líneas
`# cortacircuito`); de ahí sigue saliendo el tope diario. Tests: `pytest worker/tests/test_reservas_texto.py`
(dos procesos reales contra el mismo archivo).

## Formato del casete (tres capas)

Línea 1: cabecera `{"casete": 1, "session_id", "lang", "model", "config", "source", "cortador",
"generator", "replay_test", "started_at", ...}`. Después, una línea por evento
`{"t": epoch, "dir": ..., "kind": ..., "payload": {...}}` con `dir`:

- `server`: cada mensaje CRUDO del server tal cual llegó (`kind` = claves de primer nivel:
  `serverContent`, `voiceActivity`, `goAway`, `setupComplete`; `close` para el cierre del
  websocket con su código). Es la única evidencia de "Gemini dijo X".
- `client`: lo que hizo el worker: `connect`, `config`, `activity_start`, `ventana`, `activity_end`,
  `reabrir_pedido`, `send_error`, `drenaje`, `retiro`, `close`, entre otros.
- `emit`: cada mensaje del contrato publicado al bus (`payload` = el mensaje).

Se escribe con flush por línea: si el proceso muere, el casete vale hasta la última línea. Detalle:
docstring de `worker/casete.py`.

## Tests

```
.venv/Scripts/python -m pytest worker/tests -q
```

Sin API: usan casetes reales de `fixtures/casetes/` con el transporte de casete o transportes
falsos rotulados dentro de `worker/tests/`.
