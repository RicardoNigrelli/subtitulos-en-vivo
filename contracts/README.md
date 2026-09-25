CONGELADO 24/09 13:08 AR — desde acá sólo se AGREGAN campos (nunca se renombran ni se quitan).
Si el dominio obliga a cambiar algo: aviso al orquestador en los primeros 15 min del bloque y alias
del campo viejo. Dueño: `backend`.

AMPLIADO 24/09 14:05 AR (aditivo, B2): tipos nuevos `translation` y `partial`, campo nuevo de primer
nivel `items`, campo nuevo `translations_langs` en `GET /api/sesiones` y en `init`. Ningún campo
renombrado ni quitado; todo mensaje que validaba antes sigue validando (ver "Traducción diferida" y
"Texto provisorio" más abajo).

AMPLIADO 24/09 B4 (aditivo): clave opcional `session_start.meta.translations_langs` (lista de códigos
de idioma destino, p. ej. `["es"]`) que el hub suma a `translations_langs` apenas llega; campos nuevos
`source` y `test` en `GET /api/sesiones` y en `init`; rótulo de sesiones de prueba por `meta.source`
(`transporte-casete`, `ejemplo-contrato`; constantes `FUENTES_TEST` en `contracts/__init__.py`). El
esquema no cambió: ningún mensaje que validaba deja de validar (ver "Índice de sesiones: idiomas y
rótulo de prueba" más abajo).

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

**Ojo (25/09, seguridad M2):** el hub valida con `additionalProperties` abierto, pero **guarda y
reparte sólo los campos de primer nivel declarados en `esquema.json` `properties`** (`v type
session_id seq lang text translations audio_start audio_end t_captured t_emit t_hub replay meta
items`). Un campo nuevo de primer nivel tiene que AGREGARSE a `properties` para llegar a la audiencia
y al historial; lo extensible sin tocar el esquema es `meta`. Frame máximo en `/ingest`: 64 KiB
(`HUB_MAX_MSG_BYTES`). Límite conocido tras un reinicio del hub (traducciones previas perdidas,
"[tramo perdido]" falso): ver `hub/README.md`, "Seguridad y límites conocidos".

```bash
.venv/Scripts/python -m contracts.validate contracts/ejemplos/*.jsonl   # exit 0 = todo válido
.venv/Scripts/python -m contracts.validate fixtures/casetes/x.jsonl     # casete: valida las líneas dir=emit
```

El validador chequea cada mensaje contra el esquema, `audio_end >= audio_start`,
`t_captured <= t_emit` y `seq` estrictamente creciente por sesión dentro del archivo (los `seq: null`
no cuentan). Para `translation` imprime un `AVISO` (no error) si un item apunta a un `seq` sin `text`
de esa sesión en el archivo. Exit 0 válido ·
1 algún mensaje inválido · 2 archivo inexistente. Desde Python: `from contracts import errores`
(`errores(msg) -> list[str]`, vacía = válido).

## Campos

| Campo | Tipo | Quién | Obligatorio | Qué es |
|---|---|---|---|---|
| `v` | `1` | worker | siempre | versión del contrato |
| `type` | `text` · `rotation` · `watchdog` · `error` · `heartbeat` · `session_start` · `session_end` · `translation` · `partial` | worker | siempre | tipo de evento (`translation` y `partial`: B2) |
| `session_id` | string slug `^[a-z0-9][a-z0-9_-]{0,63}$` | worker | siempre | ej. `sala-1`; va en la URL |
| `seq` | int ≥ 0 · `null` en `heartbeat`, `partial` y `translation` | **worker** | siempre | monotónico POR SESIÓN, cubre todos los tipos salvo esos tres. **El hub nunca renumera ni rebobina.** |
| `lang` | `en` · `es` | worker | todos menos heartbeat | idioma ORIGINAL de la sesión (R18) |
| `text` | string no vacío (`null` fuera de `text`) | worker | `text` | texto final del bloque, en el idioma original |
| `translations` | objeto | worker | `text` | por idioma destino: `{"es": {"text": "...", "ok": true}}`; si falló: `{"es": {"text": null, "ok": false}}` (marcado); `{}` si no hay |
| `audio_start`, `audio_end` | float s (`null` fuera de `text`) | worker | `text` | segundos desde el inicio del audio de la sesión (SRT/VTT, R8d) |
| `t_captured` | float epoch s (`null` fuera de `text`) | worker | `text` | cuándo entró al pipeline la ÚLTIMA muestra de audio del bloque (base de la latencia percibida, C2) |
| `t_emit` | float epoch s | worker | todos menos el heartbeat del hub | cuándo el worker emitió |
| `t_hub` | float epoch s | **hub** | lo agrega el hub | cuándo el hub hizo el fan-out |
| `replay` | bool | worker / replay | todos menos heartbeat | `true` si viene de un casete o de un ejemplo. El replay NO es ASR real |
| `meta` | objeto | worker | según tipo | extensible, ver abajo |
| `items` | lista `[{seq, text, ok}]` | worker | `translation` | traducciones de mensajes `text` de la MISMA sesión, por `seq` (B2) |

Tiempos en **segundos** (float), no milisegundos: el esquema rechaza epochs > 9999999999.

### `meta` por tipo (claves obligatorias)

| `type` | `meta` |
|---|---|
| `text` | libre (los ejemplos llevan `{"source": "ejemplo-contrato"}`) |
| `rotation` | `{reason, old_id, new_id}` — rotación de la conexión con el modelo (GoAway) |
| `watchdog` | `{silent_s, reopened}` — voz enviada sin texto durante `silent_s` |
| `error` | `{code, message}` |
| `session_start` | `{title, source}` (`title`: string o `null`; `source`: string, objeto o `null`) + `translations_langs` opcional (B4): lista de idiomas destino que la sesión va a traducir, p. ej. `["es"]` |
| `session_end` | libre |
| `heartbeat` (del worker, con `t_emit`) | `{alive, audio_seconds_sent}` |
| `translation` | `{lang_to, model, batch_ms}` + `source` opcional. `lang_to`: idioma destino (`es`, `en`, …); `model`: modelo que tradujo; `batch_ms`: ms del lote (número ≥ 0; se recomienda entero) |
| `partial` | libre |

## Traducción diferida (`type=translation`) y merge en el hub

El worker emite el `text` apenas lo tiene, con `translations: {}`, y la traducción llega DESPUÉS en
un evento aparte (lotes de varias ventanas, R19):

```json
{"v":1,"type":"translation","session_id":"sala-1","seq":null,"lang":"en","replay":false,
 "t_emit":1790262009.0,"text":null,"translations":{},
 "meta":{"lang_to":"es","model":"gemini-3.5-flash-lite","batch_ms":1200},
 "items":[{"seq":2,"text":"Ejemplo de contrato: traducción del seq 2.","ok":true},{"seq":3,"text":null,"ok":false}]}
```

- `seq: null` (no ocupa número) · `lang`: idioma ORIGINAL de la sesión · `items` (≥ 1): cada uno
  `{seq, text, ok}` con la misma regla que `translations`: `ok:true` ⇒ `text` no vacío;
  `ok:false` ⇒ `text: null` (fallo marcado, la vista lo muestra como tal).
- **Merge (lo hace el hub):** cada item se aplica al mensaje `text` GUARDADO con ese
  `session_id+seq` como `translations[meta.lang_to] = {text, ok}`. `init.lines`,
  `GET /api/sesiones/<id>/historial` y todo reenvío posterior devuelven el `text` YA MERGEADO.
- **Idempotente:** aplicar dos veces el mismo item no cambia nada. Un `ok:false` NO pisa un
  `ok:true` ya guardado (un reintento fallido no borra una traducción buena); un `ok:true` sí
  reemplaza un `ok:false` (reintento exitoso) o un `ok:true` anterior (el último gana).
- **Item que llega ANTES que su `text`** (reconexión, backlog): el hub lo guarda PENDIENTE y lo
  aplica cuando llega el `text` con ese `seq`, que se reparte ya mergeado. Si el `text` no llega en
  `HUB_PENDIENTE_S` segundos (default 120) el item se descarta (contador `items_vencidos` en
  `/api/metricas`). Un item cuyo `seq` es de otro tipo (no `text`) o ya salió del historial se
  descarta (`items_huerfanos`).
- **En vivo:** el hub reenvía el evento `translation` TAL CUAL (más `t_hub`) a los clientes de la
  sesión, salvo que no cambie nada (duplicado exacto: no se reenvía). El cliente aplica cada item a
  la línea con ese `seq` si la tiene; si no la tiene puede ignorarlo: cuando el `text` llegue, llega
  mergeado.
- Puede llegar DESPUÉS del `session_end` (los lotes se atrasan: en `b1-es-60s-trad.jsonl`, 3 de 6
  eventos): la vista tiene que seguir aplicándolos después del fin de la sesión.
- No se guarda en el historial ni toca `last_seq`.

## Texto provisorio (`type=partial`)

```json
{"v":1,"type":"partial","session_id":"sala-1","seq":null,"lang":"en","replay":false,
 "text":"Contract example, partial","audio_start":12.0,"t_captured":1790262012.4,"t_emit":1790262012.6,"meta":{}}
```

- Lo que el modelo lleva transcripto del bloque en curso (`interimInputTranscription`), en el idioma
  original. `text` string (puede ser vacío), `audio_start` y `t_captured` presentes (pueden ser
  `null`), `seq: null`.
- El hub lo reenvía EN VIVO a los clientes de la sesión y **no lo guarda**: no aparece en `init`, ni
  en el historial, ni toca `last_seq`. La vista lo pinta provisorio (gris) y lo reemplaza con el
  siguiente `partial` o con el `text` final.


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
   - `translations_langs` (B2): idiomas destino vistos en la sesión, p. ej. `["es"]`; desde B4 también
     los declarados en `session_start.meta.translations_langs`.
   - `source`, `test` (B4): los mismos que en `GET /api/sesiones` (ver "Índice de sesiones").
2. Hub → cada mensaje del contrato de ESA sesión (`text`, `rotation`, `watchdog`, `error`,
   `session_start`, `session_end`, y desde B2 `translation` y `partial`) COMPLETO, con `t_hub`. El cliente elige el idioma: `text` si
   `lang` coincide con el pedido; si no `translations[<pedido>]` (si `ok: false`, está marcado como fallido).
3. Hub → cada 1 s `{"v":1,"type":"heartbeat","session_id":..,"seq":null,"t_hub":..,"last_seq":N,"state":..}`.
   Es la base del chip de conexión. `last_seq` permite detectar un hueco (se perdió un tramo).
4. Hueco o reconexión: `GET /api/sesiones/<id>/historial?desde=<último seq visto>`.
5. Después de `session_end` (B2): pueden seguir llegando `translation` rezagados; aplicarlos. Si llega
   un mensaje con `seq` MAYOR que el del `session_end`, la sesión se reabre (`state` vuelve a `live`,
   el hub lo avisa con WARNING en su log): la vista lo muestra como continuación.

`state` en `init` y `heartbeat`: `live` · `idle` · `ended` · `waiting` (todavía no llegó nada de esa sesión).

Códigos de cierre del hub: `4401` auth inválida (sólo ingesta) · `1013` cliente demasiado lento
(se le llenó la cola: reconectar) · `1001` el hub se apaga.

## HTTP (CORS abierto para GET: `Access-Control-Allow-Origin: *`)

| Ruta | Auth | Respuesta |
|---|---|---|
| `GET /health` | no | `200 {"ok":true,...}` |
| `GET /api/sesiones` | no | lista `[{session_id, lang, title, replay, last_seq, last_t_emit, state, translations_langs, ...}]`; `state`: `live` (el hub recibió algo de la sesión en los últimos 30 s) · `idle` · `ended` (llegó `session_end`); `translations_langs` (B2): idiomas destino vistos, p. ej. `["es"]` |
| `GET /api/sesiones/<id>/historial?desde=<seq>` | no | lista de mensajes `type=text` con `seq > desde`, ordenados por `seq`, con las traducciones YA MERGEADAS (404 si la sesión no existe). Con `&tipos=todos`: todos los tipos guardados (no `partial` ni `translation`, que no se guardan). (25/09, aditivo) `&limit=N` 1-1000, default **500**: los `limit` más viejos después de `desde`; si quedaron más, cabecera `X-Historial-Truncado: <n>` y se sigue con `desde=<último seq>` |
| `GET /api/metricas` | `Authorization: Bearer <HUB_TOKEN>` | contadores del hub por sesión (clientes, descartes, duplicados) |
| `GET /api` | no | (B4) lista de rutas activas |

B4: `GET /api/sesiones` agrega `source` y `test` a cada sesión (ver abajo). Si el hub se levanta con
`--web`/`--panel` también sirve la vista (`/`, `/s/<id>`) y el panel (`/panel/`) en el mismo puerto:
ver `hub/README.md`.

## Índice de sesiones: idiomas y rótulo de prueba (B4, aditivo)

- `translations_langs` (en `GET /api/sesiones` e `init`) = idiomas de
  `session_start.meta.translations_langs` (apenas llega el `session_start`, antes de la primera
  traducción) ∪ `meta.lang_to` de los `translation` ∪ claves de `translations` de los `text`. Un valor
  que no es un código de idioma (`^[a-z]{2}(-[A-Z]{2})?$`) se ignora (WARNING en el log del hub); el
  mensaje NO se rechaza.
- `source` = `session_start.meta.source`; si no vino (o vino `null`), el primer `meta.source` de un
  mensaje de la sesión que no sea `translation` (en `translation`, `meta.source` describe la
  traducción, no la sesión). String u objeto, tal cual llegó.
- `test` (bool) = `true` si ALGÚN mensaje de la sesión trae `meta.source` igual a
  `"transporte-casete"` (worker con `--transporte casete:`, respuestas grabadas, sin API) o
  `"ejemplo-contrato"` (`contracts/ejemplos/`). Una sesión `test` NO es ASR en vivo. Es independiente
  de `replay` (casete reproducido por `worker.replay` / `hub.inyectar`).

## Ejemplos (`ejemplos/`)

SINTÉTICOS y evidentes ("Contract example, line 3 of 20." / "Ejemplo de contrato, línea 3 de 20."),
todos con `replay: true` y `meta.source: "ejemplo-contrato"`. NO son transcripciones ni evidencia
de ASR: sirven para desarrollar contra el hub real con `python -m hub.inyectar`. Se regeneran con
`python -m contracts.generar_ejemplos`.

| Archivo | Contenido |
|---|---|
| `sesion-en.jsonl` | `ejemplo-en`, 20 `text` en inglés con `translations.es`; la línea 13 trae la traducción fallida (`ok:false`); 5 y 12 son largas |
| `sesion-es.jsonl` | `ejemplo-es`, 20 `text` en español con `translations.en`; la línea 7 trae la traducción fallida |
| `tipos.jsonl` | `ejemplo-tipos`: un mensaje de cada `type` en orden de vida de una sesión (los 7 de B1) |
| `traduccion.jsonl` | `ejemplo-traduccion` (B2): 3 `partial`, 3 `text` con `translations: {}` y 2 `translation`: seq 2 ok, seq 3 fallida (`ok:false`) y seq 4 cuya traducción llega ANTES que el `text` (queda pendiente en el hub y se aplica al llegar) |

**25/09 (aditivo):** `lang_origen` acepta además `pt`, `fr`, `de`, `it` (el worker los admite con aviso; probados en vivo sólo `en` y `es`). Una sala puede traducir a varios idiomas: una `translation` por idioma y `session_start.meta.translations_langs` con la lista.
