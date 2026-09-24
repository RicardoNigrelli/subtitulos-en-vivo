"""MODO REPLAY (rotulado): reproduce las lineas `emit` de un casete en el bus, con sus tiempos.

    python -m worker.replay fixtures/casetes/b1-en-60s.jsonl [--hub ws://localhost:8100/ingest]
                            [--sesion <override>] [--velocidad 1] [--salida x.jsonl]

- NO es ASR real (no satisface R17a ni R21): todo mensaje sale con `replay: true` y el
  `session_start` lleva `meta.title` con el prefijo "REPLAY".
- Unico modulo donde hay texto "enlatado": el que Gemini devolvio de verdad, leido del casete
  (dir=emit). No genera texto propio.
- Tiempos: respeta los gaps originales de `t_emit` (divididos por --velocidad). `t_emit` se
  reestampa al momento del envio y `t_captured` se corre en el mismo delta, asi la latencia
  (t_emit - t_captured) queda igual a la original.
- `seq`: se renumera como base + seq_original, con base = last_seq que el hub tiene para esa
  sesion (auth_ok). Asi un segundo replay de la misma sesion sigue creciendo y el hub no lo
  descarta por duplicado.
Exit: 0 = todo publicado (y entregado al hub si hay hub); 1 = quedo backlog sin entregar;
2 = casete invalido / sin mensajes.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from worker.casete import leer
from worker.emisor import Emisor

HUB_DEFAULT = os.environ.get("HUB_URL", "ws://localhost:8100/ingest")
PREFIJO = "REPLAY"


def emits_del_casete(path: str | os.PathLike) -> tuple[dict, list[dict]]:
    cab, eventos = leer(path)
    msgs = [e["payload"] for e in eventos if e.get("dir") == "emit" and isinstance(e.get("payload"), dict)]
    return cab, msgs


def rotular(msg: dict, *, sesion: Optional[str], base_seq: int, delta_t: float,
            nombre_casete: str) -> dict:
    m = copy.deepcopy(msg)
    m["replay"] = True
    if sesion:
        m["session_id"] = sesion
    if m.get("seq") is not None:
        m["seq"] = base_seq + int(m["seq"])
    if m.get("t_emit") is not None:
        m["t_emit"] = float(m["t_emit"]) + delta_t
    if m.get("t_captured") is not None:
        m["t_captured"] = float(m["t_captured"]) + delta_t
    meta = m.setdefault("meta", {})
    if m.get("type") == "session_start":
        titulo = meta.get("title") or ""
        if not str(titulo).startswith(PREFIJO):
            meta["title"] = f"{PREFIJO} · {titulo}".strip(" ·")
        meta["replay_of"] = nombre_casete
    return m


async def reproducir(casete: str, hub: Optional[str] = HUB_DEFAULT, sesion: Optional[str] = None,
                     velocidad: float = 1.0, salida: Optional[str] = None,
                     emisor: Optional[Emisor] = None, espera_conexion_s: float = 10.0,
                     log: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True)
                     ) -> tuple[int, list[dict]]:
    cab, msgs = emits_del_casete(casete)
    if not msgs:
        log(f"[replay] {casete}: no tiene lineas emit")
        return 2, []
    sid_orig = msgs[0].get("session_id") or cab.get("session_id")
    sid = sesion or sid_orig
    log(f"[replay] MODO REPLAY (no es ASR real): {casete} -> sesion {sid!r}, "
        f"{len(msgs)} mensajes, velocidad x{velocidad}")
    propio = emisor is None and hub is not None
    if propio:
        emisor = Emisor(hub, log=log)
    base = 0
    if emisor is not None:
        emisor.iniciar()
        t0 = time.monotonic()
        while not emisor.conectado and time.monotonic() - t0 < espera_conexion_s:
            await asyncio.sleep(0.05)
        base = int(emisor.last_seq_hub.get(sid, 0)) if emisor.conectado else 0
        if base:
            log(f"[replay] el hub ya tiene {sid!r} hasta seq={base}: se numera desde {base + 1}")
    f_sal = open(salida, "w", encoding="utf-8") if salida else None
    enviados: list[dict] = []
    t_emits = [m.get("t_emit") for m in msgs if m.get("t_emit") is not None]
    t_primero = min(t_emits) if t_emits else 0.0
    t_arranque = time.time()
    nombre = Path(casete).name
    try:
        for m in msgs:
            te = m.get("t_emit")
            if te is not None and velocidad > 0:
                objetivo = t_arranque + (float(te) - t_primero) / velocidad
                espera = objetivo - time.time()
                if espera > 0:
                    await asyncio.sleep(espera)
            ahora = time.time()
            delta = (ahora - float(te)) if te is not None else 0.0
            r = rotular(m, sesion=sid, base_seq=base, delta_t=delta, nombre_casete=nombre)
            r["t_emit"] = ahora
            if emisor is not None:
                emisor.publicar(r)
            if f_sal:
                f_sal.write(json.dumps(r, ensure_ascii=False) + "\n")
                f_sal.flush()
            enviados.append(r)
        ok = True
        if emisor is not None:
            ok = await emisor.vaciar(5.0)
            log(f"[replay] hub: enviados={emisor.n_enviados} pendientes={emisor.pendientes()} "
                f"vaciado={ok}")
    finally:
        if f_sal:
            f_sal.close()
        if propio and emisor is not None:
            await emisor.detener()
    return (0 if ok else 1), enviados


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.replay")
    ap.add_argument("casete")
    ap.add_argument("--hub", default=HUB_DEFAULT, help="URL /ingest; 'none' = sin hub")
    ap.add_argument("--sesion", default=None, help="override del session_id")
    ap.add_argument("--velocidad", type=float, default=1.0)
    ap.add_argument("--salida", default=None, help="ademas, escribe los mensajes a este JSONL")
    a = ap.parse_args(argv)
    hub = None if (a.hub or "").lower() == "none" else a.hub
    codigo, enviados = asyncio.run(reproducir(a.casete, hub, a.sesion, a.velocidad, a.salida))
    seqs = [m["seq"] for m in enviados if m.get("seq") is not None]
    print(json.dumps({"casete": a.casete, "hub": hub, "mensajes": len(enviados),
                      "seq_primero": seqs[0] if seqs else None,
                      "seq_ultimo": seqs[-1] if seqs else None,
                      "seq_creciente": all(b > x for x, b in zip(seqs, seqs[1:])),
                      "todos_replay": all(m.get("replay") is True for m in enviados),
                      "exit": codigo}, ensure_ascii=False))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
