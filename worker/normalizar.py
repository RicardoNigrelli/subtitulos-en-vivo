"""Normalizacion del texto de ASR antes de emitir `text`/`partial` y antes de traducir (25/09).

Motivo: casete `fixtures/casetes/muestra-en-20260925-094954.jsonl` (09:50, servicio degradado). El
server FUSIONO varios turnos en un solo `inputTranscription` y pego los pedazos sin espacio
("obvious.1940s", "case?you", "extremelyThis") o repitio la palabra de la union ("build build").
En corridas sanas no pasa.

Reglas CONSERVADORAS (sin API, siempre activas):
  1. puntuacion `.?!,;:` seguida de letra/digito sin espacio -> espacio. Excepto: numeros (3.5, 10:30,
     1,000), `.` seguido de minuscula (dominios, archivos, e.g.), siglas (U.S., ASP.NET), `::`, y los
     tokens con pinta de URL/mail/ruta (://, www., @, /, \\, =).
  2. palabra que EMPIEZA en minuscula (>= 2 letras) seguida de una palabra Capitalizada pegada
     ("extremelyThis", "laBueno") -> espacio. PascalCase (YouTube, GitHub, PowerShell...) no calza
     por construccion; lo demas se exceptua por lista (EXCEPCIONES_CAMEL) y por el --vocab/glosario.
  3. palabra repetida (>= 3 letras) en un turno MERGEADO: APAGADA por defecto
     (NORMALIZAR_REPETIDAS=1 la prende). No se puede distinguir de una repeticion real: en el MISMO
     turno mergeado del casete, "This this trade-off" es habla real (8 casetes sanos del mismo clip,
     p.ej. b1-en-60s y b4-trad-vivo-en, tienen "this this") y "build build" es artefacto.
  4. palabras pegadas SIN marca ("astonishinglycontroversial", "saysay", "morein"): no se tocan; no
     hay forma segura. Es comportamiento del servicio degradado (lo intenta la limpieza de abajo).

LIMPIEZA con el modelo de texto (APAGADA por defecto; LIMPIEZA_MERGE=1 la prende. Orquestador 25/09: en pruebas reales vencio el timeout 2/2 y con 15 s borro un 'this' real): SOLO para el texto de un
turno mergeado (cubre > 1 ventana) o con marcas de pegado. La hace worker/session.py con el mismo
cliente, limitador y reservas por key del traductor; aca estan el prompt y la VALIDACION de la salida
(si no pasa, se descarta y sale el texto con las reglas conservadoras).
"""
from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from typing import Iterable, Optional

LETRA = r"[^\W\d_]"
MINUS = "a-záéíóúñüàèìòùç"
MAYUS = "A-ZÁÉÍÓÚÑÜÀÈÌÒÙÇ"

EXCEPCIONES_CAMEL = {
    "YouTube", "GitHub", "JavaScript", "iPhone", "OpenAI", "DevOps", "PowerShell",
    "iPad", "iOS", "macOS", "eBay", "jQuery", "GitLab", "TypeScript", "LinkedIn", "WhatsApp",
    "PostgreSQL", "MySQL", "GraphQL", "FastAPI", "ChatGPT", "DeepMind", "PowerPoint", "MacBook",
    "DevSecOps", "useState", "camelCase", "iCloud", "eSports",
}

_PUNT = re.compile(rf"([.?!,;:])(?=[A-Za-z{MINUS}{MAYUS}¿¡0-9])")
_CAMEL = re.compile(rf"(?<!{LETRA})([{MINUS}]{{2,}})([{MAYUS}][{MINUS}]*)(?!{LETRA})")
_REPE = re.compile(rf"(?<!{LETRA})({LETRA}{{3,}})\s+\1(?!{LETRA})", re.IGNORECASE)
_ESPACIOS = re.compile(r"(\s+)")


def _es_url(tok: str) -> bool:
    t = tok.lower()
    return ("://" in t or t.startswith("www.") or "@" in t or "/" in t or "\\" in t or "=" in t)


def _punt_token(tok: str) -> tuple[str, int]:
    """Regla 1 en un token sin espacios. Devuelve (token, cambios)."""
    n = 0

    def rep(m: re.Match) -> str:
        nonlocal n
        i = m.start()
        p = tok[i - 1] if i > 0 else ""
        q = tok[m.end()]
        c = m.group(1)
        if not p or p == ":" and c == ":":
            return c                       # ".NET" / "std::vector"
        if p.isdigit() and q.isdigit():
            return c                       # 3.5  10:30  1,000
        if c == ".":
            if q.islower():
                return c                   # nerdearla.org  e.g.  archivo.py
            if p.isupper() and q.isupper():
                return c                   # U.S.A  ASP.NET
            j = i - 1
            while j >= 0 and tok[j].isalpha():
                j -= 1
            if i - 1 - j == 1:
                return c                   # sigla de una letra: "U.S.The", "i.e.So"
        n += 1
        return c + " "

    return _PUNT.sub(rep, tok), n


def _excepciones(vocab: Iterable[str]) -> set[str]:
    ex = set(EXCEPCIONES_CAMEL)
    for v in vocab or ():
        for w in re.findall(rf"{LETRA}+", v):
            ex.add(w)
    return ex


def _camel_token(tok: str, ex: set[str]) -> tuple[str, int]:
    n = 0

    def rep(m: re.Match) -> str:
        nonlocal n
        if m.group(0) in ex:
            return m.group(0)
        n += 1
        return m.group(1) + " " + m.group(2)

    return _CAMEL.sub(rep, tok), n


def repetidas_activas() -> bool:
    return os.environ.get("NORMALIZAR_REPETIDAS", "0").strip() == "1"


def limpieza_activa() -> bool:
    return os.environ.get("LIMPIEZA_MERGE", "0").strip() == "1"


def normalizar(texto: str, vocab: Iterable[str] = (), mergeado: bool = False,
               repetidas: Optional[bool] = None) -> tuple[str, dict]:
    """Reglas conservadoras. Devuelve (texto, cambios) con cambios = {"puntuacion": n, "camel": n,
    "repetidas": n}. `repetidas` None => variable NORMALIZAR_REPETIDAS (default apagada); aun
    prendida, la regla 3 solo corre si `mergeado` (turno de > 1 ventana o costura)."""
    if not texto:
        return texto, {"puntuacion": 0, "camel": 0, "repetidas": 0}
    ex = _excepciones(vocab)
    partes = _ESPACIOS.split(texto)
    np_ = nc = 0
    for i in range(0, len(partes), 2):
        tok = partes[i]
        if not tok or _es_url(tok):
            continue
        tok, a = _punt_token(tok)
        np_ += a
        # la regla 1 pudo partir el token: la 2 va sobre cada pedazo
        sub = tok.split(" ")
        out = []
        for s in sub:
            s2, b = _camel_token(s, ex)
            nc += b
            out.append(s2)
        partes[i] = " ".join(out)
    texto = "".join(partes)
    nr = 0
    if mergeado and (repetidas if repetidas is not None else repetidas_activas()):
        texto, nr = _REPE.subn(lambda m: m.group(1), texto)
    return texto, {"puntuacion": np_, "camel": nc, "repetidas": nr}


def marcas_pegado(texto: str, vocab: Iterable[str] = ()) -> bool:
    """El texto trae pegados detectables (reglas 1 o 2)."""
    _t, c = normalizar(texto, vocab, mergeado=False, repetidas=False)
    return bool(c["puntuacion"] or c["camel"])


# ---- limpieza con el modelo de texto ---------------------------------------------------------
PROMPT_LIMPIEZA = ("Fix only spacing, duplicated words at segment joins and sentence punctuation in "
                   "this speech transcript; do not paraphrase, translate, add or remove content; "
                   "return only the text")


def prompt_limpieza(texto: str) -> str:
    return f"{PROMPT_LIMPIEZA}.\n\nTranscript:\n{texto}"


def salida_limpieza(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^`{3}\w*\s*|\s*`{3}$", "", s).strip()
    # corrida real 10:03 (reportes/audio-pipeline-normalizar-real2.json): vino `[ "texto" ]`
    if s[:1] in "[\"":
        try:
            v = json.loads(s)
            if isinstance(v, list) and len(v) == 1:
                v = v[0]
            if isinstance(v, str):
                s = v.strip()
        except ValueError:
            pass
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return re.sub(r"\s+", " ", s)


_STOP = {
    "en": {"the", "and", "of", "to", "is", "that", "it", "we", "you", "was", "in", "this", "with",
           "for", "are", "be", "not", "have"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "es", "en", "un", "una", "por", "con", "para",
           "no", "se", "lo", "del"},
}


def _palabras(t: str) -> list[str]:
    return re.findall(r"[^\W_]+", t.lower())


def _compacto(t: str) -> str:
    return "".join(_palabras(t))


def validar_limpieza(original: str, limpio: str, lang: str) -> tuple[bool, str]:
    """(ok, motivo). Criterios (pedido del orquestador, 25/09): misma lengua, largo 85-105 % del
    original, sin palabras nuevas (cada palabra de la salida esta dentro del original sin espacios:
    permite separar pegadas) y caracteres sin espacios/puntuacion >= 95 % iguales."""
    if not limpio or not limpio.strip():
        return False, "vacio"
    r = len(limpio) / max(1, len(original))
    if not 0.85 <= r <= 1.05:
        return False, f"largo {r:.2f}"
    if lang in _STOP:
        otro = "es" if lang == "en" else "en"
        w = _palabras(limpio)
        propio = sum(1 for x in w if x in _STOP[lang] and x not in _STOP[otro])
        ajeno = sum(1 for x in w if x in _STOP[otro] and x not in _STOP[lang])
        w0 = _palabras(original)
        ajeno0 = sum(1 for x in w0 if x in _STOP[otro] and x not in _STOP[lang])
        if ajeno > max(propio, ajeno0):
            return False, "lengua"
    c0, c1 = _compacto(original), _compacto(limpio)
    for x in _palabras(limpio):
        if x not in c0:
            return False, f"palabra nueva: {x}"
    sim = SequenceMatcher(None, c0, c1, autojunk=False).ratio()
    if sim < 0.95:
        return False, f"caracteres {sim:.3f}"
    return True, "ok"
