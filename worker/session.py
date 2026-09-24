"""SessionWorker: una sesion de ASR por sala, desacoplada del transporte (worker/transporte.py).

B1: camino feliz de UNA sesion. Sin rotacion (B4) ni watchdog (B3): al GoAway se registra
`goaway_seen`, se sigue mandando audio hasta que el server cierra, se registra el cierre y se termina.

- Emite el `seq` (monotonico por sesion; heartbeat = null). Historial canonico: el Emisor.
- Todo queda en el casete: server crudo, client (turnos y ventanas), emit (mensajes del contrato).
- Solape y gap salen en rafaga SIN dormir; la unica espera del emisor de audio es la del gap
  (>= 0,7 s de reloj de pared entre activity_end y activity_start), y solo si hace falta.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional

from worker.casete import Grabador, kind_server
from worker.contrato import SIN_SEQ, mensaje
from worker.emisor import Emisor
from worker.ingesta import CHUNK_S, Chunk
from worker.mapeo import InfoVentana, Mapeador, segundos as _segundos_mapeo
from worker.transporte import Transporte
from worker.ventanas import GAP_S, Accion, Cortador, Ventana


@dataclass
class Resultado:
    session_id: str
    chunks_enviados: int = 0
    ventanas: int = 0
    textos: int = 0
    mensajes_server: int = 0
    goaway: Optional[dict] = None
    cierre: Optional[dict] = None
    motivo_fin: str = ""
    seq_final: int = 0
    t_inicio: float = 0.0
    t_fin: float = 0.0
    errores: list = field(default_factory=list)

    @property
    def segundos_enviados(self) -> float:
        return round(self.chunks_enviados * CHUNK_S, 1)


def _segundos(offset) -> Optional[float]:
    """'2.200s' -> 2.2 (formato Duration de protobuf en JSON)."""
    if offset is None:
        return None
    try:
        return float(str(offset).rstrip("s"))
    except ValueError:
        return None


def extraer_texto(msg: dict) -> Optional[str]:
    sc = msg.get("serverContent") or {}
    tx = (sc.get("inputTranscription") or {}).get("text")
    return tx if tx else None


class SessionWorker:
    def __init__(self, session_id: str, lang: str, transporte: Transporte,
                 fuente: AsyncIterator[Chunk], grabador: Optional[Grabador] = None,
                 emisor: Optional[Emisor] = None, titulo: str = "", source: Optional[dict] = None,
                 cortador: Optional[Cortador] = None, tope_envio_s: Optional[float] = None,
                 heartbeat_s: float = 5.0, espera_final_s: float = 30.0, gap_s: float = GAP_S,
                 traductor=None, seq_inicial: int = 0,
                 reloj: Callable[[], float] = time.time,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 log: Callable[[str], None] = print):
        self.sid = session_id
        self.lang = lang
        self.tr = transporte
        self.fuente = fuente
        self.rec = grabador
        self.emisor = emisor
        self.titulo = titulo
        self.source = source or {}
        self.cortador = cortador or Cortador()
        self.tope_chunks = None if tope_envio_s is None else int(tope_envio_s / CHUNK_S)
        self.heartbeat_s = heartbeat_s
        self.espera_final_s = espera_final_s
        self.gap_s = gap_s
        self.reloj = reloj
        self.dormir = dormir
        self.log = log
        self.seq = int(seq_inicial or 0)     # sigue desde auth_ok.last_seq (el hub no rebobina)
        self.traductor = traductor           # worker/traductor.py (R19), o None
        self.n_parciales = 0
        self.drenaje: dict = {}
        self.res = Resultado(session_id)
        self.mapa = Mapeador()                       # textos -> ventanas (worker/mapeo.py)
        self.abierta: Optional[Ventana] = None
        self.ultima: Optional[Ventana] = None
        self._t_ultimo_end: Optional[float] = None
        self._server_cerro = asyncio.Event()
        self._envio_terminado = asyncio.Event()
        self._vivo = False

    # ---- emision ----------------------------------------------------------
    def emitir(self, tipo: str, **campos) -> dict:
        if tipo not in SIN_SEQ:
            self.seq += 1
            seq = self.seq
        else:
            seq = None
        extra_casete = campos.pop("_casete", {})
        msg = mensaje(tipo, self.sid, seq, self.lang, t_emit=self.reloj(), **campos)
        if self.rec:
            self.rec.emit(msg, t=msg["t_emit"], **extra_casete)
        if self.emisor:
            self.emisor.publicar(msg)
        self.res.seq_final = self.seq
        return msg

    # ---- envio de audio ---------------------------------------------------
    async def _ejecutar(self, a: Accion) -> None:
        if a.kind == "start":
            if self._t_ultimo_end is not None:
                falta = self._t_ultimo_end + self.gap_s - self.reloj()
                if falta > 0:
                    await self.dormir(falta)   # la unica espera: el gap, no el audio
            await self.tr.activity_start()
            self.abierta = a.ventana
            if self.rec:
                self.rec.client("activity_start", {"ventana": a.ventana.idx,
                                                   "audio_start": a.ventana.audio_start})
        elif a.kind == "audio":
            await self.tr.enviar_audio(a.chunk.data)
            self.res.chunks_enviados += 1
        elif a.kind == "end":
            await self.tr.activity_end()
            self._t_ultimo_end = self.reloj()
            v = a.ventana
            self.mapa.ventana_cerrada(InfoVentana(v.idx, v.audio_start, v.audio_end, v.t_captured,
                                                  round(self.res.chunks_enviados * CHUNK_S, 3)))
            self.ultima = v
            self.abierta = None
            self.res.ventanas += 1
            if self.rec:
                self.rec.client("ventana", v.resumen(), t=self._t_ultimo_end)
                self.rec.client("activity_end", {"ventana": v.idx, "audio_end": v.audio_end},
                                t=self._t_ultimo_end)

    async def _enviar(self) -> None:
        try:
            async for c in self.fuente:
                if self._server_cerro.is_set():
                    self.res.motivo_fin = self.res.motivo_fin or "server_cerro"
                    break
                if self.tope_chunks is not None and self.res.chunks_enviados >= self.tope_chunks:
                    self.res.motivo_fin = "tope_cuota"
                    self.log(f"[{self.sid}] tope de envio alcanzado "
                             f"({self.res.segundos_enviados} s): se corta")
                    break
                for a in self.cortador.push(c):
                    await self._ejecutar(a)
            else:
                self.res.motivo_fin = self.res.motivo_fin or "fin_fuente"
            if not self._server_cerro.is_set():
                for a in self.cortador.cerrar():
                    await self._ejecutar(a)
        except Exception as e:
            self.res.errores.append(f"envio: {type(e).__name__}: {e}")
            if self.rec:
                self.rec.client("send_error", {"error": f"{type(e).__name__}: {e}",
                                               "server_ya_cerro": self._server_cerro.is_set()})
            if not self._server_cerro.is_set():
                self.log(f"[{self.sid}] error enviando: {type(e).__name__}: {e}")
            self.res.motivo_fin = self.res.motivo_fin or "error_envio"
        finally:
            cerrar_fuente = getattr(self.fuente, "aclose", None)
            if cerrar_fuente:
                try:
                    await cerrar_fuente()
                except Exception:
                    pass
            self._envio_terminado.set()

    # ---- recepcion ------------------------------------------------------------
    def _emitir_asignaciones(self, asignaciones) -> None:
        for a in asignaciones:
            self.res.textos += 1
            # el original sale YA con translations {}; la traduccion llega despues como `translation`
            msg = self.emitir("text", text=a.text, audio_start=a.audio_start, audio_end=a.audio_end,
                              t_captured=a.t_captured, _casete={"ventanas": a.ventanas})
            if self.traductor is not None:
                self.traductor.agregar(msg["seq"], a.text, msg["t_emit"])

    def _on_traduccion(self, res) -> None:
        from worker.traductor import meta_traduccion
        self.emitir("translation", items=res.items,
                    meta=meta_traduccion(res, self.traductor.a, {"vivo": True, "intentos": res.intentos}))

    def _emitir_parcial(self, texto: str) -> None:
        # turno en curso = ventana mas vieja sin ACTIVITY_END del server; si no hay, la abierta
        if self.mapa.pend:
            a0 = self.mapa.pend[0].audio_start
        else:
            a0 = self.abierta.audio_start if self.abierta else None
        tc = self.ultima.t_captured if self.ultima is not None else None
        self.n_parciales += 1
        self.emitir("partial", text=texto, audio_start=a0, t_captured=tc)

    async def _vigilar_textos(self) -> None:
        # fallback: texto sin ACTIVITY_END en ESPERA_MAX_S -> se emite igual
        while True:
            await asyncio.sleep(0.1)
            self._emitir_asignaciones(self.mapa.vencidos(self.reloj()))

    async def _recibir(self) -> None:
        async for m in self.tr.recibir():
            t = self.reloj()
            if "_close" in m:
                self.res.cierre = m["_close"]
                if self.rec:
                    self.rec.server("close", m["_close"], t=t)
                self._server_cerro.set()
                break
            self.res.mensajes_server += 1
            if self.rec:
                self.rec.server(kind_server(m), m, t=t)
            if "goAway" in m:
                self.res.goaway = {"t": t, **(m.get("goAway") or {})}
                if self.rec:
                    self.rec.client("goaway_seen", {"goAway": m.get("goAway"),
                                                    "audio_enviado_s": self.res.segundos_enviados}, t=t)
                self.log(f"[{self.sid}] GoAway recibido: {m.get('goAway')}")
            tx = extraer_texto(m)
            if tx:
                self.mapa.texto(t, tx)
            itx = ((m.get("serverContent") or {}).get("interimInputTranscription") or {}).get("text")
            if itx:
                self._emitir_parcial(itx)
            # ver worker/mapeo.py: el texto se asigna al ACTIVITY_END que le sigue (mismo ms)
            va = m.get("voiceActivity") or {}
            if va.get("type") == "ACTIVITY_END":
                self._emitir_asignaciones(self.mapa.activity_end(_segundos_mapeo(va.get("audioOffset"))))

    async def _latido(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            self.emitir("heartbeat", meta={"alive": self._vivo,
                                           "audio_seconds_sent": self.res.segundos_enviados})

    # ---- ciclo completo ---------------------------------------------------------
    async def correr(self) -> Resultado:
        self.res.t_inicio = self.reloj()
        if self.rec:
            self.rec.client("connect", {"session_id": self.sid, "lang": self.lang})
        try:
            setup = await self.tr.conectar()
        except Exception as e:
            self.res.errores.append(f"connect: {type(e).__name__}: {e}")
            if self.rec:
                self.rec.client("connect_error", {"error": f"{type(e).__name__}: {e}"})
            self.emitir("error", meta={"code": "connect", "message": f"{type(e).__name__}: {e}"})
            self.res.motivo_fin = "error_connect"
            self.res.t_fin = self.reloj()
            return self.res
        self._vivo = True
        if self.rec:
            self.rec.server(kind_server(setup), setup, fuente="sdk")
            cfg = getattr(self.tr, "config_enviada", None)
            if callable(cfg):
                self.rec.client("config", cfg())
        self.emitir("session_start", meta={"title": self.titulo, "source": self.source})
        if self.traductor is not None:
            self.traductor.on_resultado = self._on_traduccion
            self.traductor.iniciar()
        rx = asyncio.create_task(self._recibir())
        hb = asyncio.create_task(self._latido())
        vt = asyncio.create_task(self._vigilar_textos())
        tx = asyncio.create_task(self._enviar())
        await self._envio_terminado.wait()
        # DRENAJE: esperar hasta espera_final_s (30 s) los turnos pendientes (o el cierre del server)
        t0 = time.monotonic()   # reloj real del loop: el inyectable puede estar congelado en tests
        pend0 = len(self.mapa.pend)
        while (self.mapa.pend and not self._server_cerro.is_set()
               and time.monotonic() - t0 < self.espera_final_s):
            await asyncio.sleep(0.1)
        self.drenaje = {"pendientes_al_fin_fuente": pend0, "pendientes_tras_drenaje": len(self.mapa.pend),
                        "espero_s": round(time.monotonic() - t0, 2), "tope_s": self.espera_final_s}
        if self.rec:
            self.rec.client("drenaje", self.drenaje)
        if not self._server_cerro.is_set():
            # colchon corto por si llega texto tardio de la ultima ventana
            try:
                await asyncio.wait_for(self._server_cerro.wait(), 1.5)
            except asyncio.TimeoutError:
                pass
        if not self._server_cerro.is_set():
            if self.rec:
                self.rec.client("close", {"motivo": self.res.motivo_fin,
                                          "pendientes_sin_texto": len(self.mapa.pend)})
            await self.tr.cerrar()
        try:
            await asyncio.wait_for(rx, 5)
        except (asyncio.TimeoutError, Exception):
            rx.cancel()
        await tx
        hb.cancel()
        vt.cancel()
        self._emitir_asignaciones(self.mapa.vaciar())
        if self.traductor is not None:
            await self.traductor.cerrar(15.0)   # ultimo lote + traducciones en vuelo
        self._vivo = False
        cierre = self.res.cierre or {}
        if cierre.get("by") in ("server", "red") and cierre.get("code") not in (1000, None):
            self.emitir("error", meta={"code": cierre.get("code"),
                                       "message": f"server cerro la sesion: {cierre.get('reason')}"})
        if cierre.get("by") in ("server", "red") and not self.res.motivo_fin.startswith("tope"):
            self.res.motivo_fin = "server_cerro" + ("_tras_goaway" if self.res.goaway else "")
        self.emitir("session_end", meta={"reason": self.res.motivo_fin,
                                         "audio_seconds_sent": self.res.segundos_enviados})
        self.res.t_fin = self.reloj()
        return self.res
