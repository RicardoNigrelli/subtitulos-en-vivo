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


# ---- compuerta (gate-es): tirada comun EN EL MEDIO de dos textos distintos --------------------------
# Ni la contencion ni el sufijo/prefijo la ven: la vieja emitio "... sereste Brownfield ¿Sí? Bueno, tal
# vez decirdecir uno que empezó ayer ... pasado por ahípodemos ..." y la nueva "este Brownfield ¿Sí? ...
# pasado por ahíposiblemente ...": 19 palabras seguidas iguales, con bordes distintos en los dos lados.
MIN_TIRADA = 6         # palabras normalizadas seguidas; menos que eso puede ser una frase comun legitima


def _palabras(s: str) -> list[tuple[str, int, int]]:
    """[(palabra normalizada, ini, fin)] con indices ORIGINALES."""
    import re
    return [(unicodedata.normalize("NFC", m.group()).casefold(), m.start(), m.end())
            for m in re.finditer(r"\w+", s or "")]


def _tirada_mas_larga(a: list[str], b: list[str]) -> tuple[int, int]:
    """(largo, fin en `a`) de la subcadena comun mas larga de palabras entre a y b."""
    mejor, fin = 0, 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > mejor:
                    mejor, fin = cur[j], i
        prev = cur
    return mejor, fin


def tiradas_repetidas(nuevo: str, recientes: list[str], min_palabras: int = MIN_TIRADA):
    """Quita del nuevo toda tirada de >= min_palabras palabras seguidas que ya este en algun reciente.
    Devuelve None si no hay ninguna; si hay, los pedazos que quedan EN ORDEN como en contenido_repetido
    [(texto, i_reciente_anterior | None, i_reciente_siguiente | None)]. Un pedazo que ya esta entero
    (compacto) dentro de un reciente tambien es repeticion ("este" de "sereste") y no queda. [] = todo
    el texto era repeticion."""
    pal = _palabras(nuevo)
    toks = [w for w, _, _ in pal]
    recs = [[w for w, _, _ in _palabras(r)] for r in recientes]
    comp = [compacto(r) for r in recientes]
    marca: list = [None] * len(toks)          # indice del reciente que repite esa palabra
    while True:
        mejor = (0, 0, None)
        libres = [k for k in range(len(toks)) if marca[k] is None]
        # tramos contiguos de palabras no marcadas
        tramos, ini = [], None
        for k in range(len(toks) + 1):
            if k < len(toks) and marca[k] is None:
                ini = k if ini is None else ini
            elif ini is not None:
                tramos.append((ini, k))
                ini = None
        for a0, a1 in tramos:
            for i, r in enumerate(recs):
                largo, fin = _tirada_mas_larga(toks[a0:a1], r)
                if largo > mejor[0]:
                    mejor = (largo, a0 + fin, i)
        if mejor[0] < min_palabras or not libres:
            break
        largo, fin, i = mejor
        for k in range(fin - largo, fin):
            marca[k] = i
    if all(m is None for m in marca):
        return None
    pedazos, k = [], 0
    while k < len(toks):
        if marca[k] is not None:
            k += 1
            continue
        j = k
        while j < len(toks) and marca[j] is None:
            j += 1
        t = nuevo[pal[k][1]:pal[j - 1][2]].strip(_PUNTA)
        antes = marca[k - 1] if k > 0 else None
        despues = marca[j] if j < len(toks) else None
        if compacto(t) and not any(compacto(t) in rc for rc in comp):
            # cola de puntuacion del original (". ", "?") hasta la proxima palabra
            fin_txt = pal[j][1] if j < len(toks) else len(nuevo)
            t = nuevo[pal[k][1]:fin_txt].strip(" \t\n")
            pedazos.append((t, antes, despues))
        k = j
    return pedazos
