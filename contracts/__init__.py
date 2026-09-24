"""Contrato de mensajes v1 (CONGELADO: solo se agregan campos). Ver contracts/README.md.

API minima para el hub, el worker, el replay y los tests:

    from contracts import errores, es_valido, errores_frame, leer_archivo
    errores(msg)                 -> lista de strings (vacia = valido)
    errores_frame(frame, "init") -> valida frames de protocolo ($defs/frame_<nombre>)
    leer_archivo(path)           -> itera (linea, mensaje | Exception) de .jsonl / .json / casete

No importa `contracts.validate` a proposito: asi `python -m contracts.validate` corre sin
advertencias de runpy.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

VERSION = 1
TIPOS = ("text", "rotation", "watchdog", "error", "heartbeat", "session_start", "session_end")
# Lo que el hub reenvia a la audiencia tal cual (el heartbeat del worker no: el hub manda el suyo).
TIPOS_AUDIENCIA = frozenset(TIPOS) - {"heartbeat"}
ESQUEMA_PATH = Path(__file__).with_name("esquema.json")
EJEMPLOS_DIR = Path(__file__).with_name("ejemplos")

# Tolerancia para t_captured <= t_emit (mismo reloj; solo absorbe redondeos).
TOLERANCIA_RELOJ_S = 0.05


@lru_cache(maxsize=1)
def esquema() -> dict:
    with open(ESQUEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def _validador(defn: str | None = None):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    raiz = esquema()
    if defn is None:
        return Draft202012Validator(raiz, format_checker=Draft202012Validator.FORMAT_CHECKER)
    recurso = Resource.from_contents(raiz, default_specification=DRAFT202012)
    registro = Registry().with_resource(raiz["$id"], recurso)
    return Draft202012Validator({"$ref": f"{raiz['$id']}#/$defs/{defn}"}, registry=registro)


def _ruta(err) -> str:
    partes = [str(p) for p in err.absolute_path]
    return "/".join(partes) if partes else "(raiz)"


def _formatear(errs) -> list[str]:
    salida = []
    for e in sorted(errs, key=lambda e: (list(map(str, e.absolute_path)), e.message)):
        if e.context:  # anyOf/oneOf: mostrar las causas
            causas = "; ".join(sorted({c.message for c in e.context}))
            salida.append(f"{_ruta(e)}: {e.message} [{causas}]")
        else:
            salida.append(f"{_ruta(e)}: {e.message}")
    return salida


def errores(msg: Any) -> list[str]:
    """Errores de un mensaje del contrato: esquema + chequeos que JSON Schema no expresa."""
    if not isinstance(msg, dict):
        return [f"(raiz): se esperaba un objeto JSON, llego {type(msg).__name__}"]
    salida = _formatear(_validador().iter_errors(msg))
    if salida:
        return salida
    if msg.get("type") == "text":
        if msg["audio_end"] < msg["audio_start"]:
            salida.append(f"audio_end: {msg['audio_end']} < audio_start {msg['audio_start']}")
    tc, te = msg.get("t_captured"), msg.get("t_emit")
    if isinstance(tc, (int, float)) and isinstance(te, (int, float)) and tc > te + TOLERANCIA_RELOJ_S:
        salida.append(f"t_captured: {tc} es posterior a t_emit {te} (la captura precede a la emision)")
    return salida


def es_valido(msg: Any) -> bool:
    return not errores(msg)


def errores_frame(frame: Any, nombre: str) -> list[str]:
    """Valida un frame de protocolo contra $defs/frame_<nombre> (init, auth, auth_ok, rechazado, heartbeat_hub)."""
    return _formatear(_validador(f"frame_{nombre}").iter_errors(frame))


def leer_archivo(path: str | Path) -> Iterator[tuple[int, Any]]:
    """Itera (numero_de_linea, mensaje) de un archivo.

    - .jsonl: un mensaje por linea (lineas vacias se saltean). Si la linea es de CASETE
      (`{"t":..,"dir":..,"payload":{..}}`), solo se toman las de dir=emit y se devuelve el payload;
      la cabecera del casete (`{"casete":1,...}`) y las lineas dir=server/client se saltean.
    - .json: un objeto (un mensaje) o una lista de mensajes.
    Si una linea no es JSON valido, en lugar del mensaje se devuelve la excepcion.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            try:
                datos = json.load(f)
            except ValueError as e:
                yield 1, e
                return
            if isinstance(datos, list):
                for i, m in enumerate(datos, 1):
                    yield i, m
            else:
                yield 1, datos
            return
        for n, linea in enumerate(f, 1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                obj = json.loads(linea)
            except ValueError as e:
                yield n, e
                continue
            if es_linea_de_casete(obj):
                if obj.get("dir") == "emit":
                    yield n, obj.get("payload")
                continue
            yield n, obj


def es_linea_de_casete(obj: Any) -> bool:
    """Linea de casete del worker: cabecera {"casete":1,...} o evento {"t","dir","payload"}."""
    return (isinstance(obj, dict) and "type" not in obj
            and ("casete" in obj or ("dir" in obj and "payload" in obj)))
