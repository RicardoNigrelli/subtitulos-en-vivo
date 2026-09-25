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


# ---- ROJO 4 (verificacion final): dedup por CONTENIDO en la costura de una rotacion --------------
# El sufijo/prefijo de arriba no alcanza cuando la conexion VIEJA, drenando, emite un texto FUSIONADO
# que tiene ADENTRO (no en la punta) lo que la NUEVA ya emitio (casete vf-smoke-final-en: la vieja
# emitio "as an abstraction ... until the" + [el texto entero de la nueva] + "All it requires two or
# three more"). Se compara el contenido COMPACTO (minusculas, sin puntuacion ni espacios).
MIN_CONTENIDO = 10     # caracteres compactos: una frase corta legitima repetida ("thank you") no cuenta


def _compacto_con_indices(s: str) -> tuple[str, list[int]]:
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s or ""):
        c = unicodedata.normalize("NFC", ch).casefold()
        if c.isalnum():
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def compacto(s: str) -> str:
    return _compacto_con_indices(s)[0]


def contenido_repetido(nuevo: str, recientes: list[str], min_chars: int = MIN_CONTENIDO):
    """("contenido", []) si el nuevo entero ya esta dentro de algun reciente (duplicado: no se emite);
    ("contiene", pedazos) si el nuevo contiene ENTERO a uno o mas recientes: `pedazos` = lo que queda
    del nuevo fuera de esos tramos, como [(texto, i_reciente_anterior | None, i_reciente_siguiente | None)]
    (los i sirven para ubicar el pedazo en el audio); (None, []) si no hay repeticion."""
    n, idx = _compacto_con_indices(nuevo)
    if len(n) < min_chars:
        return None, []
    comp = [compacto(r) for r in recientes]
    if any(n in rc for rc in comp):
        return "contenido", []
    tramos = []
    for i, rc in enumerate(comp):
        if len(rc) >= min_chars:
            k = n.find(rc)
            if k >= 0:
                tramos.append((idx[k], idx[k + len(rc) - 1] + 1, i))
    if not tramos:
        return None, []
    tramos.sort()
    pedazos, cursor, previo = [], 0, None
    for ini, fin, i in tramos:
        if ini > cursor:
            t = nuevo[cursor:ini].strip(_PUNTA)
            if compacto(t):
                pedazos.append((t, previo, i))
        if fin > cursor:
            cursor, previo = fin, i
    t = nuevo[cursor:].strip(_PUNTA)
    if compacto(t):
        pedazos.append((t, previo, None))
    return "contiene", pedazos
