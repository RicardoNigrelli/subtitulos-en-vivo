"""Prueba de fan-out (C3, eje 2: espectadores por sesion): N clientes sinteticos sobre UNA sesion.

    python -m hub.carga --hub http://localhost:8192 --clientes 200 --mensajes 60 --muertos 20 --colgados 5

1. Abre N clientes de audiencia (/ws/<sesion>?lang=es|en alternado) y espera el init de todos.
2. Abre --muertos clientes que completan el handshake y cortan el socket sin leer, y --colgados
   que completan el handshake y despues nunca leen.
3. Un productor (token por primer frame) manda --mensajes mensajes SINTETICOS
   ("Mensaje de carga N", replay=true, meta.source="carga-sintetica") cada --intervalo s.
4. Mide por cliente vivo: seq recibidos (sin huecos ni duplicados) y demora t_recibido - t_hub.
Exit 0: TODOS los vivos recibieron 1..M en orden (mismo ultimo seq). Exit 1: no. Exit 2: sin hub.
No usar contra el hub de 8100 que usa el front: levantar otro (HUB_PORT=8192 python -m hub).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
from urllib.parse import urlparse

import aiohttp

from .config import cargar_config


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]


async def _crudo(host: str, port: int, path: str):
    reader, writer = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    await writer.drain()
    await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    return reader, writer


async def correr(base_http: str, token: str, clientes: int = 200, mensajes: int = 60,
                 intervalo: float = 0.05, muertos: int = 0, colgados: int = 0,
                 sesion: str | None = None, espera_final: float = 10.0, log=print) -> dict:
    sesion = sesion or f"carga-{int(time.time())}"
    u = urlparse(base_http)
    base_ws = f"ws://{u.hostname}:{u.port}"
    host = "127.0.0.1" if u.hostname in ("localhost", None) else u.hostname
    recibidos: list[list[int]] = [[] for _ in range(clientes)]
    demoras: list[list[float]] = [[] for _ in range(clientes)]
    inits = 0
    listos = asyncio.Event()
    fin = asyncio.Event()

    conector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=conector) as http:
        async def cliente(i: int):
            nonlocal inits
            lang = "es" if i % 2 == 0 else "en"
            async with http.ws_connect(f"{base_ws}/ws/{sesion}?lang={lang}", heartbeat=None) as ws:
                r = await ws.receive(timeout=15)
                assert json.loads(r.data)["type"] == "init"
                inits += 1
                if inits == clientes:
                    listos.set()
                while not fin.is_set():
                    try:
                        r = await ws.receive(timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if r.type != aiohttp.WSMsgType.TEXT:
                        break
                    t = time.time()
                    f = json.loads(r.data)
                    if f.get("type") == "text":
                        recibidos[i].append(f["seq"])
                        demoras[i].append(t - f["t_hub"])

        t_con = time.monotonic()
        tareas = [asyncio.create_task(cliente(i)) for i in range(clientes)]
        await asyncio.wait_for(listos.wait(), timeout=60)
        log(f"carga: {clientes} clientes vivos conectados a {sesion} en {time.monotonic() - t_con:.2f} s")

        socks = []
        for _ in range(muertos):
            _, w = await _crudo(host, u.port, f"/ws/{sesion}?lang=es")
            w.transport.abort()
        for _ in range(colgados):
            socks.append(await _crudo(host, u.port, f"/ws/{sesion}?lang=es"))
        log(f"carga: {muertos} muertos (cortan sin leer) y {colgados} colgados (nunca leen) conectados")

        ws = await http.ws_connect(f"{base_ws}/ingest")
        await ws.send_str(json.dumps({"type": "auth", "token": token}))
        ok = json.loads((await ws.receive(timeout=5)).data)
        if ok.get("type") != "auth_ok":
            raise RuntimeError(f"auth rechazada: {ok}")
        relleno = "texto sintetico de carga " * 40  # ~1 KB por mensaje
        t0 = 1790262000.0
        t_envio = time.monotonic()
        for n in range(1, mensajes + 1):
            ahora = time.time()
            await ws.send_str(json.dumps({
                "v": 1, "type": "text", "session_id": sesion, "seq": n, "lang": "en",
                "text": f"Mensaje de carga {n} de {mensajes}. {relleno}",
                "translations": {"es": {"text": f"Mensaje de carga {n} de {mensajes}.", "ok": True}},
                "audio_start": 3.0 * (n - 1), "audio_end": 3.0 * n,
                "t_captured": max(t0, ahora - 1.0), "t_emit": ahora, "replay": True,
                "meta": {"source": "carga-sintetica"}}))
            if intervalo > 0:
                await asyncio.sleep(intervalo)
        dur_envio = time.monotonic() - t_envio
        limite = time.monotonic() + espera_final
        while time.monotonic() < limite and not all(len(r) >= mensajes for r in recibidos):
            await asyncio.sleep(0.1)
        fin.set()
        await ws.close()
        await asyncio.gather(*tareas, return_exceptions=True)
        async with http.get(f"{base_http}/api/metricas", headers={"Authorization": f"Bearer {token}"}) as r:
            met = (await r.json())["sesiones"].get(sesion, {})
        for _, w in socks:
            w.transport.abort()

    esperado = list(range(1, mensajes + 1))
    completos = sum(1 for r in recibidos if r == esperado)
    ultimos = sorted({r[-1] if r else None for r in recibidos}, key=lambda x: (x is None, x))
    todas = [d for ds in demoras for d in ds]
    res = {
        "sesion": sesion, "clientes": clientes, "mensajes": mensajes, "completos": completos,
        "ultimos_seq_distintos": ultimos, "envio_s": round(dur_envio, 2),
        "demora_p50_ms": round(1000 * statistics.median(todas), 1) if todas else None,
        "demora_p95_ms": round(1000 * _pct(todas, 95), 1) if todas else None,
        "demora_max_ms": round(1000 * max(todas), 1) if todas else None,
        "entregas": len(todas),
        "hub_descartados_lentos": met.get("descartados_lentos"),
        "hub_descartados_error": met.get("descartados_error"),
        "hub_max_clientes": met.get("max_clientes"),
        "ok": completos == clientes,
    }
    return res


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m hub.carga", description=__doc__.splitlines()[0])
    ap.add_argument("--hub", default=None, help="base http:// del hub (default http://localhost:<HUB_PORT>)")
    ap.add_argument("--clientes", type=int, default=200)
    ap.add_argument("--mensajes", type=int, default=60)
    ap.add_argument("--intervalo", type=float, default=0.05)
    ap.add_argument("--muertos", type=int, default=0)
    ap.add_argument("--colgados", type=int, default=0)
    ap.add_argument("--sesion", default=None)
    ap.add_argument("--token", default=None)
    args = ap.parse_args(argv)
    cfg = cargar_config()
    base = args.hub or f"http://localhost:{cfg.port}"
    try:
        res = asyncio.run(correr(base, args.token or cfg.token, args.clientes, args.mensajes, args.intervalo,
                                 args.muertos, args.colgados, args.sesion))
    except (aiohttp.ClientError, OSError) as e:
        print(f"ERROR no se pudo conectar al hub {base}: {e}")
        return 2
    print("carga: " + json.dumps(res, ensure_ascii=False))
    print(f"RESULTADO: {res['completos']}/{res['clientes']} clientes vivos recibieron los {res['mensajes']} "
          f"mensajes en orden (ultimo seq: {res['ultimos_seq_distintos']}); demora hub->cliente "
          f"p50={res['demora_p50_ms']} ms p95={res['demora_p95_ms']} ms max={res['demora_max_ms']} ms; "
          f"hub descarto lentos={res['hub_descartados_lentos']} error={res['hub_descartados_error']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
