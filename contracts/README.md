CONGELADO 24/09 13:08 AR — desde acá sólo se AGREGAN campos (nunca se renombran ni se quitan).
Si el dominio obliga a cambiar algo: aviso al orquestador en los primeros 15 min del bloque y alias
del campo viejo. Dueño: `backend`.

Cambios desde el congelamiento (todos ENSANCHAN: lo que era válido sigue siéndolo):
- 13:14 AR (dentro de los 15 min; lo impuso el primer mensaje real del worker): `text`, `audio_start`,
  `audio_end`, `t_captured` aceptan `null` fuera de `type=text` (en `text` siguen obligatorios y no
  nulos); `session_start.meta.source` acepta cualquier tipo (el worker manda `{file, url, start_s,
  dur_s}`); el validador saltea la cabecera `{"casete":1, ...}` de los casetes.

# Contrato de mensajes v1

Un mensaje = un objeto JSON en una línea (JSONL) o en un frame WebSocket. Lo emite el **worker** (o
el replay) hacia el hub por `/ingest`; el hub le agrega `t_hub` y lo reparte a la audiencia por
`/ws/<session_id>`. Esquema formal: [`esquema.json`](esquema.json) (JSON Schema 2020-12).
`additionalProperties` queda abierto: cualquiera puede **agregar** campos sin romper a nadie.

```bash
.venv/Scripts/python -m contracts.validate contracts/ejemplos/*.jsonl   # exit 0 = todo válido
.venv/Scripts/python -m contracts.validate fixtures/casetes/x.jsonl     # casete: valida las líneas dir=emit
```

El validador chequea cada mensaje contra el esquema, `audio_end >= audio_start`,
`t_captured <= t_emit` y `seq` estrictamente creciente por sesión dentro del archivo. Exit 0 válido ·
1 algún mensaje inválido · 2 archivo inexistente. Desde Python: `from contracts import errores`
(`errores(msg) -> list[str]`, vacía = válido).

## Campos

| Campo | Tipo | Quién | Obligatorio | Qué es |
|---|---|---|---|---|
| `v` | `1` | worker | siempre | versión del contrato |
| `type` | `text` · `rotation` · `watchdog` · `error` · `heartbeat` · `session_start` · `session_end` | worker | siempre | tipo de evento |
| `session_id` | string slug `^[a-z0-9][a-z0-9_-]{0,63}$` | worker | siempre | ej. `sala-1`; va en la URL |
| `seq` | int ≥ 0 · `null` sólo en `heartbeat` | **worker** | siempre | monotónico POR SESIÓN, cubre todos los tipos salvo heartbeat. **El hub nunca renumera ni rebobina.** |
| `lang` | `en` · `es` | worker | todos menos heartbeat | idioma ORIGINAL de la sesión (R18) |
| `text` | string no vacío (`null` fuera de `text`) | worker | `text` | texto final del bloque, en el idioma original |
| `translations` | objeto | worker | `text` | por idioma destino: `{"es": {"text": "...", "ok": true}}`; si falló: `{"es": {"text": null, "ok": false}}` (marcado); `{}` si no hay |
| `audio_start`, `audio_end` | float s (`null` fuera de `text`) | worker | `text` | segundos desde el inicio del audio de la sesión (SRT/VTT, R8d) |
| `t_captured` | float epoch s (`null` fuera de `text`) | worker | `text` | cuándo entró al pipeline la ÚLTIMA muestra de audio del bloque (base de la latencia percibida, C2) |
| `t_emit` | float epoch s | worker | todos menos el heartbeat del hub | cuándo el worker emitió |
| `t_hub` | float epoch s | **hub** | lo agrega el hub | cuándo el hub hizo el fan-out |
| `replay` | bool | worker / replay | todos menos heartbeat | `true` si viene de un casete o de un ejemplo. El replay NO es ASR real |
| `meta` | objeto | worker | según tipo | extensible, ver abajo |

Tiempos en **segundos** (float), no milisegundos: el esquema rechaza epochs > 9999999999.

### `meta` por tipo (claves obligatorias)

| `type` | `meta` |
|---|---|
| `text` | libre (los ejemplos llevan `{"source": "ejemplo-contrato"}`) |
| `rotation` | `{reason, old_id, new_id}` — rotación de la conexión con el modelo (GoAway) |
| `watchdog` | `{silent_s, reopened}` — voz enviada sin texto durante `silent_s` |
| `error` | `{code, message}` |
| `session_start` | `{title, source}` (`title`: string o `null`; `source`: string, objeto o `null`) |
| `session_end` | libre |
| `heartbeat` (del worker, con `t_emit`) | `{alive, audio_seconds_sent}` |

## Protocolo de INGESTA (worker / replay → hub)

WebSocket `ws://<host>:8100/ingest`. Token en el **primer frame**, nunca en la URL.

1. Productor → `{"type":"auth","token":"<HUB_TOKEN>"}` (dentro de los 5 s).
2. Hub → `{"type":"auth_ok","v":1,"last_seq":{"<session_id>": <seq>, ...}}` (`{}` si el hub arrancó de cero).
   Token inválido o sin auth: el hub **cierra** con código `4401`.
3. Productor → un mensaje del contrato por frame.
4. **Backlog:** al (re)conectar, el productor reenvía los mensajes con `seq > last_seq[session_id]`.
   Así se recupera lo perdido si se cayó la conexión o se reinició el hub. Un worker que arranca
   sin estado puede seguir numerando desde `last_seq + 1`.
5. **Idempotencia:** el hub ignora un `session_id+seq` que ya recibió (no lo duplica ni lo reenvía).
6. Mensaje inválido: el hub NO lo reparte y responde `{"type":"rechazado","session_id":..,"seq":..,"errores":[...]}`.
   La conexión sigue abierta.
7. El `heartbeat` del worker no se reenvía a la audiencia: el hub lo usa para saber que la sesión está viva.

Las sesiones se aprenden solas del stream (primer mensaje o `session_start`).

## Protocolo de AUDIENCIA (público, sin token)

WebSocket `ws://<host>:8100/ws/<session_id>?lang=<xx>`. Se puede conectar ANTES de que la sesión
exista: recibe `init` vacío y después los mensajes cuando arranque.

1. Hub → `{"type":"init","v":1,"session_id":..,"lang":..,"session_lang":..,"last_seq":N,"state":..,"lines":[...]}`
   - `lines`: últimos 10 mensajes `type=text`, ordenados por `seq`, COMPLETOS (texto + traducciones).
   - `last_seq`: último `seq` (de cualquier tipo) que tiene el hub; `null` si todavía no hay.
   - `lang`: el idioma PEDIDO (eco de `?lang=`); si no se pidió, el original de la sesión o `null`.
     `session_lang`: idioma original de la sesión.
2. Hub → cada mensaje del contrato de ESA sesión (`text`, `rotation`, `watchdog`, `error`,
   `session_start`, `session_end`) COMPLETO, con `t_hub`. El cliente elige el idioma: `text` si
   `lang` coincide con el pedido; si no `translations[<pedido>]` (si `ok: false`, está marcado como fallido).
3. Hub → cada 1 s `{"v":1,"type":"heartbeat","session_id":..,"seq":null,"t_hub":..,"last_seq":N,"state":..}`.
   Es la base del chip de conexión. `last_seq` permite detectar un hueco (se perdió un tramo).
4. Hueco o reconexión: `GET /api/sesiones/<id>/historial?desde=<último seq visto>`.

`state` en `init` y `heartbeat`: `live` · `idle` · `ended` · `waiting` (todavía no llegó nada de esa sesión).

Códigos de cierre del hub: `4401` auth inválida (sólo ingesta) · `1013` cliente demasiado lento
(se le llenó la cola: reconectar) · `1001` el hub se apaga.

## HTTP (CORS abierto para GET: `Access-Control-Allow-Origin: *`)

| Ruta | Auth | Respuesta |
|---|---|---|
| `GET /health` | no | `200 {"ok":true,...}` |
| `GET /api/sesiones` | no | lista `[{session_id, lang, title, replay, last_seq, last_t_emit, state, ...}]`; `state`: `live` (el hub recibió algo de la sesión en los últimos 30 s) · `idle` · `ended` (llegó `session_end`) |
| `GET /api/sesiones/<id>/historial?desde=<seq>` | no | lista de mensajes `type=text` con `seq > desde`, ordenados por `seq` (404 si la sesión no existe) |
| `GET /api/metricas` | `Authorization: Bearer <HUB_TOKEN>` | contadores del hub por sesión (clientes, descartes, duplicados) |

## Ejemplos (`ejemplos/`)

SINTÉTICOS y evidentes ("Contract example, line 3 of 20." / "Ejemplo de contrato, línea 3 de 20."),
todos con `replay: true` y `meta.source: "ejemplo-contrato"`. NO son transcripciones ni evidencia
de ASR: sirven para desarrollar contra el hub real con `python -m hub.inyectar`. Se regeneran con
`python -m contracts.generar_ejemplos`.

| Archivo | Contenido |
|---|---|
| `sesion-en.jsonl` | `ejemplo-en`, 20 `text` en inglés con `translations.es`; la línea 13 trae la traducción fallida (`ok:false`); 5 y 12 son largas |
| `sesion-es.jsonl` | `ejemplo-es`, 20 `text` en español con `translations.en`; la línea 7 trae la traducción fallida |
| `tipos.jsonl` | `ejemplo-tipos`: un mensaje de cada `type` en orden de vida de una sesión |
