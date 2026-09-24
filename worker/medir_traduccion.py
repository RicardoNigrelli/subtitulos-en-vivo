"""R19 en vivo: cuantas traducciones llegaron bien y con cuanto atraso, recalculado DESDE LOS CASETES.

    python -m worker.medir_traduccion fixtures/casetes/b4-trad-vivo-en.jsonl fixtures/casetes/b4-trad-vivo-es.jsonl

Por casete (lineas `emit` del casete = lo que el worker publico al bus):
- total  = mensajes `text` emitidos.
- ok     = `text` cuyo seq tiene al menos un item `ok:true` en algun `translation`.
- atraso = t_emit del PRIMER `translation` con ese seq ok:true - t_emit del `text` (solo los ok).
- resuelto = atraso hasta el primer item de ese seq, ok o no (cuanto tardo en saberse el resultado).
Percentiles NEAREST-RANK: el valor de rango ceil(p/100 * n) en la lista ordenada (1-indexado).
Exit 0 = calculado; 1 = algun casete sin textos.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def nearest_rank(xs: list[float], p: float):
    if not xs:
        return None
    s = sorted(xs)
    k = max(1, math.ceil(p / 100.0 * len(s)))
    return round(s[k - 1], 3)


def medir(path: str) -> dict:
    lineas = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    cab, evs = lineas[0], lineas[1:]
    emit = [e["payload"] for e in evs if e.get("dir") == "emit"]
    textos = {m["seq"]: m for m in emit if m["type"] == "text"}
    primero_ok: dict[int, float] = {}
    primero: dict[int, float] = {}
    estados: dict[str, int] = {}
    n_trad = 0
    for m in emit:
        if m["type"] != "translation":
            continue
        n_trad += 1
        for it in m.get("items") or []:
            s = it.get("seq")
            if s not in textos:
                continue
            primero.setdefault(s, m["t_emit"])
            if it.get("ok"):
                primero_ok.setdefault(s, m["t_emit"])
    for m in emit:
        if m["type"] == "translation":
            for i in (m.get("meta") or {}).get("source", {}).get("intentos", []) if isinstance(
                    (m.get("meta") or {}).get("source"), dict) else []:
                estados[i.get("estado")] = estados.get(i.get("estado"), 0) + 1
    atraso = [primero_ok[s] - textos[s]["t_emit"] for s in primero_ok]
    resuelto = [primero[s] - textos[s]["t_emit"] for s in primero]
    total = len(textos)
    return {"casete": Path(path).as_posix(), "session_id": cab.get("session_id"), "lang": cab.get("lang"),
            "textos": total, "translation_eventos": n_trad, "ok": len(primero_ok),
            "ok_frac": round(len(primero_ok) / total, 3) if total else None,
            "sin_item": total - len(primero),
            "atraso_ok_p50_s": nearest_rank(atraso, 50), "atraso_ok_p95_s": nearest_rank(atraso, 95),
            "atraso_ok_max_s": round(max(atraso), 3) if atraso else None,
            "resuelto_p50_s": nearest_rank(resuelto, 50), "resuelto_p95_s": nearest_rank(resuelto, 95),
            "intentos_por_estado": estados}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):     # B7: antes '--help' se leia como archivo
        print(__doc__)
        return 0 if args else 2
    rc, filas = 0, []
    for c in args:
        r = medir(c)
        filas.append(r)
        if not r["textos"]:
            rc = 1
        print(json.dumps(r, ensure_ascii=False))
    tot = sum(r["textos"] for r in filas)
    ok = sum(r["ok"] for r in filas)
    todos = []
    for c in args:
        lineas = [json.loads(x) for x in Path(c).read_text(encoding="utf-8").splitlines() if x.strip()][1:]
        emit = [e["payload"] for e in lineas if e.get("dir") == "emit"]
        tx = {m["seq"]: m["t_emit"] for m in emit if m["type"] == "text"}
        vistos = set()
        for m in emit:
            if m["type"] == "translation":
                for it in m.get("items") or []:
                    if it.get("ok") and it["seq"] in tx and it["seq"] not in vistos:
                        vistos.add(it["seq"])
                        todos.append(m["t_emit"] - tx[it["seq"]])
    print(f"TOTAL: ok {ok}/{tot} = {round(ok / tot, 3) if tot else None} · atraso ok (nearest-rank) "
          f"p50 {nearest_rank(todos, 50)} s · p95 {nearest_rank(todos, 95)} s · n={len(todos)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
