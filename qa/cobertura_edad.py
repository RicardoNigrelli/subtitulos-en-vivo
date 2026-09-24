"""¿La pérdida de cobertura crece con la EDAD de la sesión? Por cubeta de 30 s (reloj desde started_at).

    .venv/Scripts/python qa/cobertura_edad.py fixtures/casetes/b1-nerdearla-en-intento2-quota.jsonl fixtures/casetes/b1-nerdearla-es-intento2-cancelled.jsonl fixtures/casetes/b1-en-60s.jsonl

Por cubeta (la ventana cae en la cubeta de su activity_end = línea `ventana`, t - started_at):
  1. cobertura = ventanas con voz con texto / ventanas con voz (mapeo: campo `ventanas` del emit o rango de audio).
     Si el mapeo del casete es degenerado (b1-en-60s original) se usa el hermano `-rederivado` SÓLO si sus capas
     server+client son idénticas (se verifica y se informa).
  2. latencia por turno = t_emit del texto - t de la línea `ventana` (activity_end) de su ÚLTIMA ventana; p50 y max
     (nearest-rank), asignada a la cubeta de ese activity_end.
  3. ventanas con voz enviadas con cola pendiente: al activity_end de la ventana k, alguna ventana con voz anterior
     (índice < k) todavía no "respondida". Respondida = llegó su texto o el de una ventana posterior (el server
     contesta en orden). Además: profundidad máxima de esa cola.
  4. (independiente del mapeo) atraso del server al cierre de la cubeta = audio enviado acumulado (Σ dur de `ventana`)
     - último audioOffset de voiceActivity recibido. Si crece cubeta a cubeta, el server se atrasa con la edad.
Salida: qa/out/cobertura-edad-b2.json. Exit 0 = calculado; 1 = casete ilegible o sin ventanas.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.comun import OUT, ahora_ar, leer_casete, pct  # noqa: E402

CUBETA = 30.0


def _off(s) -> float | None:
    try:
        return float(str(s).rstrip("s"))
    except Exception:
        return None


def _capas(R):
    return [json.dumps(r, sort_keys=True) for r in R if r.get("dir") in ("server", "client")]


def analizar(path: str) -> dict:
    cab, R = leer_casete(path)
    usado, nota = path, None
    txt = [r for r in R if r.get("dir") == "emit" and r.get("kind") == "text"]
    rangos = [(round(t["payload"]["audio_start"], 2), round(t["payload"]["audio_end"], 2)) for t in txt]
    if len(set(rangos)) < len(rangos) and not all(isinstance(t.get("ventanas"), list) for t in txt):
        herm = Path(path).with_name(Path(path).stem + "-rederivado.jsonl")
        if herm.is_file():
            cab2, R2 = leer_casete(herm)
            iguales = _capas(R) == _capas(R2)
            nota = f"mapeo degenerado en el original; se usa {herm.name}: capas server+client idénticas = {iguales}"
            if iguales:
                cab, R, usado = cab2, R2, str(herm)
                txt = [r for r in R if r.get("dir") == "emit" and r.get("kind") == "text"]
        else:
            nota = "mapeo degenerado y sin rederivado: resultado NO válido"
    st = cab.get("started_at") or R[0]["t"]
    ven = sorted([r for r in R if r.get("dir") == "client" and r.get("kind") == "ventana"], key=lambda r: r["payload"]["ventana"])
    W = {v["payload"]["ventana"]: v for v in ven}
    # mapeo texto -> ventanas
    mapa = []
    for t in txt:
        if isinstance(t.get("ventanas"), list):
            vs = [int(i) for i in t["ventanas"]]
        else:
            a, b = t["payload"]["audio_start"], t["payload"]["audio_end"]
            vs = [i for i, v in W.items() if abs(v["payload"]["audio_start"] - a) <= 0.05 and abs(v["payload"]["audio_end"] - b) <= 0.05]
        mapa.append((t["payload"]["t_emit"], vs))
    cubierta = {}
    for te, vs in mapa:
        for i in vs:
            cubierta[i] = min(te, cubierta.get(i, te))
    # respondida en t: llegó texto de i o de alguna posterior
    llegadas = sorted((te, max(vs)) for te, vs in mapa if vs)

    def max_respondida_hasta(t):
        m = -1
        for te, i in llegadas:
            if te <= t:
                m = max(m, i)
        return m

    # server: audioOffset de voiceActivity en el tiempo
    va = [(r["t"], _off(r["payload"]["voiceActivity"].get("audioOffset"))) for r in R
          if r.get("dir") == "server" and isinstance(r.get("payload"), dict) and "voiceActivity" in r["payload"]]
    va = [(t, o) for t, o in va if o is not None]
    fin = max(r["t"] for r in R)
    n_cub = int((fin - st) // CUBETA) + 1
    filas = []
    for k in range(n_cub):
        t0, t1 = st + k * CUBETA, st + (k + 1) * CUBETA
        enc = [v for v in ven if t0 <= v["t"] < t1]
        voz = [v for v in enc if v["payload"].get("has_voice")]
        cub = [v for v in voz if v["payload"]["ventana"] in cubierta]
        lat = []
        for te, vs in mapa:
            if vs and max(vs) in W and t0 <= W[max(vs)]["t"] < t1:
                lat.append(te - W[max(vs)]["t"])
        pend, prof = 0, []
        for v in voz:
            k_idx = v["payload"]["ventana"]
            mr = max_respondida_hasta(v["t"])
            antes = [i for i in W if i < k_idx and i > mr and W[i]["payload"].get("has_voice")]
            prof.append(len(antes))
            if antes:
                pend += 1
        enviado = sum(v["payload"]["dur"] for v in ven if v["t"] < t1)
        procesado = max((o for t, o in va if t < t1), default=0.0)
        filas.append({"cubeta_s": f"{int(k * CUBETA)}-{int((k + 1) * CUBETA)}", "ventanas_voz": len(voz),
                      "con_texto": len(cub), "cobertura": round(len(cub) / len(voz), 2) if voz else None,
                      "textos": len(lat), "lat_turno_p50_s": None if not lat else round(pct(lat, 50), 2),
                      "lat_turno_max_s": None if not lat else round(max(lat), 2),
                      "voz_con_cola_pendiente": pend, "cola_max": max(prof) if prof else 0,
                      "audio_enviado_acum_s": round(enviado, 1), "audio_server_acum_s": round(procesado, 1),
                      "atraso_server_s": round(enviado - procesado, 1)})
    return {"casete_pedido": Path(path).as_posix(), "casete_usado": Path(usado).as_posix(), "nota": nota,
            "started_at": st, "cubeta_s": CUBETA, "cubetas": filas}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = sys.argv[1:] if argv is None else argv
    out, rc = [], 0
    for c in args:
        try:
            r = analizar(c)
        except Exception as e:
            print(f"ERROR {c}: {type(e).__name__}: {e}")
            rc = 1
            continue
        out.append(r)
        print(f"\n== {Path(c).name}" + (f"  [{r['nota']}]" if r["nota"] else ""))
        print("cubeta   | voz | c/texto | cobert | textos | lat p50 | lat max | voz c/cola | cola max | enviado | server | atraso")
        for f in r["cubetas"]:
            print(f"{f['cubeta_s']:>8} | {f['ventanas_voz']:3d} | {f['con_texto']:7d} | {str(f['cobertura']):>6} | {f['textos']:6d} | "
                  f"{str(f['lat_turno_p50_s']):>7} | {str(f['lat_turno_max_s']):>7} | {f['voz_con_cola_pendiente']:10d} | "
                  f"{f['cola_max']:8d} | {f['audio_enviado_acum_s']:7.1f} | {f['audio_server_acum_s']:6.1f} | {f['atraso_server_s']:6.1f}")
    p = OUT / "cobertura-edad-b2.json"
    p.write_text(json.dumps({"generado": ahora_ar(), "comando": "python qa/cobertura_edad.py " + " ".join(args),
                             "casetes": out, "exit": rc}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {p} exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
