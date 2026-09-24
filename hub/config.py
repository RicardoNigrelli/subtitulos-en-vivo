"""Configuracion del hub: entorno > .env (solo claves HUB_*) > defaults.

HUB_HOST (127.0.0.1; en Docker 0.0.0.0) · HUB_PORT (8100) · HUB_TOKEN (dev-token si falta, con aviso) ·
HUB_HISTORY (1000 mensajes por sesion en memoria) · HUB_QUEUE_MAX (100 por cliente) ·
HUB_HEARTBEAT_S (1.0) · HUB_LIVE_S (30) · HUB_SEND_TIMEOUT_S (5) ·
HUB_PENDIENTE_S (120: vida de un item de traduccion que llego antes que su text).
El .env se lee con python-dotenv (solo claves HUB_*); el entorno tiene prioridad.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TOKEN_DEV = "dev-token"


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8100
    token: str = TOKEN_DEV
    token_origen: str = "default"  # entorno | .env | default | explicito
    history: int = 1000
    init_lines: int = 10
    queue_max: int = 100
    heartbeat_s: float = 1.0
    live_s: float = 30.0
    send_timeout_s: float = 5.0
    auth_timeout_s: float = 5.0
    max_msg_bytes: int = 1 << 20
    pendiente_s: float = 120.0     # B2: items de traduccion esperando su text
    pendientes_max: int = 2000     # B2: tope de seq pendientes por sesion (memoria)


def _dotenv() -> dict[str, str]:
    """Solo las claves HUB_* de <raiz>/.env (el resto, p. ej. GEMINI_API_KEY, ni se guarda)."""
    p = RAIZ / ".env"
    if not p.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:  # python-dotenv es opcional
        return {}
    return {k: v for k, v in dotenv_values(p).items() if k.startswith("HUB_") and v}


def cargar_config(**forzar) -> Config:
    archivo = _dotenv()

    def get(clave: str) -> tuple[str | None, str | None]:
        v = os.environ.get(clave)
        if v:
            return v, "entorno"
        v = archivo.get(clave)
        if v:
            return v, ".env"
        return None, None

    c = Config()
    for clave, attr, conv in (("HUB_HOST", "host", str), ("HUB_PORT", "port", int),
                              ("HUB_HISTORY", "history", int), ("HUB_QUEUE_MAX", "queue_max", int),
                              ("HUB_HEARTBEAT_S", "heartbeat_s", float), ("HUB_LIVE_S", "live_s", float),
                              ("HUB_SEND_TIMEOUT_S", "send_timeout_s", float),
                              ("HUB_PENDIENTE_S", "pendiente_s", float)):
        v, _ = get(clave)
        if v is not None:
            setattr(c, attr, conv(v))
    tok, origen = get("HUB_TOKEN")
    if tok:
        c.token, c.token_origen = tok, origen
    for k, v in forzar.items():
        setattr(c, k, v)
        if k == "token":
            c.token_origen = "explicito"
    return c
