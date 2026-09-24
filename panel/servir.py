"""Servidor estatico de desarrollo para panel/ (monitor, Bloque 3, R8e) + proxy autenticado de
GET /api/metricas del hub.

Sirve panel/index.html, app.js, estilo.css tal cual (mismo patron que web/servir.py) y ademas expone:

    GET /panel-api/metricas?hub=<host:puerto>

que reenvia a GET http://<host:puerto>/api/metricas agregando "Authorization: Bearer <HUB_TOKEN>"
del lado del SERVIDOR: el navegador nunca ve el token (la vista de audiencia es publica, pero
/api/metricas exige Bearer segun contracts/README.md y hub/README.md). El HUB_TOKEN se resuelve
con la MISMA logica que usa el hub de verdad (entorno > .env > 'dev-token'), importando
hub.config.cargar_config: es una LECTURA de hub/ (permitido; sólo esta prohibido escribirlo), y
evita reimplementar por separado la resolucion del token y que un dia se desalinee.

Uso:
    .venv/Scripts/python panel/servir.py --puerto 8102

Antes de levantar, verificar que el puerto este libre (en Windows el bind NO falla si el puerto ya
esta tomado; el trafico se lo lleva el primer server):
    netstat -ano | findstr :8102
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
RAIZ = Path(PANEL_DIR).resolve().parent
sys.path.insert(0, str(RAIZ))  # para "import hub.config" sin depender del cwd

from hub.config import TOKEN_DEV, cargar_config  # noqa: E402

_CFG = cargar_config()
if _CFG.token_origen == "default":
    print(f"[servir.py] AVISO: HUB_TOKEN no esta definido (ni entorno ni .env): /panel-api/metricas "
          f"usara el token de desarrollo '{TOKEN_DEV}' (el mismo default que usa el hub si tampoco "
          f"lo tiene). No usar asi en un evento real.")
else:
    print(f"[servir.py] HUB_TOKEN leido de {_CFG.token_origen} ({len(_CFG.token)} caracteres; no se imprime)")


class Handler(http.server.SimpleHTTPRequestHandler):
    """Sirve panel/ y responde /panel-api/metricas con un proxy autenticado hacia el hub."""

    def do_GET(self):
        partido = urllib.parse.urlsplit(self.path)
        if partido.path == "/panel-api/metricas":
            return self._proxy_metricas(partido.query)
        return super().do_GET()

    def _proxy_metricas(self, query: str) -> None:
        qs = urllib.parse.parse_qs(query)
        hub_host = (qs.get("hub") or [None])[0] or f"localhost:{_CFG.port}"
        url = f"http://{hub_host}/api/metricas"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_CFG.token}"})
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                cuerpo, estado = resp.read(), resp.status
        except urllib.error.HTTPError as e:
            cuerpo, estado = e.read(), e.code
        except Exception as e:  # hub caido, DNS, timeout, etc: no tirar el panel entero por esto
            cuerpo = json.dumps({"error": f"no se pudo consultar {url}: {e}"}).encode("utf-8")
            estado = 502
        self.send_response(estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()  # agrega Cache-Control: no-store (ver end_headers mas abajo)
        self.wfile.write(cuerpo)

    def end_headers(self):
        # Dev: que el navegador no cachee HTML/JS/CSS/JSON mientras iteramos.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[servir.py] " + (fmt % args))


def main():
    parser = argparse.ArgumentParser(description="Servidor estatico de dev para panel/")
    parser.add_argument("--puerto", type=int, default=8102, help="Puerto de escucha (default 8102)")
    args = parser.parse_args()

    handler = functools.partial(Handler, directory=PANEL_DIR)
    servidor = http.server.ThreadingHTTPServer(("0.0.0.0", args.puerto), handler)
    print(f"[servir.py] sirviendo {PANEL_DIR} en http://0.0.0.0:{args.puerto} (Ctrl+C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    sys.exit(main())
