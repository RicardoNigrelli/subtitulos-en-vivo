"""Cobertura y latencia POR VENTANA desde un casete (sin API; todo sale del archivo).

    python -m worker.cobertura fixtures/casetes/b1-en-60s.jsonl [otro.jsonl ...] [--detalle]

Definiciones (B2):
- Ventana = linea client `ventana` (n_chunks, has_voice, t = activity_end del cliente).
- Turno del server = cada voiceActivity ACTIVITY_END; su texto = los inputTranscription recibidos
  desde el ACTIVITY_END anterior (llegan en el mismo ms, worker/mapeo.py). Un turno cubre las
  ventanas cuyo audio acumulado enviado (fin_acum) <= audioOffset + 0,05 s.
- Cobertura = ventanas CON VOZ cubiertas por un turno CON texto / ventanas con voz.
- Latencia por ventana = t(llegada del texto de su turno) - t(activity_end de la ventana).
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from worker.casete import leer


def _seg(x):
    try:
        return float(str(x).rstrip("s"))
    except (TypeError, ValueError):
        return None


def cobertura(path: str) -> dict:
    cab, evs = leer(path)
    t0 = cab.get("started_at") or (evs[0]["t"] if evs else 0.0)
    ventanas = []
    acum = 0.0
    turnos = []
    textos_pend: list[tuple[float, str]] = []
    for e in evs:
        d, k, p = e.get("dir"), e.get("kind"), e.get("payload") or {}
        if d == "client" and k == "ventana":
            acum += p.get("n_chunks", 0) * 0.1
            ventanas.append({"ventana": p.get("ventana"), "voz": bool(p.get("has_voice")),
                             "audio": [p.get("audio_start"), p.get("audio_end")],
                             "fin_acum": round(acum, 3), "t_end": e["t"], "turno": None,
                             "con_texto": False, "lat_s": None})
        elif d == "server":
            sc = p.get("serverContent") or {}
            tx = (sc.get("inputTranscription") or {}).get("text")
            if tx:
                textos_pend.append((e["t"], tx))
            va = p.get("voiceActivity") or {}
            if va.get("type") == "ACTIVITY_END":
                turnos.append({"t": e["t"], "offset": _seg(va.get("audioOffset")),
                               "textos": textos_pend})
                textos_pend = []
    usadas = 0
    for i, tr in enumerate(turnos):
        if tr["offset"] is None:
            continue
        while usadas < len(ventanas) and ventanas[usadas]["fin_acum"] <= tr["offset"] + 0.05:
            v = ventanas[usadas]
            v["turno"] = i
            if tr["textos"]:
                v["con_texto"] = True
                v["lat_s"] = round(tr["textos"][-1][0] - v["t_end"], 3)
            usadas += 1
    voz = [v for v in ventanas if v["voz"]]
    cub = [v for v in voz if v["con_texto"]]
    sin_turno = [v for v in voz if v["turno"] is None]
    turno_sin_texto = [v for v in voz if v["turno"] is not None and not v["con_texto"]]
    lats = [v["lat_s"] for v in cub if v["lat_s"] is not None]

    def pct(q):
        return round(float(np.percentile(lats, q)), 3) if lats else None
    ult_texto = max((tr["textos"][-1][0] for tr in turnos if tr["textos"]), default=None)
    return {
        "casete": path, "cortador": cab.get("cortador"),
        "ventanas": len(ventanas), "con_voz": len(voz), "cubiertas_con_texto": len(cub),
        "cobertura": round(len(cub) / len(voz), 3) if voz else None,
        "con_voz_sin_turno": len(sin_turno), "con_voz_turno_sin_texto": len(turno_sin_texto),
        "turnos_server": len(turnos), "turnos_con_texto": sum(1 for t in turnos if t["textos"]),
        "lat_s": {"n": len(lats), "p50": pct(50), "p95": pct(95), "max": max(lats) if lats else None},
        "audio_fuente_cubierto_hasta_s": max((v["audio"][1] for v in cub), default=None),
        "t_rel_ultimo_texto": round(ult_texto - t0, 2) if ult_texto else None,
        "detalle": [{k: v[k] for k in ("ventana", "voz", "audio", "turno", "con_texto", "lat_s")}
                    for v in ventanas],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.cobertura")
    ap.add_argument("casetes", nargs="+")
    ap.add_argument("--detalle", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for c in a.casetes:
        r = cobertura(c)
        if not a.detalle:
            r.pop("detalle")
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
