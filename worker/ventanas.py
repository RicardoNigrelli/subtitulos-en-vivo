"""Cortador de ventanas para turnos manuales (skill gemini-live, "hallazgo principal").

- Ventana objetivo 3 s, tolerancia +-0,8 s (min 2,2 s, max 3,8 s), medida sobre TODO el audio de la
  ventana (solape + audio del gap + audio en vivo).
- Corte en VALLE de energia: pasado el minimo, se corta despues del primer chunk de 100 ms con
  RMS <= umbral. Umbral = percentil 25 del RMS de los chunks de los primeros 10 s (mientras no hay
  10 s, se usa lo visto hasta ahora; nunca un valor constante). Sin valle al llegar al maximo: corte
  forzado ("max").
- Solape 400 ms: la ventana n+1 arranca reenviando los ultimos 400 ms de la ventana n.
- Gap 0,7 s entre activity_end y el siguiente activity_start. El audio que llega durante el gap se
  guarda y se reenvia al abrir la ventana siguiente. Solape + gap salen como rafaga (burst=True):
  el SessionWorker los manda SIN dormir.

El cortador es puro (sin reloj ni IO): consume Chunk y devuelve Acciones; se prueba sin dormir.
El gap se cuenta en reloj de audio (7 chunks de 100 ms); el SessionWorker ademas garantiza
>= 0,7 s de reloj de pared entre activity_end y activity_start.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from worker.ingesta import CHUNK_S, Chunk, rms_pcm16

import os as _os

# LATENCIA 25/09: ventana configurable por env VENTANA_S (o --ventana-s); la tolerancia escala con la
# ventana (0,8 s a 3 s -> 0,53 s a 2 s): tolerancia_para(). El gap de 0,7 s NO escala (sin gap el server
# descarta turnos).
VENTANA_S = float(_os.environ.get("VENTANA_S") or 3.0)
TOLERANCIA_BASE_S = 0.8
TOLERANCIA_S = round(TOLERANCIA_BASE_S * VENTANA_S / 3.0, 3)


def tolerancia_para(ventana_s: float) -> float:
    return round(TOLERANCIA_BASE_S * float(ventana_s) / 3.0, 3)
SOLAPE_S = 0.4
GAP_S = 0.7
PERCENTIL = 25
CALIBRACION_S = 10.0
# has_voice: >= 3 chunks (300 ms) con RMS > max(2 x umbral, PISO_RMS). [SUPUESTO: audio-pipeline]
PISO_RMS = 100.0
VOZ_FACTOR = 2.0
VOZ_MIN_CHUNKS = 3


@dataclass
class Ventana:
    idx: int
    audio_start: float
    audio_end: float = 0.0
    bytes: int = 0
    n_chunks: int = 0
    burst_chunks: int = 0
    rms: float = 0.0
    has_voice: bool = False
    motivo: str = ""
    umbral: float = 0.0
    t_captured: float = 0.0
    chunks: list = field(default_factory=list, repr=False)

    @property
    def dur(self) -> float:
        return round(self.audio_end - self.audio_start, 3)

    def resumen(self) -> dict:
        return {"ventana": self.idx, "bytes": self.bytes, "rms": round(self.rms, 1),
                "has_voice": self.has_voice, "audio_start": self.audio_start,
                "audio_end": self.audio_end, "dur": self.dur, "n_chunks": self.n_chunks,
                "burst_chunks": self.burst_chunks, "motivo": self.motivo,
                "umbral": round(self.umbral, 1), "t_captured": self.t_captured}


@dataclass
class Accion:
    kind: str                      # "start" | "audio" | "end"
    ventana: Ventana
    chunk: Optional[Chunk] = None  # solo en "audio"
    burst: bool = False            # True = reenvio de solape/gap: mandar SIN dormir


class Cortador:
    def __init__(self, ventana_s=VENTANA_S, tolerancia_s=TOLERANCIA_S, solape_s=SOLAPE_S,
                 gap_s=GAP_S, percentil=PERCENTIL, calibracion_s=CALIBRACION_S):
        self.min_s = ventana_s - tolerancia_s
        self.max_s = ventana_s + tolerancia_s
        self.solape_n = int(round(solape_s / CHUNK_S))
        self.gap_n = int(round(gap_s / CHUNK_S))
        self.percentil = percentil
        self.calib_n = int(round(calibracion_s / CHUNK_S))
        self._rms_calib: list[float] = []
        self._umbral_fijo: Optional[float] = None
        self.estado = "idle"           # idle | open | gap
        self.actual: Optional[Ventana] = None
        self._gap_buf: list[Chunk] = []
        self._solape: list[Chunk] = []
        self.n_ventanas = 0

    # ---- umbral de valle -------------------------------------------------
    def umbral(self) -> float:
        if self._umbral_fijo is not None:
            return self._umbral_fijo
        if not self._rms_calib:
            return 0.0
        return float(np.percentile(self._rms_calib, self.percentil))

    @property
    def umbral_congelado(self) -> bool:
        return self._umbral_fijo is not None

    def _calibrar(self, c: Chunk) -> None:
        if self._umbral_fijo is None:
            self._rms_calib.append(c.rms)
            if len(self._rms_calib) >= self.calib_n:
                self._umbral_fijo = float(np.percentile(self._rms_calib, self.percentil))

    # ---- maquina de estados ------------------------------------------------
    def _nueva(self, audio_start: float) -> Ventana:
        v = Ventana(idx=self.n_ventanas, audio_start=audio_start)
        self.n_ventanas += 1
        self.actual = v
        self.estado = "open"
        return v

    def _agregar(self, c: Chunk, burst: bool) -> Accion:
        v = self.actual
        v.chunks.append(c)
        v.bytes += len(c.data)
        v.n_chunks += 1
        if burst:
            v.burst_chunks += 1
        v.audio_end = c.audio_end
        v.t_captured = c.t_captured
        return Accion("audio", v, c, burst)

    def _abrir_con_rafaga(self, rafaga: list[Chunk]) -> list[Accion]:
        v = self._nueva(rafaga[0].audio_start)
        acc = [Accion("start", v)]
        for c in rafaga:
            acc.append(self._agregar(c, burst=True))
        return acc

    def _cerrar(self, motivo: str) -> Accion:
        v = self.actual
        v.motivo = motivo
        v.umbral = self.umbral()
        v.rms = rms_pcm16(b"".join(ch.data for ch in v.chunks))
        piso = max(VOZ_FACTOR * v.umbral, PISO_RMS)
        v.has_voice = sum(1 for ch in v.chunks if ch.rms > piso) >= VOZ_MIN_CHUNKS
        self._solape = v.chunks[-self.solape_n:] if self.solape_n else []
        self.estado = "gap"
        self._gap_buf = []
        self.actual = None
        return Accion("end", v)

    def push(self, c: Chunk) -> list[Accion]:
        self._calibrar(c)
        if self.estado == "idle":
            # primera ventana: no hay gap ni solape previos
            v = self._nueva(c.audio_start)
            return [Accion("start", v), self._agregar(c, burst=False)]
        if self.estado == "gap":
            self._gap_buf.append(c)
            if len(self._gap_buf) >= self.gap_n:
                rafaga = self._solape + self._gap_buf
                self._gap_buf, self._solape = [], []
                return self._abrir_con_rafaga(rafaga)
            return []
        # open
        acc = [self._agregar(c, burst=False)]
        dur = self.actual.audio_end - self.actual.audio_start
        if dur >= self.max_s - 1e-9:
            acc.append(self._cerrar("max"))
        elif dur >= self.min_s - 1e-9 and c.rms <= self.umbral():
            acc.append(self._cerrar("valle"))
        return acc

    def cerrar(self) -> list[Accion]:
        """Fin de la fuente: cierra la ventana abierta; si quedo audio en el gap, lo manda en una
        ventana final (no se pierde audio)."""
        if self.estado == "open" and self.actual is not None:
            return [self._cerrar("fin")]
        if self.estado == "gap" and self._gap_buf:
            rafaga = self._solape + self._gap_buf
            self._gap_buf, self._solape = [], []
            acc = self._abrir_con_rafaga(rafaga)
            acc.append(self._cerrar("fin"))
            return acc
        return []
