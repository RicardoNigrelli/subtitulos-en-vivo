"""Contador de minutos de audio enviados a Gemini (skill cuota-gemini).

Cada corrida real apende UNA linea a reportes/cuota-audio.log:
    fecha-hora | sesion | segundos_enviados | archivo_fuente
fecha-hora = fin de la corrida (hora local AR). segundos_enviados = chunks enviados x 0,1 s
(incluye los reenvios de solape: es lo que se manda a la API).

    python -m worker.cuota                      # total del bloque actual y por sesion
    python -m worker.cuota --desde "2026-09-24 13:00"
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOG = Path(os.environ.get("CUOTA_LOG", RAIZ / "reportes" / "cuota-audio.log"))
# Bloque 1: 30 min de audio desde las 13:00 AR (brief B1, reportes/plan.md).
PRESUPUESTO_S = float(os.environ.get("CUOTA_BLOQUE_S", 30 * 60))
DESDE_BLOQUE = os.environ.get("CUOTA_BLOQUE_DESDE", "2026-09-24 13:00:00")
FMT = "%Y-%m-%d %H:%M:%S"


def registrar(sesion: str, segundos: float, archivo: str, log: Path = LOG) -> str:
    log.parent.mkdir(parents=True, exist_ok=True)
    linea = f"{datetime.now().strftime(FMT)} | {sesion} | {segundos:.1f} | {archivo}"
    with open(log, "a", encoding="utf-8") as f:
        f.write(linea + "\n")
    return linea


def leer(log: Path = LOG, desde: str | None = None) -> list[tuple[datetime, str, float, str]]:
    if not log.exists():
        return []
    d0 = datetime.strptime(desde, FMT if len(desde) > 16 else "%Y-%m-%d %H:%M") if desde else None
    out = []
    for linea in log.read_text(encoding="utf-8").splitlines():
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) < 4:
            continue
        try:
            dt = datetime.strptime(partes[0], FMT)
            seg = float(partes[2])
        except ValueError:
            continue
        if d0 and dt < d0:
            continue
        out.append((dt, partes[1], seg, partes[3]))
    return out


def gastado_s(desde: str | None = DESDE_BLOQUE, log: Path = LOG) -> float:
    return sum(r[2] for r in leer(log, desde))


def restante_s(desde: str | None = DESDE_BLOQUE, log: Path = LOG) -> float:
    return PRESUPUESTO_S - gastado_s(desde, log)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.cuota")
    ap.add_argument("--desde", default=DESDE_BLOQUE)
    a = ap.parse_args(argv)
    filas = leer(LOG, a.desde)
    por = defaultdict(float)
    for _, s, seg, _ in filas:
        por[s] += seg
    for s, seg in por.items():
        print(f"{s}: {seg:.1f} s = {seg / 60:.2f} min")
    tot = sum(por.values())
    print(f"TOTAL desde {a.desde}: {tot:.1f} s = {tot / 60:.2f} min "
          f"de {PRESUPUESTO_S / 60:.0f} min ({len(filas)} corridas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
