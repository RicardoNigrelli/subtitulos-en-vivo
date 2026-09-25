# hub — bus de ingesta + fan-out de subtítulos por sesión

Protocolo y formato de mensajes: [`contracts/README.md`](../contracts/README.md) (contrato v1, congelado;
sólo se agregan campos).

## Cómo se levanta

```bash
# sólo la API y los WebSocket (desarrollo: la vista y el panel se sirven aparte, en 8101 y 8102)
HUB_HOST=localhost .venv/Scripts/python -m hub                       # http://localhost:8100

# hub + vista de audiencia + panel en UN puerto (lo que usa el despliegue: 8080)
HUB_HOST=localhost HUB_PORT=8080 .venv/Scripts/python -m hub --web web --panel panel
#   http://localhost:8080/                 índice de sesiones (web/index.html)
#   http://localhost:8080/s/<id>?lang=es   vista de una sesión (web/sesion.html)
#   http://localhost:8080/panel/           panel de monitoreo (panel/index.html)

# herramientas
.venv/Scripts/python -m hub.inyectar contracts/ejemplos/sesion-en.jsonl        # empuja un JSONL/casete (replay)
.venv/Scripts/python -m hub.inyectar fixtures/casetes/x.jsonl --velocidad 4 --session-id otra-sala
.venv/Scripts/python -m hub.cliente_ws ejemplo-en --lang es   # subtítulos en la terminal + chequeo de seq
.venv/Scripts/python -m hub.carga --hub http://localhost:8192 --clientes 200   # N espectadores sintéticos
.venv/Scripts/python -m pytest hub/tests -q                   # tests (puertos efímeros, nunca 8100 ni 8080)
```

Antes de levantar, mirar que el puerto esté libre: `netstat -ano | findstr :8080`. En Windows el bind
no falla con el puerto tomado; por eso el hub prueba conectarse primero y, si alguien contesta,
aborta con exit 2. También sale con exit 2 si `--web`/`--panel` apuntan a una carpeta que no existe o
que no tiene sus html (`index.html` y `sesion.html` en web, `index.html` en panel).
Las rutas relativas (`--web web`) se resuelven contra el directorio desde el que se lanza.

## Variables

| Variable | Default | Qué es |
|---|---|---|
| `HUB_HOST` | `127.0.0.1` | en Docker: `0.0.0.0`. Ojo en Windows: con `127.0.0.1` un cliente que conecta a `localhost` prueba primero `::1` y puede tardar en caer a IPv4; con `HUB_HOST=localhost` el hub escucha en `127.0.0.1` y `::1` |
| `HUB_PORT` | `8100` | puerto de la API, los WebSocket y (con `--web`/`--panel`) los estáticos. Despliegue: `8080` |
| `HUB_TOKEN` | `dev-token` (con aviso en el log) | token de productores (`/ingest`, primer frame) y de `GET /api/metricas` (Bearer). **Si es `dev-token` o tiene menos de 16 caracteres y `HUB_HOST` no es `127.0.0.1`/`localhost`/`::1`, el hub NO arranca (exit 2).** Con `dev-token` avisa siempre, venga del entorno, de `.env` o del default. Generar uno: `python -c "import secrets;print(secrets.token_urlsafe(24))"` |
| `HUB_WEB_DIR` | (vacío) | = `--web`: carpeta de la vista de audiencia. Vacío: `GET /` devuelve la lista de rutas |
| `HUB_PANEL_DIR` | (vacío) | = `--panel`: carpeta del panel de monitoreo |
| `HUB_HISTORY` | `1000` | mensajes por sesión en memoria |
| `HUB_QUEUE_MAX` | `100` | cola por espectador; si se llena, ese espectador se desconecta (1013) |
| `HUB_HEARTBEAT_S` | `1.0` | latido a la audiencia |
| `HUB_LIVE_S` | `30` | ventana para `state=live` |
| `HUB_SEND_TIMEOUT_S` | `5` | un envío que tarda más desconecta a ESE espectador |
| `HUB_PENDIENTE_S` | `120` | vida de un item de `translation` que llegó antes que su `text` |
| `HUB_MAX_MSG_BYTES` | `65536` | tamaño máximo de un frame de `/ingest`; uno más grande corta ESA conexión (1009). El mensaje real más grande de `fixtures/casetes/**/*.jsonl` pesa 1818 B |
| `HUB_MAX_SESIONES` | `200` | salas con ingesta en memoria; un mensaje de una sala nueva por encima del tope recibe `rechazado` ("tope de sesiones"). Las terminadas no se liberan hasta reiniciar el hub |
| `HUB_MAX_ESPERA` | `50` | salas "en espera" (un espectador abrió `/ws/<slug>` de una sala sin ingesta); la siguiente se cierra con 1013 |
| `HUB_MAX_AUDIENCIA` | `2000` | WebSocket de audiencia simultáneos en TODO el hub; el siguiente se cierra con 1013. No hay tope por IP a propósito (detrás de un NAT toda la audiencia comparte IP) |

Se leen del entorno o de `.env` en la raíz con python-dotenv (sólo las claves `HUB_*`; el entorno
gana). `--web` y `--panel` ganan sobre `HUB_WEB_DIR` y `HUB_PANEL_DIR`.

## Rutas

| Ruta | Auth | Qué hace |
|---|---|---|
| `WS /ingest` | token en el primer frame `{"type":"auth","token":..}` (nunca en la URL) | productores (worker, replay): un mensaje del contrato por frame |
| `WS /ws/<session_id>?lang=<xx>` | pública | audiencia: `init` (últimas 10 líneas) + mensajes en vivo + latido cada 1 s |
| `GET /health` | no | `{"ok":true, sesiones, productores, clientes, uptime_s}` |
| `GET /api` | no | lista de rutas activas |
| `GET /api/sesiones` | no | índice de sesiones (R6): `state`, `last_seq`, `replay`, `source`, `test`, `translations_langs`, espectadores |
| `GET /api/sesiones/<id>/historial?desde=<seq>[&limit=N]` | no | mensajes `text` con `seq > desde`, con traducciones ya mergeadas. `limit` 1-1000, default 500: devuelve los `limit` MÁS VIEJOS después de `desde`; si quedaron más, la cabecera `X-Historial-Truncado: <cuántos>` lo dice y se sigue con `desde=<último seq recibido>` |
| `GET /api/sesiones/<id>/historial?desde=<seq>&tipos=todos` | no | ídem con todos los tipos guardados (`session_start`, `rotation`, `session_end`…); lo usa la vista para el backfill al reconectar |
| `GET /api/metricas` | `Authorization: Bearer <HUB_TOKEN>` | contadores por sesión (clientes, descartes, duplicados, traducciones) |
| `GET /`, `GET /index.html` | no | con `--web`: `web/index.html` (sin `--web`: la lista de rutas en JSON) |
| `GET /s/<lo-que-sea>` | no | con `--web`: `web/sesion.html` (la sesión y el idioma los lee el cliente de la URL) |
| `GET /<archivo>` | no | con `--web`: el archivo de `web/` (`/app.js`, `/estilo.css`, …) |
| `GET /panel` → `302 /panel/` · `GET /panel/` · `GET /panel/<archivo>` | no | con `--panel`: `panel/index.html` y sus archivos (el 302 conserva la query) |

Cabeceras en TODA respuesta (seguridad, 25/09): `X-Content-Type-Options: nosniff`, `Referrer-Policy:
no-referrer`, `Server: vibeathon-hub` (sin versiones). `X-Frame-Options: SAMEORIGIN` sólo en `/panel/`
(tiene el campo del token); la vista queda enmarcable. CORS `*` sigue en las lecturas.

Estáticos (`hub/estaticos.py`): sólo archivos DENTRO de la carpeta, con extensión de una lista
(`.html .js .css .json .svg .png .ico …`; no se sirven `.py` ni archivos que empiezan con `.`), con
`Cache-Control: no-cache` y `ETag` (el navegador revalida en cada carga y recibe 304 si no cambió). Un
archivo editado en disco se sirve nuevo sin reiniciar el hub. Las rutas de la API se registran antes
que las de los estáticos: `/api`, `/ws`, `/ingest` y `/health` no quedan tapadas por un archivo.

El proxy `/panel-api/metricas` de `panel/servir.py` NO existe en el hub: `GET /api/metricas` pide
Bearer y el hub no la expone sin token por otra ruta. Servido desde el hub, el panel tiene que
mandar el token él mismo (pedido a `monitor`).

## Traducciones y parciales (B2, aditivo)

- `translation` (`seq: null`, `items: [{seq, text, ok}]`, `meta.lang_to`): el hub aplica cada item al
  `text` guardado con ese `session_id+seq` como `translations[lang_to]`, así `init`, el historial y
  los reenvíos salen mergeados. Idempotente (un duplicado exacto no se reenvía; un `ok:false` no
  pisa un `ok:true`). Si el `text` todavía no llegó, el item queda pendiente `HUB_PENDIENTE_S`
  segundos y se aplica al llegar. El evento se reenvía en vivo tal cual.
- `partial` (`seq: null`): se reenvía en vivo, no se guarda, no toca `last_seq`.
- `GET /api/sesiones` e `init` agregan `translations_langs` (idiomas destino).
- Contadores en `GET /api/metricas`: `traducciones`, `traducciones_sin_cambio`, `items_aplicados`,
  `items_pendientes`, `items_vencidos`, `items_huerfanos`, `pendientes_ahora`, `parciales`.
- Semántica completa: [`contracts/README.md`](../contracts/README.md), sección "Traducción diferida".

## Índice de sesiones: idiomas y rótulo de prueba (B4, aditivo)

- `translations_langs` = idiomas declarados en `session_start.meta.translations_langs` (desde que
  llega el `session_start`, antes de la primera traducción) ∪ idiomas vistos en `translation`
  (`meta.lang_to`) y en los `translations` de los `text`. Valores que no son un código de idioma se
  ignoran con un WARNING en el log.
- `source` = `session_start.meta.source` (string u objeto; el worker manda `{file, url, start_s,
  dur_s}`); si no vino, el primer `meta.source` de un mensaje que no sea `translation`.
- `test: true` si algún mensaje de la sesión trae `meta.source` igual a `transporte-casete` (worker
  con respuestas grabadas, sin API) o `ejemplo-contrato` (`contracts/ejemplos/`): son sesiones de
  prueba, no ASR en vivo. Aparece en `GET /api/sesiones`, en `GET /api/metricas` y en `init`.
  `replay: true` es otra cosa: casete reproducido con `worker.replay` o `hub.inyectar`.

## Diseño (por qué escala a cientos de espectadores por sesión, C3 eje 2)

- La ingesta nunca espera a la audiencia: cada mensaje se valida contra el contrato, se deduplica por
  `session_id+seq`, se serializa UNA vez y se encola con `put_nowait` en la cola de cada espectador.
- Cada espectador tiene su cola acotada y su propia tarea de envío: un cliente lento, colgado o muerto
  sólo se perjudica a sí mismo (se lo desconecta con 1013 y reconecta).
- El `seq` lo pone el worker: el hub no renumera ni rebobina. Un reinicio del hub se recupera con el
  backlog del productor (`auth_ok.last_seq`).
- Estado sólo en memoria (sin base de datos). Medido: 200 espectadores sobre una sesión reciben todos
  los mensajes en orden (`hub/tests/test_fanout.py`, `python -m hub.carga`). Que un hub alcance para
  un evento entero es [SUPUESTO: backend]; si no, varias instancias repartiendo sesiones (el
  `session_id` es la clave de partición).

## Verificación con dientes

`hub/tests/mutante_fanout.py` reemplaza el fan-out por uno SERIAL y corre el test del cliente
colgado: tiene que FALLAR (`python -m pytest hub/tests/mutante_fanout.py -q` → exit 1). Si pasara,
el test no estaría midiendo nada.

## Seguridad y límites conocidos (25/09, `reportes/backend-seguridad.md`)

- **Token:** ver `HUB_TOKEN` arriba. Nunca en la query string (el access log la guarda).
- **Topes:** `HUB_MAX_MSG_BYTES`, `HUB_MAX_SESIONES`, `HUB_MAX_ESPERA`, `HUB_MAX_AUDIENCIA` (arriba).
  Los campos de primer nivel que no están en `contracts/esquema.json` pasan la validación pero el hub
  NO los guarda ni los reparte (ver `contracts/README.md`). Un JSON muy anidado recibe `rechazado`
  (antes: traceback). Los errores de un `rechazado` y del log se cortan a 300 caracteres.
- **Sin límite de intentos de auth ni de conexiones por IP:** en producción, un proxy delante con
  `limit_conn`/`limit_req` y TLS (el token viaja en claro por `ws://`).
- **Hub único en memoria.** Un reinicio del hub pierde las traducciones previas: el worker reenvía sus
  `text` (salen con `translations: {}`) pero no las `translation` ya entregadas (`worker/emisor.py`).
  Quien ya estaba mirando conserva las suyas; quien entra o hace backfill de ese tramo, y el export
  desde el hub, las ven sin traducir. Además, si la vista reconecta antes que el worker, la sala todavía
  no existe en el hub nuevo, `historial` da 404 y la vista pinta un **"[tramo perdido]" falso**.
  Medido (`reportes/adversario-final-escala.md`, sección 7, 300 clientes, hub matado y relevantado dos
  veces): seqs traducidos que perdieron la traducción `[2,3,4,5,6,7,10,11]` en las dos corridas (8/8);
  backfills 404 = 228/300 (r1) y 4/300 (r2). Ningún cliente perdió `text` (300/300 al último seq).
  NO está arreglado: pendiente reenviar las `translation` en el Emisor y que la vista trate el 404 con
  `init.state == "waiting"` como "reintentar".
- **Historial:** 1000 mensajes por sala (~50 min); una charla más larga se exporta desde el casete.
  `GET .../historial` es público y sin límite de frecuencia; con `limit` (máx. 1000) el peor caso por
  pedido baja pero no desaparece (antes 8,1 MB con `tipos=todos` y textos de 3900 caracteres).
- **Workaround de aiohttp 3.14:** al cerrar desde el server, aiohttp re-arma el heartbeat y el WS
  quedaba vivo ~30 s (medido: 300 de 300 vivos a 1 s). Los WS rechazados con 1013 se abren sin
  heartbeat y a los de auth fallida se les cancela a mano (`_cancel_heartbeat`, API privada).
