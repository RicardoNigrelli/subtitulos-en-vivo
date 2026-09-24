"""Metricas recalculables desde un casete (evidencia: todo sale del archivo, nada de memoria).

    python -m worker.analisis fixtures/casetes/b1-en-60s.jsonl [--json]

- duracion: ultimo t - started_at.
- textos server: mensajes con serverContent.inputTranscription.text (finales) e
  interimInputTranscription.text (parciales).
- cadencia: gaps entre mensajes server con texto final consecutivos (p50/p95, percentil por
  interpolacion lineal de numpy).
- lag por turno: t(voiceActivity ACTIVITY_END del server) - t(activity_end del cliente) de la
  ventana cuyo audio acumulado enviado coincide con el audioOffset del server (+-0,05 s).
  Turnos fusionados por el server (un ACTIVITY_END que cubre varias ventanas) se cuentan aparte.
- goaway: kind exacto y payload; close: codigo y motivo.
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


def _pct(v, q):
    return round(float(np.percentile(v, q)), 3) if v else None


def analizar(path: str) -> dict:
    cab, evs = leer(path)
    t0 = cab.get("started_at") or (evs[0]["t"] if evs else 0)
    finales, interinos = [], []
    fin_acum: list[tuple[int, float, float]] = []   # (ventana, acumulado enviado, t_activity_end)
    acum = 0.0
    ends = []
    goaways, closes, kinds = [], [], {}
    n_ventanas = n_voz = 0
    for e in evs:
        d, k, p = e.get("dir"), e.get("kind"), e.get("payload") or {}
        if d == "server":
            kinds[k] = kinds.get(k, 0) + 1
            sc = p.get("serverContent") or {}
            tx = (sc.get("inputTranscription") or {}).get("text")
            if tx:
                finales.append((e["t"], tx))
            it = (sc.get("interimInputTranscription") or {}).get("text")
            if it:
                interinos.append((e["t"], it))
            va = p.get("voiceActivity") or {}
            if va.get("type") == "ACTIVITY_END":
                ends.append((e["t"], _seg(va.get("audioOffset"))))
            if "goAway" in p or "goaway" in k.lower():
                goaways.append({"t_rel": round(e["t"] - t0, 2), "kind": k, "payload": p})
            if k == "close":
                closes.append({"t_rel": round(e["t"] - t0, 2), **p})
        elif d == "client" and k == "ventana":
            n_ventanas += 1
            n_voz += 1 if p.get("has_voice") else 0
            acum += p.get("n_chunks", 0) * 0.1
            fin_acum.append((p.get("ventana"), round(acum, 3), e["t"]))
    lags, fusionados = [], 0
    usados = 0
    for t_srv, off in ends:
        if off is None:
            continue
        cubiertas = [w for w in fin_acum[usados:] if w[1] <= off + 0.05]
        if not cubiertas:
            continue
        usados += len(cubiertas)
        if len(cubiertas) > 1:
            fusionados += 1
        ultima = cubiertas[-1]
        if abs(ultima[1] - off) <= 0.05:
            lags.append(round(t_srv - ultima[2], 3))
    gaps = [round(b[0] - a[0], 3) for a, b in zip(finales, finales[1:])]
    ult = evs[-1]["t"] if evs else t0
    return {
        "casete": path,
        "session_id": cab.get("session_id"),
        "model": cab.get("model"),
        "duracion_s": round(ult - t0, 1),
        "ventanas": n_ventanas,
        "ventanas_con_voz": n_voz,
        "audio_enviado_s": round(acum, 1),
        "server_kinds": kinds,
        "textos_finales": len(finales),
        "textos_interinos": len(interinos),
        "cadencia_finales_s": {"n": len(gaps), "p50": _pct(gaps, 50), "p95": _pct(gaps, 95),
                               "max": max(gaps) if gaps else None},
        "lag_turno_s": {"n": len(lags), "p50": _pct(lags, 50), "p95": _pct(lags, 95),
                        "max": max(lags) if lags else None},
        "turnos_fusionados": fusionados,
        "ventanas_sin_turno": n_ventanas - usados,
        "goaway": goaways,
        "close": closes,
        "primeros_textos": [{"t_rel": round(t - t0, 2), "text": x} for t, x in finales[:3]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.analisis")
    ap.add_argument("casetes", nargs="+")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for c in a.casetes:
        print(json.dumps(analizar(c), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
