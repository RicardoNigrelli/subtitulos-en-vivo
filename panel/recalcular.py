"""Recalcula p50/p95 de la latencia del worker desde las lineas `emit` type=text de UN casete en
disco, con la MISMA formula y el MISMO metodo de percentil que panel/app.js: para cruzar contra lo
que muestra el panel (definicion de terminado del brief B3, y skill anti-alucinacion: "un script
recalcula p50/p95 desde el casete en disco y coincide con lo que muestra el panel").

Formula (contracts/README.md, campo t_captured): t_emit - t_captured por mensaje type=text. Es la
latencia del WORKER. Si el casete es replay:true esa cifra es la GRABADA (worker.replay reestampa
t_emit y corre t_captured el mismo delta al reproducir, asi que la resta no cambia: ver
worker/replay.py "rotular"). No incluye la latencia PERCIBIDA (t_receive - t_captured): esa se mide
en vivo, en el WebSocket del panel, no se puede recalcular desde un archivo.

    .venv/Scripts/python panel/recalcular.py fixtures/casetes/b1-es-60s-trad.jsonl
    .venv/Scripts/python panel/recalcular.py fixtures/casetes/b1-en-60s-rederivado-trad.jsonl

Exit 0 = se encontro al menos un mensaje type=text con t_emit y t_captured numericos (n >= 1);
1 = ninguno (archivo vacio, sin `text`, o inexistente).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # para "from contracts import ..."
from contracts import leer_archivo  # noqa: E402

MIN_N_PERCENTIL = 10  # bajo esto, panel/app.js muestra "n insuficiente" en vez de un p95 fabricado


def pct(valores_ordenados: list, q: float):
    """nearest-rank: valor en la posicion ceil(q/100*n) de la lista YA ordenada (1-indexado).
    Misma definicion que qa/comun.py:pct (verificado leyendo ese archivo; panel no lo importa
    para no depender de la carpeta de otro agente, pero usa la MISMA formula estandar)."""
    n = len(valores_ordenados)
    if not n:
        return None
    r = max(1, math.ceil(q / 100 * n))
    return valores_ordenados[r - 1]


def latencias_texto(path) -> list:
    """t_emit - t_captured de cada linea `emit` type=text del casete (o de un .jsonl del contrato
    a secas: leer_archivo soporta ambos formatos, ver contracts/README.md)."""
    vals = []
    for _linea, obj in leer_archivo(path):
        if isinstance(obj, Exception) or not isinstance(obj, dict):
            continue
        if obj.get("type") != "text":
            continue
        te, tc = obj.get("t_emit"), obj.get("t_captured")
        if isinstance(te, (int, float)) and isinstance(tc, (int, float)):
            vals.append(te - tc)
    return vals


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python panel/recalcular.py",
                                 description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("casete", help="fixtures/casetes/<x>.jsonl (o cualquier .jsonl/.json del contrato)")
    a = ap.parse_args(argv)

    if not Path(a.casete).is_file():
        print(f"ERROR: no existe {a.casete}")
        return 1

    vals = sorted(latencias_texto(a.casete))
    n = len(vals)
    p50, p95 = pct(vals, 50), pct(vals, 95)
    r3 = lambda x: None if x is None else round(x, 3)
    doc = {
        "casete": Path(a.casete).as_posix(),
        "formula": "t_emit - t_captured por mensaje type=text (linea dir=emit del casete)",
        "metodo_percentil": "nearest-rank (posicion ceil(q/100*n), 1-indexado, sobre valores ordenados); NUNCA promedio",
        "n": n,
        "p50_s": r3(p50),
        "p95_s": r3(p95),
        "min_s": r3(vals[0]) if vals else None,
        "max_s": r3(vals[-1]) if vals else None,
        "n_insuficiente_para_p95": n < MIN_N_PERCENTIL,
    }
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    if n == 0:
        print(f"ERROR: {a.casete} no tiene mensajes type=text con t_emit y t_captured numericos")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
