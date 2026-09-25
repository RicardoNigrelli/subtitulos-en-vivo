"""Servidor estatico de desarrollo para panel/ (monitor, Bloque 10, R8e).

Sirve panel/index.html, app.js, estilo.css, tokens.css tal cual (mismo patron que web/servir.py).
NO hace de proxy hacia el hub: `panel/app.js` pide GET /api/metricas DIRECTO al hub (mismo origen o
`?hub=` saneado) con `Authorization: Bearer <token que tipeo el operador>` (sessionStorage), igual
que cuando el hub sirve el panel el mismo (`--panel`). Antes (B3-B9) este archivo exponia

    GET /panel-api/metricas?hub=<host:puerto>

que reenviaba a http://<host:puerto>/api/metricas agregando el HUB_TOKEN REAL del servidor sin
validar `hub`: cualquiera que llegara a esa ruta con un `?hub=atacante.example:PUERTO` hacia que
ESTE proceso filtrara el token real a un host arbitrario (SSRF + fuga de credencial). Hallazgo ALTO
#1 de `reportes/seguridad.md`; se ELIMINA el proxy en vez de sanearlo (instruccion del orquestador,
bloque 10): un archivo estatico no tiene por que tener un secreto server-side que reenviar. Efecto
en el operador: con el panel servido por ESTE script tambien hay que pegar el token en el campo de
la cabecera (antes se resolvia solo, del lado del servidor, desde .env) — mismo flujo que cuando el
hub sirve el panel directo, ya verificado (ver reportes/monitor-b8.md).

Uso:
    .venv/Scripts/python panel/servir.py --puerto 8102

Bind por defecto 127.0.0.1 (Hallazgo MEDIO #4 de reportes/seguridad.md: antes bindeaba 0.0.0.0 fijo,
alcanzable desde toda la LAN). Para compartir a proposito en la LAN durante un evento:
    .venv/Scripts/python panel/servir.py --puerto 8102 --host 0.0.0.0

Antes de levantar, verificar que el puerto este libre (en Windows el bind NO falla si el puerto ya
esta tomado; el trafico se lo lleva el primer server):
    netstat -ano | findstr :8102
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
from pathlib import Path

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
RAIZ = Path(PANEL_DIR).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    """Sirve panel/ tal cual. Sin rutas especiales: /api/metricas lo pide app.js directo al hub."""

    def end_headers(self):
        # Dev: que el navegador no cachee HTML/JS/CSS mientras iteramos.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[servir.py] " + (fmt % args))


def main():
    parser = argparse.ArgumentParser(description="Servidor estatico de dev para panel/")
    parser.add_argument("--puerto", type=int, default=8102, help="Puerto de escucha (default 8102)")
    parser.add_argument("--host", default="127.0.0.1",
                         help="Interfaz de bind (default 127.0.0.1; usar 0.0.0.0 para exponer en LAN a proposito)")
    args = parser.parse_args()

    handler = functools.partial(Handler, directory=PANEL_DIR)
    servidor = http.server.ThreadingHTTPServer((args.host, args.puerto), handler)
    print(f"[servir.py] sirviendo {PANEL_DIR} en http://{args.host}:{args.puerto} (Ctrl+C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    sys.exit(main())
