"""Grabador de casetes JSONL (fixtures/casetes/). Unica evidencia de "Gemini dijo X".

Linea 1 (cabecera):
  {"casete":1,"session_id":..,"lang":..,"model":..,"config":{...},"source":{...},"started_at":epoch}
Luego una linea por evento: {"t":epoch,"dir":"server"|"client"|"emit","kind":str,"payload":{...}}
  server: cada mensaje crudo del server tal cual (kind = claves de primer nivel, p.ej. serverContent,
          goAway, voiceActivity; "close" para el cierre del websocket con su codigo).
  client: connect, config, activity_start, ventana (bytes, rms, has_voice, audio_start, audio_end),
          activity_end, goaway_seen, close.
  emit:   cada mensaje del contrato emitido (payload = el mensaje).
Se hace flush por linea: si el proceso muere, el casete queda valido hasta la ultima linea.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

DIRS = ("server", "client", "emit")


def kind_server(msg: dict) -> str:
    claves = [k for k in msg.keys()]
    return "+".join(claves) if claves else "vacio"


class Grabador:
    def __init__(self, path: str | os.PathLike, cabecera: dict):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        cab = {"casete": 1}
        cab.update(cabecera)
        cab.setdefault("started_at", time.time())
        self.cabecera = cab
        self._escribir(cab)
        self.n = 0

    def _escribir(self, obj: dict) -> None:
        linea = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            self._f.write(linea + "\n")
            self._f.flush()

    def evento(self, dir: str, kind: str, payload: Any, t: Optional[float] = None, **extra) -> dict:
        if dir not in DIRS:
            raise ValueError(dir)
        ev = {"t": time.time() if t is None else t, "dir": dir, "kind": kind, "payload": payload}
        ev.update(extra)
        self._escribir(ev)
        self.n += 1
        return ev

    def server(self, kind: str, payload: Any, t: Optional[float] = None, **extra) -> dict:
        return self.evento("server", kind, payload, t, **extra)

    def client(self, kind: str, payload: Any, t: Optional[float] = None, **extra) -> dict:
        return self.evento("client", kind, payload, t, **extra)

    def emit(self, msg: dict, t: Optional[float] = None, **extra) -> dict:
        return self.evento("emit", msg.get("type", "?"), msg, t, **extra)

    def cerrar(self) -> None:
        with self._lock:
            if not self._f.closed:
                self._f.close()


def leer(path: str | os.PathLike) -> tuple[dict, list[dict]]:
    cab: Optional[dict] = None
    eventos: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for i, linea in enumerate(f):
            linea = linea.strip()
            if not linea:
                continue
            obj = json.loads(linea)
            if i == 0:
                if obj.get("casete") != 1:
                    raise ValueError(f"{path}: la linea 1 no es cabecera de casete v1")
                cab = obj
            else:
                eventos.append(obj)
    if cab is None:
        raise ValueError(f"{path}: casete vacio")
    return cab, eventos


def textos_server(eventos: list[dict]) -> list[tuple[float, str]]:
    """(t, texto) de cada mensaje server con inputTranscription.text no vacio."""
    out = []
    for ev in eventos:
        if ev.get("dir") != "server":
            continue
        p = ev.get("payload") or {}
        sc = p.get("serverContent") or {}
        tx = (sc.get("inputTranscription") or {}).get("text")
        if tx:
            out.append((ev["t"], tx))
    return out
