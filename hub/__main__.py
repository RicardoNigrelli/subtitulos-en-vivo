"""python -m hub  -> levanta el hub en HUB_HOST:HUB_PORT (127.0.0.1:8100 por defecto; en Docker HUB_HOST=0.0.0.0).

    python -m hub [--web web] [--panel panel]   (B4; o HUB_WEB_DIR / HUB_PANEL_DIR)
con --web sirve la vista en / y /s/<id>; con --panel, el panel en /panel/ (hub/estaticos.py).
Una carpeta inexistente o sin sus html aborta con exit 2.

En Windows el bind NO falla si el puerto ya esta tomado (el trafico se lo lleva el primero): por eso,
antes de bindear, se intenta conectar al puerto; si alguien contesta, se aborta con exit 2.
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys

from aiohttp import web

from . import estaticos
from .app import crear_app
from .config import TOKEN_DEV, cargar_config

log = logging.getLogger("hub")


def puerto_ocupado(host: str, port: int) -> str | None:
    # 127.0.0.1 tambien mira ::1: un cliente que use "localhost" puede resolver primero a ::1
    destinos = {"127.0.0.1", "::1"} if host in ("localhost", "127.0.0.1", "0.0.0.0", "::", "") else {host}
    for h in sorted(destinos):
        try:
            with socket.create_connection((h, port), timeout=0.5):
                return h
        except OSError:
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hub", description="Hub WebSocket (fan-out por sesion+idioma).")
    ap.add_argument("--web", metavar="DIR", help="carpeta de la vista (web/): / y /s/<id>. Default: HUB_WEB_DIR")
    ap.add_argument("--panel", metavar="DIR", help="carpeta del panel (panel/): /panel/. Default: HUB_PANEL_DIR")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    forzar = {k: v for k, v in (("web_dir", args.web), ("panel_dir", args.panel)) if v}
    cfg = cargar_config(**forzar)
    errs = estaticos.validar(cfg.web_dir, cfg.panel_dir)
    if errs:
        for e in errs:
            log.error("estaticos: %s", e)
        return 2
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
    if cfg.web_dir:
        log.info("sirve la vista: / y /s/<id> desde %s", cfg.web_dir)
    if cfg.panel_dir:
        log.info("sirve el panel: /panel/ desde %s", cfg.panel_dir)
    web.run_app(crear_app(cfg), host=cfg.host, port=cfg.port, print=None,
                access_log_format='%a "%r" %s %Tfs')
    return 0


if __name__ == "__main__":
    sys.exit(main())
