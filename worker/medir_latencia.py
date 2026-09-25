"""Latencia por casete (LATENCIA 25/09), todo recalculado desde el archivo:

    python -m worker.medir_latencia fixtures/casetes/evidencia-25-09/lat-*.jsonl

Por cada `emit` de tipo text: ventana = audio_end - audio_start; servicio = t_emit - t_captured (fin de
la ventana capturada -> texto emitido); primera palabra -> texto = ventana + servicio (la primera palabra
de la ventana espera la ventana entera). Llamadas de texto: intentos (cualquier estado) en
meta.source.intentos de las traducciones, por minuto de corrida (primer a ultimo evento).
Percentil nearest-rank. Texto -> traduccion: `python -m worker.medir_traduccion`.
"""
import json
import sys


def _p(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 2)


def medir(ruta):
    ven, serv, pp, ts, llamadas = [], [], [], [], 0
    for ln in open(ruta, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if "t" in r:
            ts.append(r["t"])
        if r.get("dir") != "emit":
            continue
        p = r.get("payload") or {}
        if r.get("kind") == "text" and p.get("t_captured") and p.get("audio_end") is not None:
            v = p["audio_end"] - p["audio_start"]
            s = p["t_emit"] - p["t_captured"]
            ven.append(v); serv.append(s); pp.append(v + s)
        if r.get("kind") == "translation":
            src = ((p.get("meta") or {}).get("source")) or {}
            if isinstance(src, dict):
                llamadas += len(src.get("intentos") or [])
    dur_min = (max(ts) - min(ts)) / 60 if ts else 0
    return {"casete": ruta, "textos": len(pp), "ventana_p50": _p(ven, .5),
            "servicio_p50": _p(serv, .5), "servicio_p95": _p(serv, .95),
            "primera_palabra_p50": _p(pp, .5), "primera_palabra_p95": _p(pp, .95),
            "llamadas_texto": llamadas, "min": round(dur_min, 2),
            "llamadas_por_min": round(llamadas / dur_min, 1) if dur_min else None}


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(json.dumps(medir(a), ensure_ascii=False))
