"""Configuracion del hub: entorno > .env (solo claves HUB_*) > defaults.

HUB_HOST (127.0.0.1; en Docker 0.0.0.0) · HUB_PORT (8100) · HUB_TOKEN (dev-token si falta, con aviso) ·
HUB_HISTORY (1000 mensajes por sesion en memoria) · HUB_QUEUE_MAX (100 por cliente) ·
HUB_HEARTBEAT_S (1.0) · HUB_LIVE_S (30) · HUB_SEND_TIMEOUT_S (5) ·
HUB_PENDIENTE_S (120: vida de un item de traduccion que llego antes que su text).
Topes (seguridad, 25/09): HUB_MAX_MSG_BYTES (65536) · HUB_MAX_SESIONES (200 salas conocidas) ·
HUB_MAX_ESPERA (50 salas "en espera" abiertas por slugs sin ingesta) · HUB_MAX_AUDIENCIA (2000 WS).
Token (A1): `dev-token` o menos de 16 caracteres -> el hub NO arranca si escucha fuera de loopback
(ver `revisar_token`). Generar uno: python -c "import secrets;print(secrets.token_urlsafe(24))"
B4 (aditivo): HUB_WEB_DIR / HUB_PANEL_DIR (o `python -m hub --web <dir> --panel <dir>`): el hub sirve
ademas los estaticos de la vista (/, /s/<id>) y del panel (/panel/). Sin ellos, solo la API.
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
    max_msg_bytes: int = 64 * 1024  # M2: el mensaje real mas grande de los casetes pesa 1818 B
    max_sesiones: int = 200        # M2: salas conocidas (con ingesta) en memoria; la 201 se rechaza
    max_espera: int = 50           # M3: salas "en espera" (solo espectadores, slug sin ingesta)
    max_audiencia: int = 2000      # M3: WS de audiencia simultaneos en todo el hub (no por IP: NAT)
    pendiente_s: float = 120.0     # B2: items de traduccion esperando su text
    pendientes_max: int = 2000     # B2: tope de seq pendientes por sesion (memoria)
    web_dir: str | None = None     # B4: carpeta de la vista (web/) servida en / y /s/<id>
    panel_dir: str | None = None   # B4: carpeta del panel (panel/) servida en /panel/


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
                              ("HUB_PENDIENTE_S", "pendiente_s", float),
                              ("HUB_MAX_MSG_BYTES", "max_msg_bytes", int),
                              ("HUB_MAX_SESIONES", "max_sesiones", int), ("HUB_MAX_ESPERA", "max_espera", int),
                              ("HUB_MAX_AUDIENCIA", "max_audiencia", int),
                              ("HUB_WEB_DIR", "web_dir", str), ("HUB_PANEL_DIR", "panel_dir", str)):
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


HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_MIN = 16


def revisar_token(c: Config) -> tuple[str | None, str | None]:
    """A1: (error_fatal, aviso). Token debil = `dev-token` o menos de TOKEN_MIN caracteres.
    Debil + host fuera de loopback -> error (el hub no arranca, exit 2). `dev-token` -> aviso SIEMPRE,
    venga del entorno, de .env o del default. Nunca incluye el valor del token."""
    debil = c.token == TOKEN_DEV or len(c.token) < TOKEN_MIN
    local = c.host.strip().lower() in HOSTS_LOCALES
    como = 'python -c "import secrets;print(secrets.token_urlsafe(24))"'
    if debil and not local:
        motivo = "es el token publico de desarrollo" if c.token == TOKEN_DEV else             f"tiene {len(c.token)} caracteres (minimo {TOKEN_MIN})"
        return (f"HUB_TOKEN (origen: {c.token_origen}) {motivo} y el hub escucharia en {c.host}, fuera de "
                f"127.0.0.1/localhost: cualquiera en la red podria publicar subtitulos. No se levanta. "
                f"Generar uno con: {como}  y ponerlo en HUB_TOKEN (entorno o .env)."), None
    if c.token == TOKEN_DEV:
        return None, (f"HUB_TOKEN es el token publico de desarrollo '{TOKEN_DEV}' (origen: {c.token_origen}). "
                      f"Solo sirve en 127.0.0.1; no usar asi en un evento. Generar uno con: {como}")
    if debil:
        return None, (f"HUB_TOKEN tiene {len(c.token)} caracteres (minimo {TOKEN_MIN} fuera de localhost). "
                      f"Generar uno con: {como}")
    return None, None
