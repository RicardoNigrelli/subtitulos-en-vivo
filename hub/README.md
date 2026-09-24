# hub — bus de ingesta + fan-out de subtítulos por sesión

Protocolo y formato de mensajes: [`contracts/README.md`](../contracts/README.md) (contrato v1, congelado).

```bash
.venv/Scripts/python -m hub                                   # levanta el hub (localhost:8100)
.venv/Scripts/python -m hub.inyectar contracts/ejemplos/sesion-en.jsonl        # empuja un JSONL/casete (replay)
.venv/Scripts/python -m hub.inyectar fixtures/casetes/x.jsonl --velocidad 4 --session-id otra-sala
.venv/Scripts/python -m hub.cliente_ws ejemplo-en --lang es   # subtítulos en la terminal + chequeo de seq
.venv/Scripts/python -m hub.carga --hub http://localhost:8192 --clientes 200   # N espectadores sintéticos
.venv/Scripts/python -m pytest hub/tests -q                   # tests (puertos efímeros, nunca 8100)
```

| Variable | Default | Qué es |
|---|---|---|
| `HUB_HOST` | `localhost` | en Docker: `0.0.0.0` |
| `HUB_PORT` | `8100` | antes de bindear, si el puerto ya contesta, el hub aborta con exit 2 (en Windows el bind no falla) |
| `HUB_TOKEN` | `dev-token` (con aviso en el log) | token de productores (`/ingest`, primer frame) y de `GET /api/metricas` (Bearer) |
| `HUB_HISTORY` | `1000` | mensajes por sesión en memoria |
| `HUB_QUEUE_MAX` | `100` | cola por espectador; si se llena, ese espectador se desconecta (1013) |
| `HUB_HEARTBEAT_S` | `1.0` | latido a la audiencia |
| `HUB_LIVE_S` | `30` | ventana para `state=live` |
| `HUB_SEND_TIMEOUT_S` | `5` | un envío que tarda más desconecta a ESE espectador |

Se leen del entorno o de `.env` en la raíz (sólo las claves `HUB_*`).

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
