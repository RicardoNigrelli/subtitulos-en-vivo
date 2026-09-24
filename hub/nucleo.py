"""Nucleo del hub: sesiones, historial, idempotencia por session_id+seq y fan-out best-effort.

Reglas (contracts/README.md):
- El `seq` lo emite el worker. El hub NUNCA renumera ni rebobina: guarda, deduplica y reparte.
- Fan-out: una asyncio.Queue(maxsize=queue_max) por cliente y una tarea de envio por cliente.
  El mensaje se serializa UNA vez y se encola en todos (put_nowait: nunca espera). Si la cola de un
  cliente se llena o su envio falla/tarda, se desconecta ESE cliente; los demas no se enteran.
- Nada de lo que hace `ingerir` espera (sin await): un cliente lento no puede frenar la ingesta.
- B2 (aditivo): `translation` se MERGEA en el `text` guardado con ese session_id+seq
  (translations[lang_to] = {text, ok}); si el text todavia no llego, el item queda pendiente
  HUB_PENDIENTE_S segundos. `partial` se reparte en vivo y NO se guarda. Ninguno toca last_seq.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from typing import Any

from contracts import TIPOS_AUDIENCIA, errores

from .config import Config

log = logging.getLogger("hub")

CIERRE_LENTO = 1013   # "try again later": se le lleno la cola, que reconecte
CIERRE_APAGADO = 1001  # going away


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class Cliente:
    """Un espectador conectado a /ws/<session_id>."""

    __slots__ = ("ws", "session_id", "lang", "cola", "tarea", "cerrado", "transporte",
                 "t_conexion", "enviados", "motivo")

    def __init__(self, ws, session_id: str, lang: str | None, queue_max: int, transporte=None):
        self.ws = ws
        self.session_id = session_id
        self.lang = lang
        self.cola: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_max)
        self.tarea: asyncio.Task | None = None
        self.cerrado = False
        self.transporte = transporte
        self.t_conexion = time.time()
        self.enviados = 0
        self.motivo: str | None = None

    def ofrecer(self, data: str) -> bool:
        if self.cerrado:
            return False
        try:
            self.cola.put_nowait(data)
            return True
        except asyncio.QueueFull:
            return False


class Sesion:
    def __init__(self, session_id: str, history: int):
        self.session_id = session_id
        self.conocida = False  # True cuando llego algo por /ingest (si no, solo tiene espectadores esperando)
        self.lang: str | None = None
        self.title: str | None = None
        self.source: str | None = None
        self.replay: bool | None = None
        self.last_seq: int | None = None
        self.last_t_emit: float | None = None
        self.last_t_hub: float | None = None
        self.t_primera: float | None = None
        self.terminada = False
        self.historial: deque[dict] = deque(maxlen=history)
        self.vistos: set[int] = set()
        # B2: merge de traducciones
        self.textos: dict[int, dict] = {}   # seq -> mensaje type=text GUARDADO (el mismo objeto del historial)
        self.pendientes: dict[int, dict[str, tuple[float, dict]]] = {}  # seq -> {lang_to: (t_llegada, {text, ok})}
        self.idiomas_destino: set[str] = set()
        self.clientes: set[Cliente] = set()
        self.latido_worker: dict | None = None
        # contadores (para /api/metricas y el panel)
        self.recibidos = 0
        self.duplicados = 0
        self.rechazados = 0
        self.latidos_worker = 0
        self.descartados_lentos = 0
        self.descartados_error = 0
        self.max_clientes = 0
        self.traducciones = 0          # eventos translation validos recibidos
        self.traducciones_sin_cambio = 0  # duplicados exactos: no se reenvian
        self.items_aplicados = 0       # items mergeados en un text guardado (incluye pendientes aplicados)
        self.items_pendientes = 0      # items que llegaron antes que su text
        self.items_vencidos = 0        # pendientes descartados tras HUB_PENDIENTE_S (o por el tope)
        self.items_huerfanos = 0       # seq de otro tipo o fuera del historial
        self.parciales = 0

    def estado(self, ahora: float, ventana: float) -> str:
        if self.terminada:
            return "ended"
        if self.last_t_hub is not None and ahora - self.last_t_hub <= ventana:
            return "live"
        return "idle"

    def lineas(self, n: int) -> list[dict]:
        textos = sorted((m for m in self.historial if m["type"] == "text"), key=lambda m: m["seq"])
        return textos[-n:] if n > 0 else []

    def resumen(self, ahora: float, ventana: float) -> dict:
        return {
            "session_id": self.session_id,
            "lang": self.lang,
            "title": self.title,
            "replay": self.replay,
            "last_seq": self.last_seq,
            "last_t_emit": self.last_t_emit,
            "state": self.estado(ahora, ventana),
            "source": self.source,
            "last_t_hub": self.last_t_hub,
            "viewers": len(self.clientes),
            "viewers_por_idioma": dict(Counter(c.lang or "-" for c in self.clientes)),
            "translations_langs": sorted(self.idiomas_destino),
        }

    def metricas(self) -> dict:
        return {
            "recibidos": self.recibidos, "duplicados": self.duplicados, "rechazados": self.rechazados,
            "latidos_worker": self.latidos_worker, "descartados_lentos": self.descartados_lentos,
            "descartados_error": self.descartados_error, "clientes": len(self.clientes),
            "max_clientes": self.max_clientes, "en_historial": len(self.historial),
            "latido_worker": self.latido_worker,
            "traducciones": self.traducciones, "traducciones_sin_cambio": self.traducciones_sin_cambio,
            "items_aplicados": self.items_aplicados, "items_pendientes": self.items_pendientes,
            "items_vencidos": self.items_vencidos, "items_huerfanos": self.items_huerfanos,
            "pendientes_ahora": sum(len(v) for v in self.pendientes.values()),
            "parciales": self.parciales,
        }

    # -------------------------------------------------------------- B2: merge de traducciones
    def aplicar(self, destino: dict, lang_to: str, tr: dict) -> bool:
        """translations[lang_to] = tr en el text guardado. Idempotente. Un ok:false no pisa un ok:true.
        Devuelve True si cambio algo."""
        actual = destino["translations"].get(lang_to)
        if actual == tr:
            return False
        if actual is not None and actual.get("ok") and not tr["ok"]:
            return False
        destino["translations"][lang_to] = tr
        self.items_aplicados += 1
        return True

    def purgar_pendientes(self, ahora: float, vida_s: float) -> int:
        """Descarta los items pendientes mas viejos que vida_s. Devuelve cuantos descarto."""
        n = 0
        for seq in list(self.pendientes):
            por_lang = self.pendientes[seq]
            for lang_to in [k for k, (t, _) in por_lang.items() if ahora - t > vida_s]:
                del por_lang[lang_to]
                n += 1
            if not por_lang:
                del self.pendientes[seq]
        self.items_vencidos += n
        return n


class Hub:
    def __init__(self, config: Config):
        self.config = config
        self.sesiones: dict[str, Sesion] = {}
        self.t_inicio = time.time()
        self.productores: set = set()
        self.auth_fallidas = 0
        self.rechazados_sin_sesion = 0
        self._cierres: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ sesiones
    def sesion(self, session_id: str) -> Sesion:
        s = self.sesiones.get(session_id)
        if s is None:
            s = self.sesiones[session_id] = Sesion(session_id, self.config.history)
        return s

    def conocidas(self) -> list[Sesion]:
        return sorted((s for s in self.sesiones.values() if s.conocida), key=lambda s: s.session_id)

    def last_seq(self) -> dict[str, int]:
        return {s.session_id: s.last_seq for s in self.conocidas() if s.last_seq is not None}

    # ------------------------------------------------------------------ ingesta
    def ingerir(self, msg: Any) -> tuple[str, list[str]]:
        """Devuelve (estado, errores). estado: ok | duplicado | latido | rechazado. No espera nunca."""
        errs = errores(msg)
        if errs:
            sid = msg.get("session_id") if isinstance(msg, dict) else None
            s = self.sesiones.get(sid) if isinstance(sid, str) else None
            if s is not None:
                s.rechazados += 1
            else:
                self.rechazados_sin_sesion += 1
            return "rechazado", errs

        ahora = time.time()
        s = self.sesion(msg["session_id"])
        if not s.conocida:
            s.conocida, s.t_primera = True, ahora
            log.info("sesion nueva: %s (lang=%s, replay=%s)", s.session_id, msg.get("lang"), msg.get("replay"))
        s.last_t_hub = ahora
        if msg.get("lang"):
            s.lang = msg["lang"]
        if "replay" in msg:
            s.replay = msg["replay"]

        if msg["type"] == "heartbeat":
            s.latidos_worker += 1
            s.latido_worker = {"t_emit": msg.get("t_emit"), "t_hub": ahora, "meta": msg.get("meta")}
            return "latido", []
        if msg["type"] == "partial":
            # En vivo y nada mas: no se guarda, no se deduplica (no tiene seq), no toca last_seq.
            s.parciales += 1
            self.difundir(s, dumps({**msg, "t_hub": ahora}))
            return "parcial", []
        if msg["type"] == "translation":
            return self._traduccion(s, msg, ahora)

        seq = msg["seq"]
        if seq in s.vistos:
            s.duplicados += 1
            if msg["type"] == "session_start":
                log.warning("sesion %s: session_start con seq=%s ya visto (last_seq=%s). Si el worker "
                            "reinicio con numeracion nueva, el hub NO rebobina: usar otro session_id o "
                            "seguir desde last_seq+1 (auth_ok).", s.session_id, seq, s.last_seq)
            return "duplicado", []
        s.vistos.add(seq)
        s.recibidos += 1
        if s.terminada and s.last_seq is not None and seq > s.last_seq:
            log.warning("sesion %s: llega seq=%s (%s) despues de session_end (last_seq=%s): se reabre. "
                        "Si es otra corrida del worker con el mismo session_id, sus seq <= %s se pierden.",
                        s.session_id, seq, msg["type"], s.last_seq, s.last_seq)

        m = dict(msg)
        m["t_hub"] = ahora
        if m["type"] == "text":
            # copia propia: el merge la modifica en el lugar (historial e init devuelven lo mergeado)
            m["translations"] = dict(m.get("translations") or {})
            s.idiomas_destino.update(m["translations"])
            for lang_to, (_t, tr) in (s.pendientes.pop(seq, None) or {}).items():
                s.aplicar(m, lang_to, tr)  # la traduccion llego antes que el text: se aplica ahora
            s.textos[seq] = m
        if m["type"] == "session_start":
            meta = m.get("meta") or {}
            s.title, s.source = meta.get("title"), meta.get("source")
        if s.last_seq is None or seq > s.last_seq:
            s.last_seq, s.last_t_emit = seq, m.get("t_emit")
            s.terminada = m["type"] == "session_end"
        if s.historial.maxlen is not None and len(s.historial) == s.historial.maxlen:
            viejo = s.historial[0]  # el append de abajo lo saca del historial: tambien del indice
            if s.textos.get(viejo["seq"]) is viejo:
                del s.textos[viejo["seq"]]
        s.historial.append(m)
        if m["type"] in TIPOS_AUDIENCIA:
            self.difundir(s, dumps(m))
        return "ok", []

    def _traduccion(self, s: Sesion, msg: dict, ahora: float) -> tuple[str, list[str]]:
        """Mergea cada item en el text guardado (o lo deja pendiente) y reenvia el evento en vivo
        tal cual, salvo que no haya cambiado nada (duplicado exacto)."""
        s.traducciones += 1
        lang_to = msg["meta"]["lang_to"]
        s.idiomas_destino.add(lang_to)
        cambios = 0
        for it in msg["items"]:
            seq, tr = it["seq"], {"text": it["text"], "ok": it["ok"]}
            destino = s.textos.get(seq)
            if destino is not None:
                cambios += s.aplicar(destino, lang_to, tr)
            elif seq in s.vistos:
                s.items_huerfanos += 1  # ese seq es de otro tipo o ya salio del historial
            else:
                previo = s.pendientes.get(seq, {}).get(lang_to)
                if previo is not None and (previo[1] == tr or (previo[1]["ok"] and not tr["ok"])):
                    continue
                s.pendientes.setdefault(seq, {})[lang_to] = (ahora, tr)
                s.items_pendientes += 1
                cambios += 1
        exceso = len(s.pendientes) - self.config.pendientes_max
        if exceso > 0:  # tope de memoria: se van los seq pendientes mas viejos
            viejos = sorted(s.pendientes, key=lambda k: min(t for t, _ in s.pendientes[k].values()))
            for seq in viejos[:exceso]:
                s.items_vencidos += len(s.pendientes.pop(seq))
        if not cambios:
            s.traducciones_sin_cambio += 1
            return "duplicado", []
        self.difundir(s, dumps({**msg, "t_hub": ahora}))
        return "ok", []

    # ------------------------------------------------------------------ audiencia
    def suscribir(self, ws, session_id: str, lang: str | None, transporte=None) -> Cliente:
        """Registra al cliente y le encola el `init` en el MISMO paso (sin await en el medio):
        todo mensaje que llegue despues queda detras del init, sin huecos ni duplicados."""
        s = self.sesion(session_id)
        c = Cliente(ws, session_id, lang, self.config.queue_max, transporte)
        ahora = time.time()
        init = {
            "type": "init",
            "v": 1,
            "session_id": session_id,
            "lang": lang or s.lang,
            "session_lang": s.lang,
            "last_seq": s.last_seq,
            "state": s.estado(ahora, self.config.live_s) if s.conocida else "waiting",
            "title": s.title,
            "replay": s.replay,
            "t_hub": ahora,
            "translations_langs": sorted(s.idiomas_destino),
            "lines": s.lineas(self.config.init_lines),
        }
        c.cola.put_nowait(dumps(init))
        s.clientes.add(c)
        s.max_clientes = max(s.max_clientes, len(s.clientes))
        return c

    def difundir(self, s: Sesion, data: str) -> None:
        for c in list(s.clientes):
            if not c.ofrecer(data):
                self.desconectar(c, "lento")

    def desconectar(self, c: Cliente, motivo: str) -> None:
        """Saca al cliente del fan-out YA (sin await) y lo cierra en segundo plano."""
        if c.cerrado:
            return
        c.cerrado, c.motivo = True, motivo
        s = self.sesiones.get(c.session_id)
        if s is not None:
            s.clientes.discard(c)
            if motivo == "lento":
                s.descartados_lentos += 1
            elif motivo == "error_envio":
                s.descartados_error += 1
            if not s.conocida and not s.clientes:
                self.sesiones.pop(c.session_id, None)
        if c.tarea is not None and c.tarea is not asyncio.current_task():
            c.tarea.cancel()
        if motivo in ("lento", "error_envio", "apagado"):
            log.info("cliente %s/%s desconectado: %s", c.session_id, c.lang, motivo)
            codigo = CIERRE_APAGADO if motivo == "apagado" else CIERRE_LENTO
            t = asyncio.ensure_future(self._cerrar(c, codigo, motivo))
            self._cierres.add(t)
            t.add_done_callback(self._cierres.discard)

    async def _cerrar(self, c: Cliente, codigo: int, motivo: str) -> None:
        try:
            await asyncio.wait_for(c.ws.close(code=codigo, message=motivo.encode()), timeout=2.0)
        except Exception:
            pass
        finally:
            if not c.ws.closed and c.transporte is not None:
                c.transporte.abort()  # no leyo ni el close: se corta el socket

    async def enviador(self, c: Cliente) -> None:
        """Una tarea por cliente: vacia SU cola. Si un envio falla o tarda, cae solo el."""
        try:
            while True:
                data = await c.cola.get()
                await asyncio.wait_for(c.ws.send_str(data), timeout=self.config.send_timeout_s)
                c.enviados += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            self.desconectar(c, "error_envio")

    async def latidos(self) -> None:
        """Cada heartbeat_s: un heartbeat por sesion con espectadores (serializado una vez)."""
        while True:
            await asyncio.sleep(self.config.heartbeat_s)
            ahora = time.time()
            for s in list(self.sesiones.values()):
                if s.pendientes:
                    n = s.purgar_pendientes(ahora, self.config.pendiente_s)
                    if n:
                        log.info("sesion %s: %d item(s) de traduccion vencidos sin su text (%.0f s)",
                                 s.session_id, n, self.config.pendiente_s)
                if not s.clientes:
                    continue
                self.difundir(s, dumps({
                    "v": 1, "type": "heartbeat", "session_id": s.session_id, "seq": None,
                    "t_hub": ahora, "last_seq": s.last_seq,
                    "state": s.estado(ahora, self.config.live_s) if s.conocida else "waiting",
                }))

    async def apagar(self) -> None:
        for s in list(self.sesiones.values()):
            for c in list(s.clientes):
                self.desconectar(c, "apagado")
        for ws in list(self.productores):
            try:
                await asyncio.wait_for(ws.close(code=CIERRE_APAGADO, message=b"hub apagandose"), 2.0)
            except Exception:
                pass
        if self._cierres:
            await asyncio.wait(list(self._cierres), timeout=3.0)

    # ------------------------------------------------------------------ vistas
    def metricas(self) -> dict:
        ahora = time.time()
        return {
            "t_hub": ahora,
            "uptime_s": round(ahora - self.t_inicio, 1),
            "productores": len(self.productores),
            "auth_fallidas": self.auth_fallidas,
            "rechazados_sin_sesion": self.rechazados_sin_sesion,
            "clientes": sum(len(s.clientes) for s in self.sesiones.values()),
            "sesiones": {s.session_id: {**s.resumen(ahora, self.config.live_s), **s.metricas()}
                         for s in self.sesiones.values()},
        }
