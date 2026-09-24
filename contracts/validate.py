"""Valida archivos contra el contrato v1.

    python -m contracts.validate contracts/ejemplos/*.jsonl
    python -m contracts.validate fixtures/casetes/x.jsonl     (casete: valida solo las lineas dir=emit)

Por archivo: cada mensaje contra contracts/esquema.json + chequeos semanticos
(audio_end >= audio_start, t_captured <= t_emit) + seq estrictamente creciente POR SESION
dentro del archivo (heartbeat, partial y translation llevan seq null y no cuentan).
type=translation (AMPLIADO 24/09 B2): ademas del esquema, cada item debe apuntar a un seq de un
`text` de la MISMA sesion en el archivo; si no, se imprime un AVISO (no es error: el hub guarda el
item pendiente hasta que llegue el text, ver contracts/README.md).

Exit 0: todo valido. Exit 1: algun mensaje invalido. Exit 2: uso incorrecto / archivo inexistente.
Si el shell no expande el comodin (cmd, PowerShell), se expande aca.
"""
from __future__ import annotations

import argparse
import glob
import sys
from collections import Counter
from pathlib import Path

from contracts import errores, leer_archivo


def _expandir(patrones: list[str]) -> tuple[list[Path], list[str]]:
    archivos, faltan = [], []
    for p in patrones:
        if any(c in p for c in "*?["):
            hallados = sorted(glob.glob(p))
            if hallados:
                archivos.extend(Path(h) for h in hallados)
            else:
                faltan.append(p)
        elif Path(p).is_file():
            archivos.append(Path(p))
        else:
            faltan.append(p)
    return archivos, faltan


def validar_archivo(path: Path, out=sys.stdout) -> tuple[int, int]:
    """Devuelve (mensajes_leidos, errores). Imprime un renglon por error y un resumen."""
    n_msgs = n_err = 0
    tipos: Counter = Counter()
    ultimo_seq: dict[str, tuple[int, int]] = {}
    textos: dict[str, set[int]] = {}          # seq de los type=text por sesion
    items: list[tuple[int, str, int]] = []    # (linea, sesion, seq) de cada item de translation
    for linea, msg in leer_archivo(path):
        n_msgs += 1
        if isinstance(msg, Exception):
            n_err += 1
            print(f"ERROR {path}:{linea}: JSON invalido: {msg}", file=out)
            continue
        errs = errores(msg)
        if isinstance(msg, dict):
            tipos[str(msg.get("type"))] += 1
        if not errs and isinstance(msg, dict) and msg.get("seq") is not None:
            sid, seq = msg["session_id"], msg["seq"]
            if sid in ultimo_seq and seq <= ultimo_seq[sid][0]:
                errs.append(f"seq: {seq} no es mayor que el anterior {ultimo_seq[sid][0]} "
                            f"(linea {ultimo_seq[sid][1]}) de la sesion {sid!r}")
            else:
                ultimo_seq[sid] = (seq, linea)
        if not errs and isinstance(msg, dict):
            if msg.get("type") == "text":
                textos.setdefault(msg["session_id"], set()).add(msg["seq"])
            elif msg.get("type") == "translation":
                items.extend((linea, msg["session_id"], it["seq"]) for it in msg["items"])
        for e in errs:
            n_err += 1
            print(f"ERROR {path}:{linea}: {e}", file=out)
    for linea, sid, seq in items:
        if seq not in textos.get(sid, ()):
            print(f"AVISO {path}:{linea}: item de translation con seq={seq} sin text de la sesion "
                  f"{sid!r} en este archivo (el hub lo deja pendiente)", file=out)
    resumen = ", ".join(f"{t}={c}" for t, c in sorted(tipos.items())) or "sin mensajes"
    estado = "OK" if n_err == 0 and n_msgs > 0 else "FALLA"
    if n_msgs == 0:
        n_err += 1
        print(f"ERROR {path}: el archivo no tiene mensajes del contrato", file=out)
    print(f"{estado} {path}: {n_msgs} mensajes ({resumen}), {n_err} errores", file=out)
    return n_msgs, n_err


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m contracts.validate", description=__doc__.splitlines()[0])
    ap.add_argument("archivos", nargs="+", help=".jsonl (un mensaje por linea, o casete), .json (objeto o lista)")
    args = ap.parse_args(argv)
    archivos, faltan = _expandir(args.archivos)
    for f in faltan:
        print(f"ERROR no existe o no coincide ningun archivo: {f}")
    if faltan or not archivos:
        return 2
    total_msgs = total_err = 0
    for a in archivos:
        m, e = validar_archivo(a)
        total_msgs += m
        total_err += e
    print(f"TOTAL: {len(archivos)} archivos, {total_msgs} mensajes, {total_err} errores")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
