"""Glosario automatico desde la agenda (R8c) -> custom_vocabulary de Gemini Live.

Skill gemini-live: custom_vocabulary corrige NOMBRES PROPIOS (no mejora la tasa de error global). La agenda
del evento ya trae los nombres que van a sonar: orador, empresa, tecnologias, titulo y resumen.

Formato de la agenda (fixtures/agenda.json, UTF-8):

    {"evento": "Nerdearla 2026",
     "terminos_evento": ["Nerdearla"],            # van en TODAS las sesiones
     "sesiones": [
       {"id": "booch-en",                         # --charla (default: --sesion de worker.run)
        "titulo": "...", "orador": "Nombre Apellido", "empresa": "..." | null,
        "idioma": "en" | "es", "url": "https://...",
        "resumen": "...",                         # texto publico de la charla (de ahi salen siglas y
                                                  #  nombres propios)
        "tecnologias": ["..."],                   # lista cargada por el organizador
        "terminos": ["..."],                      # nombres que el orador avisa que va a decir
        "fuente_datos": "de donde sale cada campo"}]}

Extraccion por sesion, en este orden de prioridad (dedup sin distinguir mayusculas, tope
MAX_TERMINOS): terminos_evento; orador (nombre completo y apellido); empresa; tecnologias; terminos;
nombres propios del titulo y del resumen (siglas de 2-6 mayusculas, CamelCase y secuencias de
palabras Capitalizadas; una palabra capitalizada suelta al inicio de oracion NO cuenta).

    python -m worker.glosario fixtures/agenda.json --charla booch-en      # imprime el vocabulario
Exit: 0 = ok; 1 = charla no encontrada; 2 = uso / agenda invalida.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_TERMINOS = 40
_MAY = "A-ZÁÉÍÓÚÑÜ"
_MIN = "a-záéíóúñü"
_SIGLA = re.compile(rf"\b[{_MAY}]{{2,6}}s?\b")
_CAMEL = re.compile(rf"\b[{_MAY}]?[{_MIN}]+[{_MAY}][\w]+\b")
_CAP = re.compile(rf"\b[{_MAY}][{_MIN}'’-]+(?:\s+(?:of|de|del|the|y|and)?\s*[{_MAY}][{_MIN}'’-]+)*")
_PARADA = {w.lower() for w in (
    "The A An And Or But In On At As Of For With From By To Is It He She His Her They We You I "
    "This That These Those What Why How When Where Also Since Track Formato Charla Sobre "
    "El La Los Las Un Una Y O Pero En Con Sin Por Para Desde Hasta Cada Ya Si Veremos Vivimos "
    "Muchas Tambien También Esta Este Estas Estos Como Cómo Que Qué Donde Cuando").split()}


def _limpio(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip(" .,;:()\"'“”«»")


def nombres_propios(texto: str) -> list[str]:
    """Siglas, CamelCase y secuencias Capitalizadas de `texto` (en orden de aparicion)."""
    out: list[tuple[int, str]] = []
    texto = str(texto or "")
    for m in _SIGLA.finditer(texto):
        out.append((m.start(), m.group(0)))
    for m in _CAMEL.finditer(texto):
        out.append((m.start(), m.group(0)))
    for m in _CAP.finditer(texto):
        frase = m.group(0).strip()
        palabras = frase.split()
        quitadas = 0
        while palabras and palabras[0].lower() in _PARADA:     # "En Nerdearla" -> "Nerdearla"
            palabras = palabras[1:]
            quitadas += 1
        frase = " ".join(palabras)
        if not frase or frase.lower() in _PARADA:
            continue
        previo = texto[:m.start()].rstrip()
        inicio_oracion = quitadas == 0 and ((not previo) or previo[-1] in ".!?:\n¿¡-–—\"“")
        if len(palabras) == 1 and inicio_oracion:
            continue
        if len(palabras) == 1 and len(frase) < 3:
            continue
        out.append((m.start(), frase))
    out.sort(key=lambda x: x[0])
    return [t for _, t in out]


def _agregar(dest: list[str], vistos: set, termino: str) -> None:
    t = _limpio(termino)
    if not t or len(t) < 2 or t.lower() in vistos or t.lower() in _PARADA:
        return
    vistos.add(t.lower())
    dest.append(t)


def terminos_charla(agenda: dict, charla: dict, maximo: int = MAX_TERMINOS) -> list[str]:
    out: list[str] = []
    vistos: set = set()
    for t in agenda.get("terminos_evento") or []:
        _agregar(out, vistos, t)
    orador = _limpio(charla.get("orador"))
    if orador:
        _agregar(out, vistos, orador)
        partes = orador.split()
        if len(partes) > 1:
            _agregar(out, vistos, partes[-1])
    if charla.get("empresa"):
        _agregar(out, vistos, charla["empresa"])
    for campo in ("tecnologias", "terminos"):
        for t in charla.get(campo) or []:
            _agregar(out, vistos, t)
    for campo in ("titulo", "resumen"):
        for t in nombres_propios(charla.get(campo) or ""):
            _agregar(out, vistos, t)
    return out[:maximo]


def cargar(ruta: str | Path) -> dict:
    agenda = json.loads(Path(ruta).read_text(encoding="utf-8"))
    if not isinstance(agenda, dict) or not isinstance(agenda.get("sesiones"), list):
        raise ValueError("agenda sin lista 'sesiones'")
    return agenda


def buscar(agenda: dict, charla_id: str) -> dict | None:
    for s in agenda["sesiones"]:
        if str(s.get("id")) == str(charla_id):
            return s
    return None


def vocabulario(ruta: str | Path, charla_id: str, maximo: int = MAX_TERMINOS) -> list[str]:
    """custom_vocabulary de la charla `charla_id` de la agenda `ruta` (KeyError si no esta)."""
    agenda = cargar(ruta)
    ch = buscar(agenda, charla_id)
    if ch is None:
        ids = ", ".join(str(s.get("id")) for s in agenda["sesiones"])
        raise KeyError(f"charla {charla_id!r} no esta en {ruta} (hay: {ids})")
    return terminos_charla(agenda, ch, maximo)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.glosario")
    ap.add_argument("agenda")
    ap.add_argument("--charla", default=None, help="id de la sesion en la agenda (sin: todas)")
    ap.add_argument("--max", type=int, default=MAX_TERMINOS)
    a = ap.parse_args(argv)
    try:
        agenda = cargar(a.agenda)
    except (OSError, ValueError) as e:
        print(f"[glosario] agenda invalida: {e}", file=sys.stderr)
        return 2
    ids = [a.charla] if a.charla else [str(s.get("id")) for s in agenda["sesiones"]]
    out = {}
    for i in ids:
        ch = buscar(agenda, i)
        if ch is None:
            print(f"[glosario] charla {i!r} no encontrada", file=sys.stderr)
            return 1
        out[i] = terminos_charla(agenda, ch, a.max)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
