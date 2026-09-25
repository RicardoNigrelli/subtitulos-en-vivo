"""Higiene de datos sensibles del worker (adversario final, seguridad A2 / M5 / B7).

- `sin_credenciales(url)`: una URL de stream (rtmp://usuario:CLAVE@host/live/STREAMKEY,
  srt://host:9000?passphrase=X, HLS firmada) NO puede llegar al session_start (el hub lo publica sin
  auth), al casete (se versiona), al log ni a reportes/cuota-audio.log. Se quita userinfo, query y
  fragment. La ruta queda (en RTMP la stream key suele ir en la ruta: se reemplaza el ultimo tramo por
  "***" cuando el esquema es de ingesta en vivo: rtmp/rtmps/srt/rtsp). Excepcion: el parametro `v` de
  una URL de YouTube (id publico del video citado con --url) se conserva.
- `validar_sesion(sid)`: slug del hub `^[a-z0-9][a-z0-9_-]{0,63}$` (el mismo que usa el hub y
  worker/importar.py); evita path traversal en el nombre del casete y gastar cuota contra un hub que
  rechazaria cada mensaje.
- `entorno_subproceso()`: copia de os.environ SIN credenciales para ffmpeg / yt-dlp (no necesitan
  ninguna): se quitan GEMINI_API_KEY*, HUB_TOKEN, CARTESIA_API_KEY y cualquier variable cuyo nombre
  contenga KEY, TOKEN, SECRET o PASSWORD.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SENSIBLES = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD", re.IGNORECASE)
_EXPLICITAS = ("HUB_TOKEN", "CARTESIA_API_KEY")
_VIVO = ("rtmp", "rtmps", "srt", "rtsp", "rtsps")
_YOUTUBE = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")


def sin_credenciales(url):
    """URL sin userinfo, query ni fragment (y sin el ultimo tramo de ruta en rtmp/srt/rtsp).
    None / "" / algo que no es URL con esquema: se devuelve tal cual (un archivo local no tiene
    credenciales)."""
    if not url or not isinstance(url, str):
        return url
    try:
        p = urlsplit(url)
    except ValueError:
        return "<url invalida>"
    if not p.scheme or len(p.scheme) == 1 or not p.netloc:    # "C:\x.wav", "clip.wav", "audio=Mic"
        return url
    host = p.hostname or ""
    try:
        puerto = p.port
    except ValueError:
        puerto = None
    netloc = (f"[{host}]" if ":" in host else host) + (f":{puerto}" if puerto else "")
    ruta = p.path
    esquema = p.scheme.lower()
    if esquema in _VIVO and ruta.strip("/"):
        partes = ruta.rstrip("/").split("/")
        if len(partes) > 1:
            partes[-1] = "***"
        ruta = "/".join(partes)
    query = ""
    if host.lower() in _YOUTUBE and esquema in ("http", "https"):
        query = urlencode([(k, v) for k, v in parse_qsl(p.query) if k == "v"])
    return urlunsplit((p.scheme, netloc, ruta, query, ""))


def validar_sesion(sid: str) -> bool:
    return bool(SLUG.match(sid or ""))


def es_sensible(nombre: str) -> bool:
    n = nombre.upper()
    return n.startswith("GEMINI_API_KEY") or n in _EXPLICITAS or bool(_SENSIBLES.search(n))


def entorno_subproceso(base=None) -> dict:
    env = dict(os.environ if base is None else base)
    return {k: v for k, v in env.items() if not es_sensible(k)}
