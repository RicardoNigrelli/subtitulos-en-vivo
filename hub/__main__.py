"""python -m hub  -> levanta el hub en HUB_HOST:HUB_PORT (localhost:8100 por defecto).

En Windows el bind NO falla si el puerto ya esta tomado (el trafico se lo lleva el primero): por eso,
antes de bindear, se intenta conectar al puerto; si alguien contesta, se aborta con exit 2.
"""
from __future__ import annotations

import logging
import socket
import sys

from aiohttp import web

from .app import crear_app
from .config import TOKEN_DEV, cargar_config

log = logging.getLogger("hub")


def puerto_ocupado(host: str, port: int) -> str | None:
    destinos = {"127.0.0.1", "::1"} if host in ("localhost", "0.0.0.0", "::", "") else {host}
    for h in sorted(destinos):
        try:
            with socket.create_connection((h, port), timeout=0.5):
                return h
        except OSError:
            continue
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    cfg = cargar_config()
    if cfg.token_origen == "default":
        log.warning("HUB_TOKEN no esta definido (ni en el entorno ni en .env): se usa el token de "
                    "desarrollo '%s'. No usar asi en un evento.", TOKEN_DEV)
    else:
        log.info("HUB_TOKEN leido de %s (%d caracteres; no se imprime)", cfg.token_origen, len(cfg.token))
    ocupado = puerto_ocupado(cfg.host, cfg.port)
    if ocupado:
        log.error("el puerto %d ya contesta en %s: otro proceso lo tiene. No se levanta el hub "
                  "(en Windows el bind no falla y el trafico se lo llevaria el otro).", cfg.port, ocupado)
        return 2
    log.info("hub escuchando en http://%s:%d  (ws: /ingest, /ws/<sesion>?lang=xx · http: /health, "
             "/api/sesiones) historial=%d cola=%d", cfg.host, cfg.port, cfg.history, cfg.queue_max)
    web.run_app(crear_app(cfg), host=cfg.host, port=cfg.port, print=None,
                access_log_format='%a "%r" %s %Tfs')
    return 0


if __name__ == "__main__":
    sys.exit(main())
