"""Cuenta apariciones BIEN ESCRITAS de terminos en los textos de un casete (A/B del glosario R8c).

    python -m worker.medir_glosario CASETE "Grady Booch,Jim Rumbaugh,Ivar Jacobson,Simula"
    python -m worker.medir_glosario CASETE --agenda fixtures/agenda.json --charla booch-en

Cuenta sobre los `text` EMITIDOS al bus (lo que ve el publico: despues de la dedup de costuras).
"bien escrita" = coincidencia EXACTA con mayusculas y limites de palabra; `sin_mayusculas` = la misma
busqueda sin distinguir mayusculas (la diferencia son apariciones mal capitalizadas). Tambien imprime
el custom_vocabulary con el que se grabo el casete (cabecera), para saber que config se midio.
Exit: 0 = medido; 1 = casete sin textos; 2 = uso.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def textos(casete: str | Path) -> tuple[dict, list[str]]:
    cab, out = {}, []
    for i, linea in enumerate(Path(casete).read_text(encoding="utf-8").splitlines()):
        if not linea.strip():
            continue
        d = json.loads(linea)
        if i == 0 and "casete" in d:
            cab = d
            continue
        if d.get("dir") == "emit" and (d.get("payload") or {}).get("type") == "text":
            out.append(str(d["payload"].get("text") or ""))
    return cab, out


def contar(ts: list[str], termino: str, mayusculas: bool = True) -> int:
    pat = re.compile(r"(?<!\w)" + re.escape(termino) + r"(?!\w)", 0 if mayusculas else re.I)
    return sum(len(pat.findall(t)) for t in ts)


def medir(casete: str | Path, terminos: list[str]) -> dict:
    cab, ts = textos(casete)
    vocab = (((cab.get("config") or {}).get("input_audio_transcription") or {})
             .get("custom_vocabulary"))
    por = {t: {"bien_escrita": contar(ts, t), "sin_mayusculas": contar(ts, t, False)} for t in terminos}
    return {"casete": str(casete), "textos": len(ts), "custom_vocabulary": vocab,
            "terminos": por, "total_bien_escritas": sum(v["bien_escrita"] for v in por.values()),
            "total_sin_mayusculas": sum(v["sin_mayusculas"] for v in por.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.medir_glosario")
    ap.add_argument("casete")
    ap.add_argument("terminos", nargs="?", default=None, help="separados por comas")
    ap.add_argument("--agenda", default=None)
    ap.add_argument("--charla", default=None)
    a = ap.parse_args(argv)
    if a.terminos:
        terms = [t.strip() for t in a.terminos.split(",") if t.strip()]
    elif a.agenda and a.charla:
        from worker.glosario import vocabulario
        terms = vocabulario(a.agenda, a.charla)
    else:
        ap.error("pasar terminos o --agenda y --charla")
        return 2
    r = medir(a.casete, terms)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["textos"] else 1


if __name__ == "__main__":
    sys.exit(main())
