"""Cobertura desde un casete: ventanas con voz (capa client, `has_voice`) vs turnos con texto (emit).

    .venv/Scripts/python qa/cobertura.py fixtures/casetes/*.jsonl [--salida qa/out/cobertura-b2.json] [--umbral-tramo 10]

Mapeo ventana -> texto: campo `ventanas` de la línea emit (explícito) si está; si no, por rango de audio
(audio_start/audio_end del texto == el de la ventana, tolerancia 0,05 s). Si varios textos tienen el mismo
rango el mapeo es DEGENERADO y se marca (caso b1-en-60s original: usar el rederivado).
Cruce independiente: finales crudos del server (`inputTranscription`) y `python -m worker.analisis`.
No es WER. Exit 0 = calculado; 1 = algún casete sin ventanas o ilegible; 3 = algún tramo sin texto > umbral
(sólo con --estricto).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.comun import OUT, PY, RAIZ, ahora_ar, leer_casete  # noqa: E402


def _union(intervalos):
    tot, fin = 0.0, None
    for a, b in sorted(intervalos):
        if fin is None or a > fin:
            tot += b - a
            fin = b
        elif b > fin:
            tot += b - fin
            fin = b
    return tot


def cobertura_casete(path, umbral_tramo: float = 10.0) -> dict:
    cab, R = leer_casete(path)
    st = cab.get("started_at") or R[0]["t"]
    ven = [r for r in R if r.get("dir") == "client" and r.get("kind") == "ventana"]
    txt = [r for r in R if r.get("dir") == "emit" and r.get("kind") == "text"]
    finales = [r for r in R if r.get("dir") == "server"
               and ((r.get("payload") or {}).get("serverContent") or {}).get("inputTranscription")]
    W = {v["payload"]["ventana"]: v for v in ven}
    cubiertas: set[int] = set()
    rangos = [(round(t["payload"]["audio_start"], 2), round(t["payload"]["audio_end"], 2)) for t in txt]
    con_campo = sum(1 for t in txt if isinstance(t.get("ventanas"), list))
    if txt and con_campo == len(txt):
        mapeo = "campo_ventanas"
        for t in txt:
            cubiertas.update(int(i) for i in t["ventanas"])
    else:
        mapeo = "rango_audio"
        for (a, b) in rangos:
            for i, v in W.items():
                p = v["payload"]
                if abs(p["audio_start"] - a) <= 0.05 and abs(p["audio_end"] - b) <= 0.05:
                    cubiertas.add(i)
    degenerado = len(set(rangos)) < len(rangos)
    voz = [v for v in ven if v["payload"].get("has_voice")]
    sin = [v for v in voz if v["payload"]["ventana"] not in cubiertas]
    # tramos = corridas de ventanas con voz consecutivas (por índice de ventana) sin texto
    tramos, cur = [], []
    orden = sorted(voz, key=lambda v: v["payload"]["ventana"])
    for v in orden:
        if v["payload"]["ventana"] in cubiertas:
            if cur:
                tramos.append(cur)
            cur = []
        else:
            cur.append(v)
    if cur:
        tramos.append(cur)

    def fila(v):
        p = v["payload"]
        return {"ventana": p["ventana"], "audio_start": p["audio_start"], "audio_end": p["audio_end"],
                "t_rel_envio": round(v["t"] - st, 2)}

    T = []
    for tr in tramos:
        a, b = tr[0]["payload"]["audio_start"], tr[-1]["payload"]["audio_end"]
        T.append({"ventanas": [tr[0]["payload"]["ventana"], tr[-1]["payload"]["ventana"]], "n": len(tr),
                  "audio_start": a, "audio_end": b, "dur_audio_s": round(b - a, 2),
                  "t_rel_desde": round(tr[0]["t"] - st, 2), "t_rel_hasta": round(tr[-1]["t"] - st, 2)})
    voz_s = _union([(v["payload"]["audio_start"], v["payload"]["audio_end"]) for v in voz])
    cub_s = _union([(v["payload"]["audio_start"], v["payload"]["audio_end"]) for v in voz
                    if v["payload"]["ventana"] in cubiertas])
    tramo_max = max((t["dur_audio_s"] for t in T), default=0.0)
    res = {
        "casete": str(Path(path).as_posix()), "session_id": cab.get("session_id"), "lang": cab.get("lang"),
        "mapeo": mapeo, "mapeo_degenerado": degenerado,
        "ventanas": len(ven), "ventanas_con_voz": len(voz), "ventanas_voz_con_texto": len(voz) - len(sin),
        "fraccion_ventanas": round((len(voz) - len(sin)) / len(voz), 3) if voz else None,
        "audio_voz_s": round(voz_s, 2), "audio_voz_cubierto_s": round(cub_s, 2),
        "fraccion_audio": round(cub_s / voz_s, 3) if voz_s else None,
        "textos_emit": len(txt), "finales_server": len(finales),
        "tramo_sin_texto_max_s": tramo_max, "umbral_tramo_s": umbral_tramo,
        "tramos_sin_texto_mayores_al_umbral": [t for t in T if t["dur_audio_s"] > umbral_tramo],
        "tramos_sin_texto": T, "ventanas_sin_texto": [fila(v) for v in sin],
    }
    return res


def analisis_worker(path) -> int | None:
    """Cruce con la herramienta de audio-pipeline (no la reimplementa: la ejecuta)."""
    try:
        p = subprocess.run([PY, "-m", "worker.analisis", str(path)], cwd=str(RAIZ), capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        for ln in p.stdout.splitlines():
            if "ventanas_sin_turno" in ln:
                return int(ln.split(":")[-1].strip().strip(",").strip())
        d = json.loads(p.stdout)
        return d.get("ventanas_sin_turno")
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("casetes", nargs="+")
    ap.add_argument("--salida", default=str(OUT / "cobertura-b2.json"))
    ap.add_argument("--umbral-tramo", type=float, default=10.0)
    ap.add_argument("--estricto", action="store_true", help="exit 3 si algún tramo sin texto supera el umbral")
    a = ap.parse_args(argv)
    out, rc = [], 0
    for c in a.casetes:
        try:
            r = cobertura_casete(c, a.umbral_tramo)
        except Exception as e:
            print(f"ERROR {c}: {type(e).__name__}: {e}")
            rc = 1
            continue
        r["worker_analisis_ventanas_sin_turno"] = analisis_worker(c)
        out.append(r)
        print(f"{Path(c).name:45s} mapeo={r['mapeo']}{' DEGENERADO' if r['mapeo_degenerado'] else ''} "
              f"voz={r['ventanas_con_voz']} con_texto={r['ventanas_voz_con_texto']} "
              f"frac_ventanas={r['fraccion_ventanas']} frac_audio={r['fraccion_audio']} "
              f"tramo_max={r['tramo_sin_texto_max_s']}s finales_server={r['finales_server']} "
              f"worker.analisis.sin_turno={r['worker_analisis_ventanas_sin_turno']}")
        if not r["ventanas"]:
            rc = 1
        if a.estricto and r["tramos_sin_texto_mayores_al_umbral"]:
            rc = rc or 3
    Path(a.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(a.salida).write_text(json.dumps({"generado": ahora_ar(), "comando": "qa/cobertura.py " + " ".join(sys.argv[1:] if argv is None else argv),
                                          "nota": "cobertura = ventanas con voz con texto / ventanas con voz. No es WER.",
                                          "casetes": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {a.salida} exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
