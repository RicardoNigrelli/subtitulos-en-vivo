"""TRANSPORTE DE TEST / REPLAY (rotulado): NO habla con Gemini. Camino de test, no camino real.

Reproduce las lineas `server` de un casete REAL (fixtures/casetes/) sincronizadas con el audio que
el SessionWorker va ENVIANDO, para probar "reabrir con solape" (worker/session.py) sin API:

- Cada linea server del casete queda anclada al audio ACUMULADO que el cliente original llevaba
  enviado cuando llego (suma de `dur` de las lineas client `ventana`: la misma escala que el
  `audioOffset` del server). Se entrega cuando la conexion bajo prueba alcanza ese acumulado.
  Asi un atasco del casete (offset trabado mientras se enviaba audio) se reproduce igual: el atraso
  que ve el worker = el atraso que tuvo el casete.
- Conexion nueva tras una reapertura (fabrica(corte_s)): arranca en el acumulado del casete que
  corresponde a `corte_s` (posicion de audio de la fuente) y entrega DESDE AHI; los audioOffset se
  reescriben relativos a ese arranque (max(0, off - ref)), como una sesion nueva de verdad.
- `mudo_desde=S` (S en segundos de audio ENVIADO, escala audioOffset): la conexion activa al cruzar S
  deja de entregar TODO (sesion muda: sin textos, sin voiceActivity, sin error). Las conexiones que se
  abran despues funcionan (simula que la sesion nueva anda).
- La linea server `close` del casete se entrega como {"_close": ...} (dispara la reapertura por cierre).
- B7 `red_caida=S` (misma escala que mudo_desde): la conexion activa al cruzar S simula la caida de red
  del intento corto de B5 (fixtures/casetes/b5-qa-b5-en-cierre1006.jsonl): el envio queda TRABADO
  `red_bloqueo_s` segundos reales y despues el receptor ve `red_close` (1006 "sin frame de cierre",
  by "red") y ESE MISMO envio falla con ConnectionError (como ConnectionClosedError del SDK); los
  envios siguientes a esa conexion fallan. Con red_bloqueo_s=None el envio queda colgado para siempre
  (solo lo destraba el tope de envio del SessionWorker). Las conexiones que se abran despues andan.

CLI (via worker.run): --transporte casete:<archivo.jsonl>[:mudo=S]
ROTULO (B4): todo mensaje que sale al bus lleva replay:true y meta.source="transporte-casete", y el
session_start lleva "[TEST] " en meta.title (SessionWorker lee SOURCE_REPLAY del transporte).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from worker.casete import leer
from worker.ingesta import SR

BYTES_POR_S = SR * 2


def _seg(offset) -> Optional[float]:
    try:
        return float(str(offset).rstrip("s"))
    except (TypeError, ValueError):
        return None


@dataclass
class _Linea:
    env: float          # audio acumulado enviado por el cliente original al llegar la linea
    kind: str
    payload: dict
    t: float


class Guion:
    """Las lineas server de un casete, ancladas al audio acumulado enviado."""

    def __init__(self, path: str):
        self.path = str(path)
        self.cab, evs = leer(path)
        self.lineas: list[_Linea] = []
        self.ventanas: list[tuple[float, float]] = []    # (audio_end de la fuente, env tras la ventana)
        env = 0.0
        for e in evs:
            p = e.get("payload") or {}
            if e.get("dir") == "client" and e.get("kind") == "ventana":
                env = round(env + float(p.get("dur") or 0.0), 3)
                self.ventanas.append((float(p.get("audio_end") or 0.0), env))
            elif e.get("dir") == "server" and e.get("fuente") != "sdk":
                self.lineas.append(_Linea(env, e.get("kind", ""), p, e["t"]))
        self.env_total = env

    def env_de_pos(self, pos_s: float) -> float:
        """Acumulado del casete al arrancar una ventana en `pos_s` (fuente): el de la ultima ventana
        que termino antes de pos_s + solape."""
        ref = 0.0
        for fin, env in self.ventanas:
            if fin <= pos_s + 0.45:
                ref = env
            else:
                break
        return ref

    def pos_de_env(self, env_s: float) -> float:
        """Posicion de la fuente cuando el casete llevaba `env_s` enviados (aprox. por ventana)."""
        for fin, env in self.ventanas:
            if env >= env_s:
                return fin
        return self.ventanas[-1][0] if self.ventanas else 0.0


class TransporteCasete:
    """Implementa worker.transporte.Transporte sin red. Rotulado: test/replay."""

    ROTULO = "transporte_casete (test, sin API)"
    # SessionWorker lo lee: TODO mensaje sale con replay:true y meta.source="transporte-casete"
    SOURCE_REPLAY = "transporte-casete"

    def __init__(self, guion: Guion, ref_env: float, fabrica: "FabricaCasete", n: int):
        self.g = guion
        self.ref = ref_env
        self.fab = fabrica
        self.n = n
        self.acc = 0.0
        self.acc_end = 0.0
        self.i = next((k for k, l in enumerate(guion.lineas) if l.env > ref_env or
                       (ref_env == 0.0 and l.env >= 0.0)), len(guion.lineas))
        self.q: asyncio.Queue = asyncio.Queue()
        self.cerrado = False
        self.muda = False
        self.entregadas = 0
        self.descartadas_mudo = 0
        self.caida = False            # B7: esta conexion "perdio la red" (red_caida)

    def config_enviada(self) -> dict:
        return {"model": self.ROTULO,
                "config": {"transporte": self.ROTULO, "casete": self.g.path, "ref_env": self.ref,
                           "mudo_desde": self.fab.mudo_desde, "conexion_n": self.n}}

    async def conectar(self) -> dict:
        return {"setupComplete": {}}

    def _reescribir(self, payload: dict) -> dict:
        va = payload.get("voiceActivity")
        if not va or va.get("audioOffset") is None or self.ref == 0.0:
            return payload
        off = _seg(va["audioOffset"])
        if off is None:
            return payload
        nuevo = dict(payload)
        nuevo["voiceActivity"] = {**va, "audioOffset": f"{max(0.0, off - self.ref):.3f}s"}
        return nuevo

    def _entregar(self) -> None:
        # el server responde un turno despues de su activity_end: se entrega hasta el acumulado del
        # ultimo activity_end (el mudo se evalua con el acumulado corriente)
        pos = self.ref + (self.acc_end if self.fab.turnos else self.acc)
        pos_mudo = self.ref + self.acc
        f = self.fab
        if (f.mudo_desde is not None and not f.mudo_usado and not self.muda
                and pos_mudo >= f.mudo_desde):
            self.muda = True
            f.mudo_usado = True
        while self.i < len(self.g.lineas) and self.g.lineas[self.i].env <= pos + 1e-6:
            linea = self.g.lineas[self.i]
            self.i += 1
            if self.muda:
                self.descartadas_mudo += 1
                continue
            self.entregadas += 1
            if linea.kind == "close":
                if f.close_entregado:      # el cierre es de la sesion original: una sola vez
                    continue
                f.close_entregado = True
                self.q.put_nowait({"_close": {**linea.payload, "by": linea.payload.get("by", "server"),
                                              "casete": True}})
            else:
                self.q.put_nowait(self._reescribir(linea.payload))

    async def _red(self) -> None:
        """B7: caida de red simulada (ver docstring del modulo)."""
        f = self.fab
        if self.caida:
            raise ConnectionError("no close frame received or sent (transporte_casete: red caida)")
        if f.red_caida is None or f.red_usada or self.ref + self.acc < f.red_caida:
            return
        f.red_usada = True
        self.caida = True
        if f.red_bloqueo_s is None:
            await asyncio.Event().wait()          # colgado: nunca vuelve (solo lo cancela un tope)
        await asyncio.sleep(f.red_bloqueo_s)
        self.q.put_nowait({"_close": {**f.red_close, "casete": True, "simulado": True}})
        await asyncio.sleep(0)       # B5: el receptor registro el close 0,6 ms ANTES del send_error
        raise ConnectionError("no close frame received or sent (transporte_casete: red caida)")

    async def enviar_audio(self, data: bytes) -> None:
        if self.cerrado:
            raise ConnectionError("transporte_casete cerrado")
        await self._red()
        self.acc = round(self.acc + len(data) / BYTES_POR_S, 4)
        self._entregar()
        await asyncio.sleep(0)       # deja correr al receptor (tests sin tiempo real)

    async def activity_start(self) -> None:
        if self.cerrado:
            raise ConnectionError("transporte_casete cerrado")
        await self._red()

    async def activity_end(self) -> None:
        if self.cerrado:
            raise ConnectionError("transporte_casete cerrado")
        await self._red()
        self.acc_end = self.acc
        self._entregar()
        await asyncio.sleep(0)

    async def recibir(self) -> AsyncIterator[dict]:
        while True:
            m = await self.q.get()
            yield m
            if "_close" in m:
                self.cerrado = True
                return

    async def cerrar(self) -> None:
        if not self.cerrado:
            self.cerrado = True
            self.q.put_nowait({"_close": {"code": 1000, "reason": "cliente", "by": "client"}})


class FabricaCasete:
    """fabrica(corte_s) -> TransporteCasete. La primera conexion arranca en 0."""

    def __init__(self, casete: str, mudo_desde: Optional[float] = None,
                 red_caida: Optional[float] = None, red_bloqueo_s: Optional[float] = 0.0,
                 red_close: Optional[dict] = None):
        self.guion = Guion(casete)
        self.mudo_desde = mudo_desde
        self.mudo_usado = False
        self.red_caida = red_caida
        self.red_bloqueo_s = red_bloqueo_s
        self.red_close = red_close or {"code": 1006, "reason": "sin frame de cierre", "by": "red"}
        self.red_usada = False
        self.close_entregado = False
        self.turnos = True          # False si el worker corre con VAD automatico (sin activity_end)
        self.n = 0
        self.conexiones: list[TransporteCasete] = []

    @classmethod
    def desde_spec(cls, spec: str) -> "FabricaCasete":
        """'casete:<archivo>[:mudo=S]' (el archivo puede traer 'E:\\...' en Windows)."""
        resto = spec[len("casete:"):] if spec.startswith("casete:") else spec
        mudo = None
        m = re.search(r":mudo=([0-9.]+)$", resto)
        if m:
            mudo = float(m.group(1))
            resto = resto[:m.start()]
        return cls(resto, mudo)

    def __call__(self, corte_s: float) -> TransporteCasete:
        ref = 0.0 if self.n == 0 else self.guion.env_de_pos(corte_s)
        self.n += 1
        tr = TransporteCasete(self.guion, ref, self, self.n)
        self.conexiones.append(tr)
        return tr
