"""Replay y emisor contra un hub de prueba EFIMERO (websocket real en 127.0.0.1, puerto libre).

El hub de prueba implementa solo el protocolo de /ingest del contrato: auth -> auth_ok con
last_seq, despues un mensaje por frame. Guarda lo recibido. No genera ni modifica texto.
"""
import asyncio
import json
from pathlib import Path

import websockets

from worker.casete import leer
from worker.emisor import Emisor
from worker.replay import reproducir

RAIZ = Path(__file__).resolve().parents[2]
CASETE_60S = RAIZ / "fixtures" / "casetes" / "b1-en-60s.jsonl"


class HubDePrueba:
    def __init__(self, last_seq=None, token="tok"):
        self.last_seq = dict(last_seq or {})
        self.token = token
        self.recibidos = []
        self.server = None
        self.url = None

    async def _handler(self, ws):
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth" or auth.get("token") != self.token:
            await ws.close(4401, "auth")
            return
        await ws.send(json.dumps({"type": "auth_ok", "v": 1, "last_seq": self.last_seq}))
        async for raw in ws:
            m = json.loads(raw)
            self.recibidos.append(m)
            if m.get("seq") is not None:
                sid = m["session_id"]
                self.last_seq[sid] = max(self.last_seq.get(sid, 0), m["seq"])

    async def __aenter__(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/ingest"
        return self

    async def __aexit__(self, *a):
        self.server.close()
        await self.server.wait_closed()


def _emits():
    _, evs = leer(CASETE_60S)
    return [e["payload"] for e in evs if e["dir"] == "emit"]


def test_replay_emite_todos_los_mensajes_con_seq_creciente_y_rotulado():
    originales = _emits()
    assert originales, "el casete no tiene lineas emit"

    async def main():
        async with HubDePrueba() as hub:
            em = Emisor(hub.url, token="tok", log=lambda s: None)
            codigo, enviados = await reproducir(str(CASETE_60S), hub=None, emisor=em,
                                                velocidad=500, log=lambda s: None)
            em.iniciar() if em._tarea is None else None
            await em.vaciar(5)
            await asyncio.sleep(0.2)
            await em.detener()
            return codigo, enviados, hub.recibidos

    codigo, enviados, recibidos = asyncio.run(main())
    assert codigo == 0
    assert len(enviados) == len(originales)
    con_seq = [m for m in recibidos if m.get("seq") is not None]
    assert len(con_seq) == sum(1 for m in originales if m.get("seq") is not None)
    seqs = [m["seq"] for m in con_seq]
    assert all(b > a for a, b in zip(seqs, seqs[1:])), seqs
    assert all(m["replay"] is True for m in recibidos)
    inicio = [m for m in recibidos if m["type"] == "session_start"]
    assert inicio and inicio[0]["meta"]["title"].startswith("REPLAY")
    # los textos son los del casete (lo que Gemini devolvio), sin tocar
    assert [m["text"] for m in recibidos if m["type"] == "text"] == \
           [m["text"] for m in originales if m["type"] == "text"]
    # la latencia original (t_emit - t_captured) se conserva
    for o, r in zip([m for m in originales if m["type"] == "text"],
                    [m for m in enviados if m["type"] == "text"]):
        assert abs((o["t_emit"] - o["t_captured"]) - (r["t_emit"] - r["t_captured"])) < 0.05


def test_replay_renumera_desde_el_last_seq_del_hub():
    async def main():
        async with HubDePrueba(last_seq={"otra-sala": 40}) as hub:
            codigo, enviados = await reproducir(str(CASETE_60S), hub=None, sesion="otra-sala",
                                                emisor=Emisor(hub.url, token="tok", log=lambda s: None),
                                                velocidad=500, log=lambda s: None)
            await asyncio.sleep(0.3)
            return hub.recibidos

    recibidos = asyncio.run(main())
    seqs = [m["seq"] for m in recibidos if m.get("seq") is not None]
    assert seqs and seqs[0] == 41
    assert all(m["session_id"] == "otra-sala" for m in recibidos)


def test_emisor_reenvia_backlog_con_seq_mayor_a_last_seq():
    async def main():
        em = Emisor(None, token="tok", log=lambda s: None)
        for i in range(1, 6):
            em.publicar({"v": 1, "type": "text", "session_id": "s", "seq": i, "text": f"m{i}"})
        async with HubDePrueba(last_seq={"s": 3}) as hub:
            em.url = hub.url
            em.iniciar()
            assert await em.vaciar(5)
            await asyncio.sleep(0.2)
            await em.detener()
            return hub.recibidos

    recibidos = asyncio.run(main())
    assert [m["seq"] for m in recibidos] == [4, 5]
