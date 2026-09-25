"""A partir de `corte-v2-timeline.json` (que escribe `componer.py`), arma el `--offset-map` que
espera `docs/export/exportar.py`: por cada tramo de narracion (n1..n6), el rango [concat_inicio,
concat_inicio+duracion) en la linea de tiempo del WAV DE TRABAJO (narracion-es.wav, la concatenacion
de los 6 WAV de narracion en orden) y cuanto hay que sumarle para caer en el video FINAL editado.

Uso:
    .venv/Scripts/python docs/video/generar_offset_map.py \
        --timeline reportes/video/corte-v2-timeline.json \
        --salida reportes/video/narracion-offset-map.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", default=str(RAIZ / "reportes/video/corte-v2-timeline.json"))
    ap.add_argument("--salida", default=str(RAIZ / "reportes/video/narracion-offset-map.json"))
    args = ap.parse_args()

    datos = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    offsets = datos["narracion_offsets"]

    mapa = []
    for clave, info in offsets.items():
        mapa.append({
            "clave": clave,
            "desde": info["concat_inicio"],
            "hasta": round(info["concat_inicio"] + info["duracion"], 3),
            "offset": info["offset"],
        })
    mapa.sort(key=lambda r: r["desde"])
    Path(args.salida).write_text(json.dumps(mapa, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK: {len(mapa)} tramos -> {args.salida}")
    for r in mapa:
        print(f"  {r['clave']}: [{r['desde']}, {r['hasta']}) offset={r['offset']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
