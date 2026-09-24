"""Dedup de costuras A NIVEL DE CARACTERES (skill gemini-live: el solape de 400 ms y el reenvio al
reabrir duplican texto en las costuras).

Regla: si el SUFIJO del ultimo texto emitido coincide con el PREFIJO del texto nuevo (comparados
normalizados: minusculas, sin puntuacion, espacios colapsados) en >= MIN_CHARS caracteres y en
borde de palabra en los dos lados, se corta ese prefijo del texto nuevo. Si no queda nada, el texto
nuevo es un duplicado entero (devuelve ""). No inventa texto: solo quita caracteres del nuevo.
"""
from __future__ import annotations

import unicodedata

MIN_CHARS = 3          # "the", "que": una palabra de 1-2 letras ("a", "de") no alcanza para cortar
_PUNTA = " \t\n,.;:!?¡¿-–—…\"'()[]"


def _norm_con_indices(s: str) -> tuple[str, list[int]]:
    """Normaliza `s` y devuelve el indice ORIGINAL de cada caracter normalizado."""
    out: list[str] = []
    idx: list[int] = []
    espacio = True
    for i, ch in enumerate(s or ""):
        c = unicodedata.normalize("NFC", ch).casefold()
        if c.isalnum():
            out.append(c)
            idx.append(i)
            espacio = False
        elif not espacio:
            out.append(" ")
            idx.append(i)
            espacio = True
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def solape(previo: str, nuevo: str, min_chars: int = MIN_CHARS) -> int:
    """Largo (en caracteres normalizados) del mayor sufijo de `previo` = prefijo de `nuevo`, en borde
    de palabra en los dos lados; 0 si no hay (o es menor que min_chars)."""
    p, _ = _norm_con_indices(previo)
    n, _ = _norm_con_indices(nuevo)
    for k in range(min(len(p), len(n)), min_chars - 1, -1):
        if n[k - 1] == " ":
            continue
        if (k == len(n) or n[k] == " ") and (k == len(p) or p[-k - 1] == " ") and p.endswith(n[:k]):
            return k
    return 0


def dedup_costura(previo: str | None, nuevo: str, min_chars: int = MIN_CHARS) -> tuple[str, int]:
    """(texto nuevo sin la parte repetida, caracteres normalizados quitados)."""
    if not previo or not nuevo:
        return nuevo, 0
    k = solape(previo, nuevo, min_chars)
    if k == 0:
        return nuevo, 0
    _, idx = _norm_con_indices(nuevo)
    corte = idx[k - 1] + 1
    resto = nuevo[corte:].lstrip(_PUNTA)
    return resto, k
