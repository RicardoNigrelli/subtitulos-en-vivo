"""Servidor estatico de desarrollo para web/ (frontend, Bloque 1).

Sirve los archivos de esta carpeta y reescribe cualquier ruta /s/<lo-que-sea>
a sesion.html: la sesion y el idioma se resuelven en el cliente (app.js) leyendo
location.pathname / location.search, no en el servidor.

Uso:
    .venv/Scripts/python web/servir.py --puerto 8101

Antes de levantar, verificar que el puerto este libre (en Windows el bind NO
falla si el puerto ya esta tomado; el trafico se lo lleva el primer server):
    netstat -ano | findstr :8101
"""

import argparse
import functools
import http.server
import os
import urllib.parse

WEB_DIR = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    """Sirve web/ y reescribe /s/<lo-que-sea> a sesion.html (ruteo del lado cliente)."""

    def do_GET(self):
        ruta = urllib.parse.urlsplit(self.path).path
        if ruta == "/s" or ruta.startswith("/s/"):
            self.path = "/sesion.html"
        return super().do_GET()

    def end_headers(self):
        # Dev: que el navegador no cachee HTML/JS/CSS mientras iteramos.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[servir.py] " + (fmt % args))


def main():
    parser = argparse.ArgumentParser(description="Servidor estatico de dev para web/")
    parser.add_argument("--puerto", type=int, default=8101, help="Puerto de escucha (default 8101)")
    args = parser.parse_args()

    handler = functools.partial(Handler, directory=WEB_DIR)
    servidor = http.server.ThreadingHTTPServer(("0.0.0.0", args.puerto), handler)
    print(f"[servir.py] sirviendo {WEB_DIR} en http://0.0.0.0:{args.puerto} (Ctrl+C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
