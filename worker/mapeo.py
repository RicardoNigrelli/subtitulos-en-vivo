"""Asignacion de textos finales a ventanas de audio (audio_start, audio_end, t_captured).

Medido 24/09 13:12-13:22 (casetes b1-en-60s y nerdearla-en-full): por turno el server manda
  serverContent.inputTranscription.text -> serverContent.generationComplete ->
  voiceActivity {type: ACTIVITY_END, audioOffset: "<s>s"}
en el mismo milisegundo. audioOffset = audio ACUMULADO que recibio el server (incluye los
reenvios de solape). No llega turnComplete. Cuando el server va atrasado FUSIONA varias ventanas en
un solo turno: un ACTIVITY_END cubre varias ventanas.

Regla: el texto se retiene hasta el ACTIVITY_END que le sigue (llega junto, ~0 ms) y se asigna al
rango de TODAS las ventanas cubiertas por ese audioOffset. Si en ESPERA_MAX_S no llega el
ACTIVITY_END, se emite con la ventana pendiente mas vieja (fallback).
Lo usan el SessionWorker (en vivo) y worker.rederivar (offline, sobre un casete).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

TOL_S = 0.05
ESPERA_MAX_S = 0.5


@dataclass
class InfoVentana:
    idx: int
    audio_start: float
    audio_end: float
    t_captured: float
    fin_acum: float          # audio acumulado enviado al server al cerrar esta ventana


@dataclass
class Asignacion:
    t: float                 # cuando llego el texto
    text: str
    audio_start: Optional[float]
    audio_end: Optional[float]
    t_captured: Optional[float]
    ventanas: list


class Mapeador:
    def __init__(self, espera_max_s: float = ESPERA_MAX_S):
        self.pend: deque[InfoVentana] = deque()
        self.textos: list[tuple[float, str]] = []
        self.ultima: Optional[InfoVentana] = None
        self.espera_max_s = espera_max_s

    def ventana_cerrada(self, info: InfoVentana) -> None:
        self.pend.append(info)
        self.ultima = info

    def texto(self, t: float, text: str) -> None:
        self.textos.append((t, text))

    def _resolver(self, cubiertas: list[InfoVentana]) -> list[Asignacion]:
        if not self.textos:
            return []
        ref = cubiertas or ([self.pend[0]] if self.pend else ([self.ultima] if self.ultima else []))
        out = []
        for t, tx in self.textos:
            if ref:
                out.append(Asignacion(t, tx, ref[0].audio_start, ref[-1].audio_end,
                                      ref[-1].t_captured, [v.idx for v in ref]))
            else:
                out.append(Asignacion(t, tx, None, None, None, []))
        self.textos = []
        return out

    def activity_end(self, offset_s: Optional[float]) -> list[Asignacion]:
        cubiertas: list[InfoVentana] = []
        if offset_s is None:
            if self.pend:
                cubiertas.append(self.pend.popleft())
        else:
            while self.pend and self.pend[0].fin_acum <= offset_s + TOL_S:
                cubiertas.append(self.pend.popleft())
        return self._resolver(cubiertas)

    def vencidos(self, ahora: float) -> list[Asignacion]:
        if self.textos and ahora - self.textos[0][0] >= self.espera_max_s:
            return self._resolver([])
        return []

    def vaciar(self) -> list[Asignacion]:
        return self._resolver([])


def segundos(offset) -> Optional[float]:
    """'2.200s' -> 2.2 (Duration de protobuf en JSON)."""
    if offset is None:
        return None
    try:
        return float(str(offset).rstrip("s"))
    except ValueError:
        return None
