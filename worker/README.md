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
--traducir-a X        codigo ISO del idioma destino (es, en, pt...) | none | auto (en→es, es→en)
--timeout-trad-s S    timeout por llamada del traductor
--vad-auto            VAD automático del server (sin turnos manuales; sólo para el A/B)
--sin-reabrir         desactiva la reapertura con solape (una sola conexión)
--transporte T        gemini | casete:<archivo.jsonl>[:mudo=S]  (test, rotulado, sin API)
```

Defaults exactos: `python -m worker.run --help` y `grep -n "add_argument" worker/run.py`.

**Qué idiomas acepta cada flag.**

- `--lang {en,es}`: idioma de ORIGEN que recibe el modelo Live (`contracts/esquema.json#lang_origen`,
  congelado por R18 a español/inglés). Verificado HOY con ASR real: `en` y `es` (ver filas R17a+R18 y
  CHECKPOINT de `ESTADO.md`). Otros códigos: la API los rechazaría o el `argparse` los corta antes
  (`choices=["en", "es"]`); no probados, no se afirma que funcionen.
- `--traducir-a` / `worker.traducir_casete --a`: idioma DESTINO de la traducción, GENÉRICO por código
  ISO (`contracts/esquema.json#lang_destino`, patrón `^[a-z]{2}(-[A-Z]{2})?$`: `es`, `en`, `pt`,
  `pt-BR`...; no es una lista cerrada de `{es,en}`). `worker/traductor.py` (`IDIOMAS`) le da un nombre
  legible al prompt si lo conoce (`en`, `es`, `pt`) y usa el código tal cual si no; Gemini lo traduce
  igual. R19 obliga EN→ES; ES→EN sale gratis (R14); **`pt` verificado offline contra un casete real**
  (B10: `reportes/audio-pipeline-b10-pt.md`). Otros códigos ISO: no verificados con una llamada real,
  pero el camino no distingue idiomas, así que no hay motivo para que sólo fallen ellos.

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
| `archivo` (default) | `--archivo x.wav` | `-ss --inicio`, `-t --duracion`; ritmo real simulado; termina al acabar el archivo |
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

## Operación en sala: correr sin límite, parar, qué pasa si se cae el cable

Un proceso por sala, que queda escuchando hasta que alguien lo para.

**Correr sin límite.** Con `--fuente mic` o `--fuente url`, si no se pasa `--duracion` (o se pasa
`--duracion 0`), el worker no tiene fin propio: no se agrega `-t` a ffmpeg y el presupuesto de audio
del bloque (`CUOTA_BLOQUE_S`, una guarda de desarrollo) no lo corta ni le impide arrancar. Los segundos
enviados se registran igual en `reportes/cuota-audio.log` al terminar. `--tope-envio-s N` sigue
funcionando si se quiere un tope explícito. Con `--fuente archivo` termina al acabar el archivo.

```
.venv/Scripts/python -m worker.run --fuente mic --dispositivo "<nombre>" --sesion sala-1 --lang es --hub ws://localhost:8100/ingest
```

**Parar.** Ctrl+C en la consola (o Ctrl+Break, o `SIGTERM` en Linux) hace una parada
limpia: deja de leer la fuente, cierra la ventana abierta, espera hasta 8 s el último texto
(`PARADA_DRENAJE_S` en `worker/session.py`), cierra la conexión con Gemini en orden, manda
`session_end` con `meta.reason: "stop"` al hub, cierra el casete y sale con exit 0. Un segundo Ctrl+C
interrumpe sin esperar (no garantiza `session_end`). ffmpeg corre en su propio grupo de procesos para que el Ctrl+C le
llegue sólo al worker.

**Si se cae el cable o el stream.** Si ffmpeg termina o no entrega bytes durante `FUENTE_TIMEOUT_S`
(default 10 s), el worker:
1. manda al hub un `error` con `meta.code: "fuente"` y cierra la
   ventana abierta para que su texto llegue igual;
2. reintenta abrir la fuente con espera 1, 2, 4, 8… s, hasta `FUENTE_BACKOFF_MAX_S` (30 s), sin límite
   de intentos;
3. cuando vuelven los bytes sigue con la MISMA `session_id` y el `seq` continuo; el audio retoma la
   línea de tiempo donde quedó. La conexión con Gemini no se toca: se reabre sólo por sus propias
   causas (cierre del server, atasco, GoAway).

En el casete quedan las líneas `client` `fuente_caida` y `fuente_reabierta` (motivo, número de caída,
posición de audio) y `parada`. En el resumen JSON final, `caidas_fuente` y `motivo_fin: "stop"`.

`FUENTE_TIMEOUT_S` tiene que ser mayor que el sondeo inicial de ffmpeg: con UDP/mpegts, ffmpeg tarda
unos segundos en entregar el primer byte después de abrir (ver `worker/tests/test_sala.py`, que usa
7 s con las opciones de producción).

**Si la conexión nueva con Gemini también queda muda.** A veces pasa: en la corrida en vivo `vf2-en` la
conexión abierta tras un atasco no devolvió ningún texto en 40 s. No hay arreglo del lado nuestro. El
watchdog (`MUDO_S`, 10 s con voz y sin texto) o el atraso (`ATASCO_UMBRAL_S` sostenido `ATASCO_SOSTENIDO_S`)
vuelven a reabrir; cada atasco seguido duplica el sostenido (hasta 120 s) y se reenvían hasta `REENVIO_MAX_S`
(15 s) de audio. Si el atasco dura más que eso, el audio anterior queda sin texto (`rotation.meta.audio_lost_s`).

Medición en REPLAY (transporte de casete, no en vivo; `reportes/audio-pipeline-umbral.log`):

| Casete | Reaperturas 22/8 | Reaperturas 10/4 | Tramo más largo sin texto 22/8 | Tramo más largo sin texto 10/4 |
|---|---|---|---|---|
| en-quota (455 s) | 8 | 14 | 252,4 s (81,5 s en 0–200 s) | 255,3 s (72,4 s en 0–200 s) |
| es-cancelled (533 s) | 8 | 11 | 258,8 s (0,0 s en 0–200 s) | 249,8 s (6,5 s en 0–200 s) |
| es mudo desde 60 s (160 s) | 1 | 2 | 21,1 s | 24,4 s |

Los tramos de más de 200 s son el server que muere al final de esos casetes (cuota agotada, cancelado):
en replay la conexión nueva repite la misma falla, así que ningún umbral los arregla. Por eso se
mantiene 22 / 8: es el valor con el que se hicieron las corridas en vivo del proyecto; 10 / 4 no
mostró ventaja en esta medición.

Verificación sin API (0 min): `.venv/Scripts/python -m pytest worker/tests/test_sala.py -q`
(parada con señal simulada; fuente UDP real que se corta y vuelve; `worker.run` como proceso aparte
con `--transporte casete:`, corte y vuelta del stream UDP y señal real al final).

### Varias salas a la vez: cuando el server se traba (mitigaciones operativas)

Lo medido (`reportes/audio-pipeline-doble.md`, `python -m worker.cadencia <casete>`, sin API): con DOS
`worker.run` simultáneos, 4 de 4 salas con atasco tuvieron el envío sano hasta el primer pedido de
reapertura (el pautado de la fuente iba 0,013–0,016 s tarde como máximo) y el traductor ni había
reservado en la corrida 05:16. Lo que se trabó fue el server: en `sala-a-051619` y `sala-b-051619`, la
conexión c1 dio 17 y 31 mensajes, 0 textos, y el único `audioOffset` fue "0s" (nunca cerró el primer
turno); en la doble de 01:00 (`vf2-en`/`vf2-es`) el offset se congeló en 25,8 y 21,2 s en las dos salas
a la vez. La doble de ayer 16:50 (2 × 11 min, misma config enviada al server) y la de hoy 05:30 (2 × 60 s)
no se trabaron: es intermitente. No sabemos por qué pasa más con dos sesiones; lo de abajo es operación,
no está probado en vivo.

1. **Escalonar el arranque de las salas 20–30 s.** En los dos pares trabados, las dos salas
   arrancaron en el mismo segundo (05:16:20,755 / 05:16:20,772; 01:00:37,189 las dos) y la traba
   empezó en los primeros 3–27 s de la conexión (el pedido de reapertura, a +25–52 s). Arrancar la
   sala B 20–30 s después de la A evita que las dos atraviesen a la vez ese tramo inicial. Costo: ninguno (la sala B
   empieza a transcribir 20–30 s más tarde).
2. **Una key/proyecto de Google AI Studio por sala** (`--key NOMBRE_VAR`, p. ej. `GEMINI_API_KEY_B` en
   `.env`). `--key` aplica a las DOS llamadas de la sala: la Live API (conexión inicial y cada
   rotación) y el traductor de texto (`worker/run.py`: `armar_transportes` / `armar_traductor`; hasta el
   25/09 05:46 el traductor usaba siempre `GEMINI_API_KEY`; test `worker/tests/test_key_por_sala.py`).
   Las reservas de texto entre procesos (`reportes/cuota-texto-reservas.jsonl`) NO distinguen key:
   siguen siendo un tope conservador COMPARTIDO por todas las salas de la máquina (≤ 12 llamadas por
   modelo en 60 s entre todas; 14 con un solo modelo sano), aunque cada key tenga su propio cupo. Si la traba es capacidad o
   cola por proyecto, dos proyectos no comparten esa cola. Costo: una key más por sala y su propia
   cuota. [SUPUESTO: audio-pipeline] que la cola es por proyecto: no medido.
3. **`ATASCO_ARRANQUE_S` (apagado por defecto).** Con N > 0 reabre la conexión que todavía no dio
   NINGÚN final después de N s de audio propio con voz pendiente (backoff: se duplica en cada atasco
   seguido). En `sala-a/b-051619` la reapertura llegó a +25,2 s ("mudo") y +30,8 s ("atraso"); c1
   ya llevaba 10 s de audio enviado a +12,7 / +12,5 s (contado desde las ventanas del casete), así que
   con `ATASCO_ARRANQUE_S=10` el pedido hubiese salido 12–18 s antes (estimación sobre el casete, no
   corrida). Queda apagado porque sólo se probó en replay
   (`reportes/audio-pipeline-gate.md`) y un server que tarda en dar el primer final pero no está
   trabado se reabriría de más. Para activarlo en una sala: `ATASCO_ARRANQUE_S=10 python -m worker.run ...`.

Además (arreglado el 25/09, no era la causa): la reserva entre procesos del traductor y la creación del
cliente genai (traductor y cada `conectar()` de Gemini, 3,2–3,3 s medidos con carga) corrían en el loop
y podían frenar el envío; ahora van a un hilo (`asyncio.to_thread`). Tests:
`worker/tests/test_loop_no_bloqueado.py` y `worker/tests/test_conectar_no_bloquea.py`.

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
python -m worker.traducir_casete CASETE --a CODIGO [--salida X] [--timeout-s S]
                                 [--sin-parciales] [--reintentos N]
```

`--a` es un código ISO del idioma destino, GENÉRICO (no `choices=["es","en"]`): cualquier valor que
matchee `contracts/esquema.json#lang_destino` (`^[a-z]{2}(-[A-Z]{2})?$`, p.ej. `es`, `en`, `pt`,
`pt-BR`); otro formato corta el CLI antes de llamar a la API (`worker.traducir_casete.lang_destino`).
Lee los `text` del casete, arma los lotes con la misma regla que el worker en vivo
(`worker.traductor.Lotes`) y hace llamadas reales al modelo de texto (cuentan en
`CUOTA_TEXTO_LOG`). Escribe `<nombre>-trad.jsonl` = original + `translation` intercalados con
`meta.source.offline: true`. Exit 0 = escrito; 2 = casete sin textos.

```
.venv/Scripts/python -m worker.traducir_casete fixtures/casetes/b1-es-60s.jsonl --a pt \
  --salida fixtures/casetes/b1-es-60s-trad-pt.jsonl
```

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
| `ATASCO_UMBRAL_S` / `ATASCO_SOSTENIDO_S` | `22` / `8` | reapertura por atraso del server (10 / 4 se probó el 25/09 y se descartó: `reportes/audio-pipeline-umbral.log`) |
| `MUDO_S` | `10` | reapertura por ventanas con voz sin ningún texto (watchdog) |
| `ATASCO_ARRANQUE_S` | `0` (apagado) | reabre la conexión que todavía no dio NINGÚN final después de N s de audio propio con voz pendiente (probado sólo en replay; ver `reportes/audio-pipeline-gate.md`) |
| `ROTACION_PREVENTIVA_S` | `240` | reapertura preventiva por audio enviado a una conexión |
| `DRENAJE_VIEJA_S` | `20` | cuánto drena la conexión vieja tras reabrir |
| `REENVIO_MAX_S` | `15` | tope de audio que se reenvía a la conexión nueva |
| `ENVIO_TIMEOUT_S` | `5` | un envío trabado más que esto da la conexión por muerta y reabre |
| `FUENTE_TIMEOUT_S` | `10` | mic/url: sin bytes de ffmpeg durante esto, la fuente se da por caída y se reabre |
| `FUENTE_BACKOFF_MAX_S` | `30` | tope de la espera entre reintentos de abrir la fuente (1, 2, 4… s) |

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
