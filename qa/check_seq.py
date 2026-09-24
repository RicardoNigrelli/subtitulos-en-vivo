"""Chequeo de contrato/bus sobre la capa `emit` de casetes: `seq` monotónico y SIN huecos.

    .venv/Scripts/python qa/check_seq.py fixtures/casetes/*.jsonl

Por casete, sobre los mensajes emit con seq != null (todo menos heartbeat), en orden de archivo:
  - seq entero, cada uno = anterior + 1 (ni huecos ni repetidos ni retrocesos)
  - un solo session_id; t_emit no decreciente; en `text`: t_captured <= t_emit y texto no vacío
  - además, cada mensaje valida contra el contrato (`contracts.errores`)
Exit 0 = todo OK · 1 = algún casete viola algo · 2 = archivo inexistente o ilegible.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.comun import leer_casete  # noqa: E402


def chequear(path) -> list[str]:
    from contracts import errores
    _, R = leer_casete(path)
    em = [r["payload"] for r in R if r.get("dir") == "emit" and isinstance(r.get("payload"), dict)]
    con_seq = [m for m in em if m.get("seq") is not None]
    prob = []
    if not con_seq:
        return ["no hay mensajes emit con seq"]
    sids = {m.get("session_id") for m in em}
    if len(sids) != 1:
        prob.append(f"más de un session_id: {sorted(map(str, sids))}")
    prev, prev_te = None, None
    for m in con_seq:
        s = m.get("seq")
        if not isinstance(s, int):
            prob.append(f"seq no entero: {s!r}")
            continue
        if prev is not None and s != prev + 1:
            prob.append(f"seq {s} después de {prev} ({'hueco' if s > prev + 1 else 'repetido/retroceso'})")
        prev = s
    for m in em:
        te = m.get("t_emit")
        if isinstance(te, (int, float)):
            if prev_te is not None and te < prev_te:
                prob.append(f"t_emit decrece en seq={m.get('seq')}: {te} < {prev_te}")
            prev_te = te
        if m.get("type") == "text":
            if not (m.get("t_captured") is not None and m["t_captured"] <= m["t_emit"]):
                prob.append(f"seq={m.get('seq')}: t_captured > t_emit o nulo")
        for e in errores(m):
            prob.append(f"contrato seq={m.get('seq')}: {e}")
    return prob


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    rc = 0
    for c in args:
        if not Path(c).is_file():
            print(f"ERROR {c}: no existe")
            return 2
        try:
            p = chequear(c)
        except Exception as e:
            print(f"ERROR {c}: ilegible ({type(e).__name__}: {e})")
            return 2
        _, R = leer_casete(c)
        seqs = [r["payload"]["seq"] for r in R if r.get("dir") == "emit" and r["payload"].get("seq") is not None]
        print(f"{'OK  ' if not p else 'FALLA'} {Path(c).name}: {len(seqs)} con seq, {seqs[0] if seqs else '-'}..{seqs[-1] if seqs else '-'}, problemas={len(p)}")
        for x in p:
            print(f"   - {x}")
        if p:
            rc = 1
    print(f"exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
