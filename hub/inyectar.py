"""Empuja un JSONL de mensajes del contrato (o un CASETE del worker) al hub por /ingest.

    python -m hub.inyectar contracts/ejemplos/sesion-en.jsonl
    python -m hub.inyectar fixtures/casetes/x.jsonl --velocidad 2 --session-id sala-9
    python -m hub.inyectar <archivo> [--hub ws://localhost:8100/ingest] [--velocidad 1.0]
                           [--session-id X] [--max-gap S] [--token T]

- Casete: lineas {"t":..,"dir":"emit","payload":{mensaje}}; se toman SOLO las dir=emit.
- Respeta los gaps relativos de t_emit (divididos por --velocidad; 0 = sin esperas).
- Fuerza replay=true. NO reescribe t_emit ni t_captured: la latencia de un replay no es real.
- Se comporta como un productor: manda solo los seq > last_seq que el hub informa en auth_ok
  (si se corta, se vuelve a correr y sigue donde quedo). Los seq null (heartbeat, partial) de un
  tramo que el hub ya tiene se saltean; los `translation` se mandan siempre (el merge del hub es
  idempotente: un duplicado exacto no cambia nada ni se reenvia).
Exit 0: todo enviado y nada rechazado · 1: el hub rechazo mensajes · 2: no se pudo conectar/autenticar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

from contracts import leer_archivo

from .config import cargar_config


class ErrorInyeccion(Exception):
    pass


def cargar(path: str | Path, session_id: str | None = None) -> list[dict]:
    msgs = []
    for linea, obj in leer_archivo(path):
        if isinstance(obj, Exception) or not isinstance(obj, dict):
            raise ErrorInyeccion(f"{path}:{linea}: no es un mensaje JSON valido ({obj})")
        m = dict(obj)
        m["replay"] = True
        if session_id:
            m["session_id"] = session_id
        msgs.append(m)
    return msgs


async def inyectar(msgs: list[dict], url: str, token: str, velocidad: float = 1.0,
                   max_gap: float | None = None, log=print) -> dict:
    """Manda los mensajes al hub respetando los gaps de t_emit. Devuelve un resumen."""
    res = {"enviados": 0, "salteados": 0, "rechazados": [], "last_seq_hub": {}}
    async with aiohttp.ClientSession() as http:
        try:
            ws = await http.ws_connect(url, heartbeat=20.0)
        except (aiohttp.ClientError, OSError) as e:
            raise ErrorInyeccion(f"no se pudo conectar a {url}: {e}") from e
        async with ws:
            await ws.send_str(json.dumps({"type": "auth", "token": token}))
            r = await ws.receive(timeout=5.0)
            if r.type != aiohttp.WSMsgType.TEXT:
                raise ErrorInyeccion(f"el hub no acepto el token (cierre {ws.close_code})")
            ok = json.loads(r.data)
            if ok.get("type") != "auth_ok":
                raise ErrorInyeccion(f"respuesta inesperada al auth: {ok}")
            last = ok.get("last_seq") or {}
            res["last_seq_hub"] = last

            async def lector():
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.TEXT:
                        f = json.loads(m.data)
                        if f.get("type") == "rechazado":
                            res["rechazados"].append(f)
                            log(f"RECHAZADO {f.get('session_id')} seq={f.get('seq')}: {'; '.join(f.get('errores', [])[:3])}")

            tarea_lector = asyncio.create_task(lector())
            t_prev = None
            arrancado: set[str] = set()
            for m in msgs:
                sid, seq = m.get("session_id"), m.get("seq")
                if seq is not None and isinstance(last.get(sid), int) and seq <= last[sid]:
                    res["salteados"] += 1
                    continue
                if (seq is None and m.get("type") != "translation" and sid in last
                        and sid not in arrancado):
                    res["salteados"] += 1  # latido o parcial de un tramo que el hub ya tiene
                    continue
                arrancado.add(sid)
                te = m.get("t_emit")
                if t_prev is not None and isinstance(te, (int, float)) and velocidad > 0:
                    espera = max(0.0, te - t_prev) / velocidad
                    if max_gap is not None:
                        espera = min(espera, max_gap)
                    if espera > 0:
                        await asyncio.sleep(espera)
                if isinstance(te, (int, float)):
                    t_prev = te
                await ws.send_str(json.dumps(m, ensure_ascii=False))
                res["enviados"] += 1
            await asyncio.sleep(0.3)  # margen para recibir rechazos de los ultimos mensajes
            tarea_lector.cancel()
            await asyncio.gather(tarea_lector, return_exceptions=True)
    return res


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m hub.inyectar", description=__doc__.splitlines()[0])
    ap.add_argument("archivo")
    ap.add_argument("--hub", default=None, help="URL de ingesta (default ws://localhost:<HUB_PORT>/ingest)")
    ap.add_argument("--velocidad", type=float, default=1.0, help="factor de velocidad (2 = el doble; 0 = sin esperas)")
    ap.add_argument("--session-id", default=None, help="reescribe session_id (misma grabacion como otra sesion)")
    ap.add_argument("--max-gap", type=float, default=None, help="tope en segundos para cada espera")
    ap.add_argument("--token", default=None, help="default: HUB_TOKEN del entorno o .env, si no dev-token")
    args = ap.parse_args(argv)
    cfg = cargar_config()
    url = args.hub or f"ws://localhost:{cfg.port}/ingest"
    token = args.token or cfg.token
    try:
        msgs = cargar(args.archivo, args.session_id)
    except (ErrorInyeccion, OSError) as e:
        print(f"ERROR {e}")
        return 2
    sesiones = sorted({m.get("session_id") for m in msgs})
    print(f"inyectar: {len(msgs)} mensajes de {args.archivo} -> {url} (sesiones={sesiones}, "
          f"velocidad={args.velocidad}, replay=true forzado)")
    t0 = time.time()
    try:
        res = asyncio.run(inyectar(msgs, url, token, args.velocidad, args.max_gap))
    except ErrorInyeccion as e:
        print(f"ERROR {e}")
        return 2
    print(f"inyectar: enviados={res['enviados']} salteados(ya en el hub)={res['salteados']} "
          f"rechazados={len(res['rechazados'])} last_seq_hub_al_conectar={res['last_seq_hub']} "
          f"en {time.time() - t0:.1f} s")
    return 1 if res["rechazados"] else 0


if __name__ == "__main__":
    sys.exit(main())
