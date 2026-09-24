"""¿Por qué `qa/cobertura.py` y `worker.cobertura` dan distinto en algunos casetes? (adversario B2 §5.1)

    .venv/Scripts/python qa/cobertura_mapeos.py fixtures/casetes/b1-*.jsonl fixtures/casetes/b2-*.jsonl [--salida qa/out/cobertura-mapeos-b3.json]

Por casete, sin API, todo desde el archivo:
  1. ventanas con voz cubiertas según tres mapeos texto->ventana:
     exacto (rango de audio del texto == el de la ventana ±0,05 s; lo que usa qa/cobertura.py sin campo),
     contención (la ventana cae dentro del rango del texto ±0,05 s) y campo `ventanas` (si el casete lo trae);
  2. palabras por texto con su rango de audio y su llegada (t - started_at);
  3. `worker.cobertura --detalle` (turno del server por audioOffset) y cuántas de sus ventanas "cubiertas"
     recibieron el texto con latencia <= 10 s y <= 30 s (lo que la audiencia ve a tiempo).
Exit 0 = calculado; 1 = algún casete ilegible.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.comun import OUT, PY, RAIZ, ahora_ar, leer_casete  # noqa: E402


def analizar(path: str) -> dict:
    cab, R = leer_casete(path)
    st = cab.get("started_at") or R[0]["t"]
    ven = [r["payload"] for r in R if r.get("dir") == "client" and r.get("kind") == "ventana"]
    txt = [r for r in R if r.get("dir") == "emit" and r.get("kind") == "text"]
    voz = [v for v in ven if v.get("has_voice")]
    ids_voz = {v["ventana"] for v in voz}
    rangos = [(t["payload"]["audio_start"], t["payload"]["audio_end"]) for t in txt]
    exacto = {v["ventana"] for v in voz for (a, b) in rangos
              if abs(v["audio_start"] - a) <= .05 and abs(v["audio_end"] - b) <= .05}
    cont = {v["ventana"] for v in voz for (a, b) in rangos if v["audio_start"] >= a - .05 and v["audio_end"] <= b + .05}
    con_campo = sum(1 for t in txt if isinstance(t.get("ventanas"), list))
    campo = set()
    for t in txt:
        if isinstance(t.get("ventanas"), list):
            campo.update(int(i) for i in t["ventanas"])
    campo &= ids_voz
    textos = [{"seq": t["payload"]["seq"], "audio": [t["payload"]["audio_start"], t["payload"]["audio_end"]],
               "t_rel": round(t["t"] - st, 1), "palabras": len((t["payload"].get("text") or "").split())} for t in txt]
    wc = subprocess.run([PY, "-m", "worker.cobertura", path, "--detalle"], cwd=str(RAIZ), capture_output=True,
                        text=True, env={"PYTHONIOENCODING": "utf-8", **__import__("os").environ})
    w = json.loads(wc.stdout.strip().splitlines()[-1]) if wc.returncode == 0 and wc.stdout.strip() else None
    a_tiempo = None
    if w:
        dv = [x for x in w["detalle"] if x["voz"]]
        cub = [x for x in dv if x["con_texto"]]
        a_tiempo = {"con_voz": len(dv), "worker_cubiertas": len(cub),
                    **{f"lat_le_{k}s": sum(1 for x in cub if x["lat_s"] is not None and x["lat_s"] <= k) for k in (10, 30)}}
    return {"casete": Path(path).as_posix(), "voz": len(voz), "textos": len(txt), "textos_con_campo_ventanas": con_campo,
            "textos_sin_ventana_de_igual_rango": sum(1 for (a, b) in rangos if not any(
                abs(v["audio_start"] - a) <= .05 and abs(v["audio_end"] - b) <= .05 for v in ven)),
            "cubiertas_exacto": len(exacto), "cubiertas_contencion": len(cont),
            "cubiertas_campo": len(campo) if con_campo else None, "worker_cobertura": a_tiempo,
            "worker_cobertura_exit": wc.returncode, "textos_detalle": textos}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("casetes", nargs="+")
    ap.add_argument("--salida", default=str(OUT / "cobertura-mapeos-b3.json"))
    a = ap.parse_args(argv)
    rc, res = 0, []
    for c in a.casetes:
        try:
            r = analizar(c)
        except Exception as e:
            print(f"{c}: ILEGIBLE {type(e).__name__}: {e}")
            rc = 1
            continue
        res.append(r)
        w = r["worker_cobertura"] or {}
        f = lambda n: f"{n}/{r['voz']}={n / r['voz']:.3f}" if r["voz"] and n is not None else str(n)
        print(f"{Path(c).name:44s} voz={r['voz']:3d} textos={r['textos']:3d} (campo {r['textos_con_campo_ventanas']:3d}) | "
              f"exacto {f(r['cubiertas_exacto'])} contención {f(r['cubiertas_contencion'])} campo {f(r['cubiertas_campo'])} | "
              f"worker.cobertura {f(w.get('worker_cubiertas'))} a<=10s {f(w.get('lat_le_10s'))} a<=30s {f(w.get('lat_le_30s'))}")
    Path(a.salida).write_text(json.dumps({"generado": ahora_ar(), "comando": "python qa/cobertura_mapeos.py " +
                                          " ".join(sys.argv[1:] if argv is None else argv), "casetes": res, "exit": rc},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {a.salida} exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
