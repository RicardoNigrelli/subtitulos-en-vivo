# hub — bus de ingesta + fan-out de subtítulos por sesión

Protocolo y formato de mensajes: [`contracts/README.md`](../contracts/README.md) (contrato v1, congelado).

```bash
.venv/Scripts/python -m hub                                   # levanta el hub (127.0.0.1:8100)
.venv/Scripts/python -m hub.inyectar contracts/ejemplos/sesion-en.jsonl        # empuja un JSONL/casete (replay)
.venv/Scripts/python -m hub.inyectar fixtures/casetes/x.jsonl --velocidad 4 --session-id otra-sala
.venv/Scripts/python -m hub.cliente_ws ejemplo-en --lang es   # subtítulos en la terminal + chequeo de seq
.venv/Scripts/python -m hub.carga --hub http://localhost:8192 --clientes 200   # N espectadores sintéticos
.venv/Scripts/python -m pytest hub/tests -q                   # tests (puertos efímeros, nunca 8100)
```

| Variable | Default | Qué es |
|---|---|---|
| `HUB_HOST` | `127.0.0.1` | en Docker: `0.0.0.0`. Ojo en Windows: con `127.0.0.1` un cliente que conecta a `localhost` prueba primero `::1` y el `websockets` de Python tarda ~2 s en caer a IPv4 (medido 24/09); con `HUB_HOST=localhost` el hub escucha en `127.0.0.1` y `::1` |
| `HUB_PORT` | `8100` | antes de bindear, si el puerto ya contesta, el hub aborta con exit 2 (en Windows el bind no falla) |
| `HUB_TOKEN` | `dev-token` (con aviso en el log) | token de productores (`/ingest`, primer frame) y de `GET /api/metricas` (Bearer) |
| `HUB_HISTORY` | `1000` | mensajes por sesión en memoria |
| `HUB_QUEUE_MAX` | `100` | cola por espectador; si se llena, ese espectador se desconecta (1013) |
| `HUB_HEARTBEAT_S` | `1.0` | latido a la audiencia |
| `HUB_LIVE_S` | `30` | ventana para `state=live` |
| `HUB_SEND_TIMEOUT_S` | `5` | un envío que tarda más desconecta a ESE espectador |
| `HUB_PENDIENTE_S` | `120` | B2: vida de un item de `translation` que llegó antes que su `text` |

Se leen del entorno o de `.env` en la raíz con python-dotenv (sólo las claves `HUB_*`; el entorno gana).

## Traducciones y parciales (B2, aditivo)

- `translation` (`seq: null`, `items: [{seq, text, ok}]`, `meta.lang_to`): el hub aplica cada item al
  `text` guardado con ese `session_id+seq` como `translations[lang_to]`, así `init`, el historial y
  los reenvíos salen mergeados. Idempotente (un duplicado exacto no se reenvía; un `ok:false` no
  pisa un `ok:true`). Si el `text` todavía no llegó, el item queda pendiente `HUB_PENDIENTE_S`
  segundos y se aplica al llegar. El evento se reenvía en vivo tal cual.
- `partial` (`seq: null`): se reenvía en vivo, no se guarda, no toca `last_seq`.
- `GET /api/sesiones` e `init` agregan `translations_langs` (idiomas destino vistos).
- Contadores en `GET /api/metricas`: `traducciones`, `traducciones_sin_cambio`, `items_aplicados`,
  `items_pendientes`, `items_vencidos`, `items_huerfanos`, `pendientes_ahora`, `parciales`.
- Semántica completa: [`contracts/README.md`](../contracts/README.md), sección "Traducción diferida".

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
