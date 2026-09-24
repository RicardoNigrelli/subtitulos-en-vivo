"""Latencia desde archivos en disco (no corre nada: recalcula siempre desde casetes y logs del bus).

    .venv/Scripts/python qa/latencia.py --casetes "fixtures/casetes/*.jsonl" --bus-tags b2-replay,b2-replay-c2 [--salida qa/out/latencia-b2.json]

(a) WORKER, desde el casete: por cada línea emit `text`, t_emit - t_captured (t_captured = cuándo entró la
    última muestra de audio del bloque, t_emit = cuándo el worker emitió). Medido en B1 con ASR real.
    OJO: sólo entran los bloques que TUVIERON texto; las ventanas con voz sin texto no tienen latencia
    (se informan al lado, ver qa/cobertura.py).
(b) BUS, desde qa/out/smoke-<tag>-*.bus.jsonl (cliente /ws real sobre un hub real, t_receive por frame):
    hub_mas_red = t_receive - t_emit. Contra un REPLAY esto mide SÓLO el sobrecosto hub+red (el replay
    reestampa t_emit al publicar). 'percibida_replay' = t_receive - t_captured conserva la latencia del
    worker grabada + el sobrecosto medido: NO es la percibida real, que se mide en B3/B5 sobre corridas vivas.
(c) PERCIBIDA EN VIVO (B3), desde qa/out/smoke-<tag>-qa-<tag>-N.bus.jsonl de un smoke REAL (worker.run, ASR real):
    t_receive (cliente /ws) - t_captured (worker), por sesión y por corrida; + la de la traducción
    (t_receive del `translation` - t_captured del `text` del mismo seq). Rechaza (exit 1) buses de replay.
        .venv/Scripts/python qa/latencia.py --casetes "qa/out/smoke-b3-qa-b3-*.casete.jsonl" --bus-tags "" --vivo-tags b3 --salida qa/out/latencia-b3.json
p50/p95 nearest-rank, por casete / por sesión y corrida. Nunca promedio.
Exit 0 = calculado; 1 = faltan archivos, no hay textos o un tag "vivo" no viene de worker.run.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.bus import analizar_bus  # noqa: E402
from qa.cobertura import cobertura_casete  # noqa: E402
from qa.comun import OUT, RAIZ, ahora_ar, leer_casete, resumen  # noqa: E402


def latencia_casete(path) -> dict:
    cab, R = leer_casete(path)
    txt = [r for r in R if r.get("dir") == "emit" and r.get("kind") == "text"]
    ven = {r["payload"]["ventana"]: r["payload"] for r in R if r.get("dir") == "client" and r.get("kind") == "ventana"}
    vals = [r["payload"]["t_emit"] - r["payload"]["t_captured"] for r in txt]
    # cruce: t_captured del texto == t_captured de la última ventana mapeada (si el casete lo permite)
    cruce_n = cruce_ok = 0
    desde_primera = []  # t_emit - t_captured de la PRIMERA ventana del bloque (lo que esperó la primera palabra)
    for r in txt:
        vs = r.get("ventanas")
        if isinstance(vs, list) and vs and all(v in ven and "t_captured" in ven[v] for v in vs):
            cruce_n += 1
            if abs(max(ven[v]["t_captured"] for v in vs) - r["payload"]["t_captured"]) < 1e-3:
                cruce_ok += 1
            desde_primera.append(r["payload"]["t_emit"] - min(ven[v]["t_captured"] for v in vs))
    cob = cobertura_casete(path)
    return {"casete": Path(path).as_posix(), "session_id": cab.get("session_id"), "lang": cab.get("lang"),
            "t_emit_menos_t_captured_s": resumen(vals),
            "t_emit_menos_t_captured_primera_ventana_s": resumen(desde_primera) if desde_primera else None,
            "t_captured_verificable_contra_ventana": f"{cruce_ok}/{cruce_n}" if cruce_n else "no (el casete no trae t_captured en la ventana o no trae el campo ventanas)",
            "ventanas_con_voz": cob["ventanas_con_voz"], "ventanas_voz_sin_texto_no_entran": cob["ventanas_con_voz"] - cob["ventanas_voz_con_texto"]}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--casetes", default="fixtures/casetes/*.jsonl", help="glob")
    ap.add_argument("--bus-tags", default="b2-replay,b2-replay-c2", help="tags de qa/smoke.py = corridas")
    ap.add_argument("--salida", default=str(OUT / "latencia-b2.json"))
    ap.add_argument("--vivo-tags", default="",
                    help="tags de qa/smoke.py en MODO REAL (worker.run, ASR real) = corridas: percibida EN VIVO")
    a = ap.parse_args(argv)
    rc = 0
    casetes = sorted(glob.glob(str(RAIZ / a.casetes)) or glob.glob(a.casetes))
    A = []
    for c in casetes:
        r = latencia_casete(c)
        A.append(r)
        s = r["t_emit_menos_t_captured_s"]
        print(f"(a) {Path(c).name:45s} n={s['n']:3d} p50={s['p50']} p95={s['p95']} min={s['min']} max={s['max']} "
              f"| sin texto (no entran): {r['ventanas_voz_sin_texto_no_entran']}/{r['ventanas_con_voz']} ventanas con voz "
              f"| t_captured vs ventana: {r['t_captured_verificable_contra_ventana']}")
    if not A:
        print("no hay casetes")
        rc = 1
    B = {}
    for i, tag in enumerate([t for t in a.bus_tags.split(",") if t], 1):
        files = sorted(glob.glob(str(OUT / f"smoke-{tag}-qa-{tag}-[0-9]*.bus.jsonl")))
        if not files:
            print(f"(b) corrida {i} tag={tag}: SIN archivos qa/out/smoke-{tag}-*.bus.jsonl")
            rc = 1
            continue
        B[f"corrida{i}:{tag}"] = {}
        for f in files:
            r = analizar_bus(f)
            L = r["latencia_s"]
            B[f"corrida{i}:{tag}"][r["sid"]] = {"bus": Path(f).relative_to(RAIZ).as_posix(), "textos": r["textos"],
                                                "hub_mas_red_s": L["hub_mas_red"], "hub_interno_s": L["hub_interno"],
                                                "entrega_hub_cliente_s": L["entrega_hub_cliente"],
                                                "percibida_replay_s": L["percibida"]}
            print(f"(b) corrida {i} {r['sid']:28s} n={L['hub_mas_red']['n']:3d} hub+red p50={L['hub_mas_red']['p50']} "
                  f"p95={L['hub_mas_red']['p95']} | t_hub-t_emit p50={L['hub_interno']['p50']} p95={L['hub_interno']['p95']} "
                  f"| percibida_replay p50={L['percibida']['p50']} p95={L['percibida']['p95']}")
            if not r["textos"]:
                rc = 1
    # (c) PERCIBIDA EN VIVO: t_receive (cliente /ws) - t_captured (worker), sobre corridas REALES de qa/smoke.py.
    V = {}
    for i, tag in enumerate([t for t in a.vivo_tags.split(",") if t], 1):
        # B5: session_id con nombre (qa-b5-en, qa-b5-es) además de los numerados (qa-b3-1)
        files = sorted(glob.glob(str(OUT / f"smoke-{tag}-qa-{tag}-*.bus.jsonl")))
        if not files:
            print(f"(c) corrida {i} tag={tag}: SIN archivos qa/out/smoke-{tag}-qa-{tag}-*.bus.jsonl")
            rc = 1
            continue
        V[f"corrida{i}:{tag}"] = {}
        for f in files:
            meta = json.loads(Path(f).read_text(encoding="utf-8").splitlines()[0])
            cmd = " ".join(meta.get("cmd") or [])
            productor = "worker.run" if "worker.run" in cmd else ("worker.replay" if "worker.replay" in cmd else "?")
            r = analizar_bus(f)
            L, T = r["latencia_s"], r["traduccion"]
            V[f"corrida{i}:{tag}"][r["sid"]] = {
                "bus": Path(f).relative_to(RAIZ).as_posix(), "productor": productor, "lang": meta.get("lang"),
                "textos": r["textos"], "percibida_s": L["percibida"], "hub_mas_red_s": L["hub_mas_red"],
                "traduccion": {k: T[k] for k in ("eventos", "items_ok", "items_ok_false", "textos_sin_ningun_item")},
                "percibida_traduccion_s": T["percibida_traduccion_s"], "text_a_translation_s": T["text_a_translation_s"],
                "rotaciones": [(x["seq"], x["reason"]) for x in r["rotaciones"]]}
            if productor != "worker.run":
                print(f"(c) {f}: el productor NO es worker.run ({productor}): no es percibida en vivo")
                rc = 1
            if not r["textos"]:
                rc = 1
            print(f"(c) corrida {i} {r['sid']:14s} [{productor}] n={L['percibida']['n']:3d} PERCIBIDA p50={L['percibida']['p50']} "
                  f"p95={L['percibida']['p95']} max={L['percibida']['max']} | hub+red p50={L['hub_mas_red']['p50']} "
                  f"p95={L['hub_mas_red']['p95']} | traducción ok={T['items_ok']} ok:false={T['items_ok_false']} "
                  f"percibida trad p50={T['percibida_traduccion_s']['p50']} p95={T['percibida_traduccion_s']['p95']} "
                  f"(n={T['percibida_traduccion_s']['n']})")
    doc = {"generado": ahora_ar(),
           "comando": "python qa/latencia.py " + " ".join(sys.argv[1:] if argv is None else argv),
           "metodo_percentil": "nearest-rank; nunca promedio",
           "a_worker_desde_casete": {"que_mide": "t_emit - t_captured por texto (ASR real de B1). Sólo bloques con texto.",
                                     "casetes": A},
           "b_bus_replay": {"que_mide": "t_receive - t_emit = SOBRECOSTO hub+red contra replay. 'percibida_replay' = "
                                        "latencia del worker grabada + sobrecosto; NO es la percibida real (B3/B5: t_receive - t_captured en vivo).",
                            "corridas": B},
           "c_percibida_en_vivo": {"que_mide": "t_receive (cliente /ws sobre el hub) - t_captured (worker.run, ASR REAL), "
                                               "por texto, por sesión y por corrida (tag). percibida_traduccion = t_receive del "
                                               "evento translation - t_captured del text del mismo seq (sólo items ok). "
                                               "Con UNA corrida por configuración es una observación, no un resultado.",
                                   "corridas": V},
           "exit": rc}
    Path(a.salida).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {a.salida} exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
