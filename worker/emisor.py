"""Emisor al hub (bus /ingest) con historial canonico y backlog.

Protocolo (brief B1): WS /ingest; primer frame {"type":"auth","token":HUB_TOKEN}; respuesta
{"type":"auth_ok","last_seq":{session_id: seq}}; despues un mensaje del contrato por frame.
Al (re)conectar se reenvia el backlog con seq > last_seq. Historial en memoria: ultimos 1000 por
sesion. Heartbeats (seq null) no van al historial: se mandan solo si hay conexion.
Si el hub no esta, el worker sigue (casete + log de lo que hubiera emitido) y reintenta con backoff.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from typing import Callable, Optional

HISTORIAL_MAX = 1000


def token_hub() -> str:
    try:
        from worker.gemini import _cargar_env
        _cargar_env()
    except Exception:
        pass
    return os.environ.get("HUB_TOKEN") or "dev-token"


def _log_stderr(s: str) -> None:
    print(s, file=sys.stderr, flush=True)


class Emisor:
    def __init__(self, url: Optional[str], token: Optional[str] = None,
                 log: Callable[[str], None] = _log_stderr, backoff_max: float = 10.0):
        self.url = url
        self.token = token or token_hub()
        self.log = log
        self.backoff_max = backoff_max
        self.historial: dict[str, deque] = {}
        self.enviado_hasta: dict[str, int] = {}
        self.last_seq_hub: dict[str, int] = {}
        self.volatiles: deque = deque(maxlen=50)
        self.conectado = False
        self.n_enviados = 0
        self.n_conexiones = 0
        self._hay_nuevos = asyncio.Event()
        self._parar = False
        self._tarea: Optional[asyncio.Task] = None

    # ---- API ----------------------------------------------------------------
    def publicar(self, msg: dict) -> None:
        sid = msg.get("session_id")
        if msg.get("seq") is None:
            if self.conectado:
                self.volatiles.append(msg)
        else:
            self.historial.setdefault(sid, deque(maxlen=HISTORIAL_MAX)).append(msg)
            if not self.url or not self.conectado:
                tx = (msg.get("text") or "")[:70]
                self.log(f"[emisor] sin hub, queda en backlog: {sid} seq={msg['seq']} "
                         f"type={msg['type']} {tx!r}")
        self._hay_nuevos.set()

    def pendientes(self) -> int:
        n = 0
        for sid, dq in self.historial.items():
            h = self.enviado_hasta.get(sid, 0)
            n += sum(1 for m in dq if m["seq"] > h)
        return n

    def iniciar(self) -> None:
        if self.url and self._tarea is None:
            self._tarea = asyncio.create_task(self._correr())

    async def vaciar(self, timeout: float = 3.0) -> bool:
        """Espera a que el backlog este enviado (o timeout). True si quedo vacio."""
        if not self.url:
            return False
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.conectado and self.pendientes() == 0 and not self.volatiles:
                return True
            await asyncio.sleep(0.05)
        return self.pendientes() == 0

    async def detener(self) -> None:
        self._parar = True
        self._hay_nuevos.set()
        if self._tarea:
            self._tarea.cancel()
            try:
                await self._tarea
            except (asyncio.CancelledError, Exception):
                pass

    # ---- conexion -------------------------------------------------------------
    async def _correr(self) -> None:
        import websockets

        espera = 0.5
        while not self._parar:
            try:
                async with websockets.connect(self.url, open_timeout=5, max_size=2**22) as ws:
                    await ws.send(json.dumps({"type": "auth", "token": self.token}))
                    resp = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if resp.get("type") != "auth_ok":
                        raise RuntimeError(f"auth rechazada: {str(resp)[:200]}")
                    self.last_seq_hub = {k: int(v) for k, v in (resp.get("last_seq") or {}).items()
                                         if v is not None}
                    # arranca desde lo que el hub ya tiene
                    self.enviado_hasta = dict(self.last_seq_hub)
                    self.conectado = True
                    self.n_conexiones += 1
                    espera = 0.5
                    self.log(f"[emisor] conectado a {self.url} last_seq={self.last_seq_hub}")
                    lector = asyncio.create_task(self._leer(ws))
                    try:
                        while not self._parar:
                            await self._enviar_pendientes(ws)
                            self._hay_nuevos.clear()
                            if self.pendientes() == 0 and not self.volatiles:
                                espera_ev = asyncio.create_task(self._hay_nuevos.wait())
                                done, _ = await asyncio.wait({espera_ev, lector},
                                                             return_when=asyncio.FIRST_COMPLETED)
                                if lector in done:
                                    espera_ev.cancel()
                                    raise ConnectionError("el hub cerro la conexion")
                    finally:
                        lector.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.conectado:
                    self.log(f"[emisor] conexion perdida: {type(e).__name__}: {e}")
                else:
                    self.log(f"[emisor] hub no disponible ({type(e).__name__}: {str(e)[:120]}); "
                             f"reintento en {espera:.1f} s")
            self.conectado = False
            if self._parar:
                break
            await asyncio.sleep(espera)
            espera = min(espera * 2, self.backoff_max)

    async def _enviar_pendientes(self, ws) -> None:
        for sid, dq in list(self.historial.items()):
            h = self.enviado_hasta.get(sid, 0)
            for m in list(dq):
                if m["seq"] > h:
                    await ws.send(json.dumps(m, ensure_ascii=False))
                    self.enviado_hasta[sid] = m["seq"]
                    h = m["seq"]
                    self.n_enviados += 1
        while self.volatiles:
            await ws.send(json.dumps(self.volatiles.popleft(), ensure_ascii=False))

    async def _leer(self, ws) -> None:
        async for raw in ws:
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") in ("error", "nack", "rechazado"):
                self.log(f"[emisor] hub respondio: {str(m)[:200]}")
