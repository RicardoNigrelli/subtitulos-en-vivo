"""Helpers de test: un hub REAL (misma app que `python -m hub`) en un puerto efimero de 127.0.0.1.
Nunca usa 8100 (lo usa frontend en paralelo)."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from hub.app import HUB_KEY, crear_app
from hub.config import Config

TOKEN = "token-de-test"
RAIZ = Path(__file__).resolve().parents[2]
EJEMPLOS = RAIZ / "contracts" / "ejemplos"


class HubDePrueba:
    def __init__(self, runner: web.AppRunner, app: web.Application, port: int):
        self.runner, self.app, self.port = runner, app, port
        self.http = f"http://127.0.0.1:{port}"
        self.ws = f"ws://127.0.0.1:{port}"
        self.ingest = f"{self.ws}/ingest"

    @property
    def nucleo(self):
        return self.app[HUB_KEY]

    async def parar(self):
        await asyncio.wait_for(self.runner.cleanup(), timeout=15)


async def levantar(**kw) -> HubDePrueba:
    kw.setdefault("heartbeat_s", 0.2)
    cfg = Config(host="127.0.0.1", port=0, token=TOKEN, token_origen="explicito", **kw)
    app = crear_app(cfg)
    runner = web.AppRunner(app, shutdown_timeout=2.0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return HubDePrueba(runner, app, port)


# ------------------------------------------------------------------------------ helpers
async def productor(http: aiohttp.ClientSession, h: HubDePrueba, token: str = TOKEN):
    ws = await http.ws_connect(h.ingest)
    await ws.send_str(json.dumps({"type": "auth", "token": token}))
    r = await ws.receive(timeout=5)
    assert r.type == aiohttp.WSMsgType.TEXT, f"sin auth_ok: {r.type} code={ws.close_code}"
    ok = json.loads(r.data)
    assert ok["type"] == "auth_ok"
    return ws, ok


async def recibir(ws, n: int | None = None, timeout: float = 5.0, hasta_tipo: str | None = None,
                  latidos: list | None = None) -> list[dict]:
    """Junta frames que no sean heartbeat hasta tener n, ver hasta_tipo o agotar el timeout."""
    out: list[dict] = []
    fin = time.monotonic() + timeout
    while True:
        falta = fin - time.monotonic()
        if falta <= 0:
            break
        try:
            r = await ws.receive(timeout=falta)
        except asyncio.TimeoutError:
            break
        if r.type != aiohttp.WSMsgType.TEXT:
            break
        f = json.loads(r.data)
        if f.get("type") == "heartbeat":
            if latidos is not None:
                latidos.append(f)
            continue
        out.append(f)
        if n is not None and len(out) >= n:
            break
        if hasta_tipo is not None and f.get("type") == hasta_tipo:
            break
    return out


def texto(sid: str, seq: int, t0: float = 1790262000.0, text: str | None = None, lang: str = "en") -> dict:
    """Mensaje type=text SINTETICO y rotulado (no es una transcripcion)."""
    return {"v": 1, "type": "text", "session_id": sid, "seq": seq, "lang": lang,
            "text": text or f"Mensaje de prueba {seq} de la sesion {sid}.",
            "translations": {"es": {"text": f"Mensaje de prueba {seq}.", "ok": True}},
            "audio_start": 3.0 * (seq - 1), "audio_end": 3.0 * seq,
            "t_captured": t0 + 3.0 * seq, "t_emit": t0 + 3.0 * seq + 1.0,
            "replay": True, "meta": {"source": "test-hub"}}


async def ws_crudo(port: int, path: str):
    """Cliente WebSocket a mano (sin libreria que lea en segundo plano): hace el handshake y
    devuelve (reader, writer). Sirve para simular clientes muertos o colgados."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    await writer.drain()
    cabecera = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    assert b" 101 " in cabecera.split(b"\r\n", 1)[0], cabecera
    return reader, writer
