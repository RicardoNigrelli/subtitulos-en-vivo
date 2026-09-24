"""Estaticos de la vista (web/) y del panel (panel/) servidos por el MISMO hub (B4).

Para que el despliegue sea de dos servicios (hub + worker, C4): la audiencia abre
http://<host>:<puerto>/ y la vista le habla al hub en el mismo origen (mismo host y puerto).

Rutas (solo si se configuro la carpeta: HUB_WEB_DIR / HUB_PANEL_DIR o --web / --panel):
  web    GET /  -> index.html · GET /s y /s/<lo-que-sea> -> sesion.html (la sesion y el idioma los
         resuelve el cliente, igual que web/servir.py) · GET /<archivo> -> <web>/<archivo>
  panel  GET /panel -> 302 a /panel/ (conserva la query) · GET /panel/ -> index.html ·
         GET /panel/<archivo> -> <panel>/<archivo>
/api/*, /ws/*, /ingest y /health quedan como estaban: se registran ANTES que el comodin de web.

Que se sirve: solo archivos que estan DENTRO de la carpeta (sin '..', sin segmentos que empiecen
con '.', sin ':' ni '\\') y con una extension de TIPOS (no se sirven .py ni dotfiles).
El Content-Type sale de TIPOS y no de mimetypes: en Windows mimetypes lee el registro y puede
devolver text/plain para .js.
Cache: 'Cache-Control: no-cache' + ETag (el navegador revalida en cada carga; 304 si no cambio).
El proxy /panel-api/metricas de panel/servir.py NO esta aca: /api/metricas sigue pidiendo Bearer
y el hub no la expone sin token por otra ruta.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

log = logging.getLogger("hub")

TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".vtt": "text/vtt; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}
CACHE = "no-cache"
REQUERIDOS = {"web": ("index.html", "sesion.html"), "panel": ("index.html",)}


def _no_encontrado(rel: str) -> web.Response:
    return web.Response(text=json.dumps({"error": f"no existe: {rel}"}, ensure_ascii=False), status=404,
                        content_type="application/json", charset="utf-8")


class Carpeta:
    """Una carpeta de estaticos. Cachea el contenido por (mtime_ns, tamano): si el archivo cambia en
    disco, la proxima peticion lo relee (sin reiniciar el hub)."""

    def __init__(self, raiz: str | Path):
        self.raiz = Path(raiz).resolve()
        self._cache: dict[Path, tuple[tuple[int, int], bytes, str]] = {}

    def resolver(self, rel: str) -> Path | None:
        """Ruta absoluta del archivo pedido, o None si no se sirve (fuera de la carpeta, dotfile,
        extension no permitida, no existe)."""
        if not rel or len(rel) > 256 or any(c in rel for c in ("\\", ":", "\x00")):
            return None
        partes = rel.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") for p in partes):
            return None
        if Path(partes[-1]).suffix.lower() not in TIPOS:
            return None
        try:
            p = self.raiz.joinpath(*partes).resolve()
        except (OSError, ValueError):
            return None
        if self.raiz not in p.parents or not p.is_file():
            return None
        return p

    def respuesta(self, request: web.Request, rel: str) -> web.Response:
        p = self.resolver(rel)
        if p is None:
            return _no_encontrado(rel)
        try:
            st = p.stat()
            clave = (st.st_mtime_ns, st.st_size)
            c = self._cache.get(p)
            if c is None or c[0] != clave:
                c = (clave, p.read_bytes(), f'"{st.st_mtime_ns:x}-{st.st_size:x}"')
                self._cache[p] = c
        except OSError:
            return _no_encontrado(rel)
        _, cuerpo, etag = c
        cab = {"Cache-Control": CACHE, "ETag": etag}
        inm = request.headers.get("If-None-Match")
        if inm and (inm.strip() == "*" or etag in [x.strip().removeprefix("W/") for x in inm.split(",")]):
            return web.Response(status=304, headers=cab)
        cab["Content-Type"] = TIPOS[Path(rel).suffix.lower()]
        return web.Response(body=cuerpo, headers=cab)


def validar(web_dir: str | None, panel_dir: str | None) -> list[str]:
    """Errores de configuracion (carpeta inexistente o sin sus html). Lista vacia = ok."""
    errs = []
    for nombre, d in (("web", web_dir), ("panel", panel_dir)):
        if not d:
            continue
        p = Path(d)
        if not p.is_dir():
            errs.append(f"{nombre}: {d!r} no es una carpeta (cwd={Path.cwd()})")
            continue
        errs += [f"{nombre}: falta {r} en {p.resolve()}" for r in REQUERIDOS[nombre] if not (p / r).is_file()]
    return errs


def montar(app: web.Application, web_dir: str | None, panel_dir: str | None) -> dict[str, str]:
    """Registra las rutas de estaticos. Se llama DESPUES de registrar la API: el comodin de web
    ('/{archivo}') va ultimo. Devuelve {"web": carpeta, "panel": carpeta} con lo montado."""
    montado: dict[str, str] = {}
    if panel_dir:
        panel = Carpeta(panel_dir)

        async def panel_sin_barra(request: web.Request) -> web.Response:
            q = request.query_string
            raise web.HTTPFound("/panel/" + (f"?{q}" if q else ""))  # rutas relativas de panel/index.html

        async def panel_indice(request: web.Request) -> web.Response:
            return panel.respuesta(request, "index.html")

        async def panel_archivo(request: web.Request) -> web.Response:
            return panel.respuesta(request, request.match_info["archivo"])

        app.router.add_get("/panel", panel_sin_barra)
        app.router.add_get("/panel/", panel_indice)
        app.router.add_get("/panel/{archivo:.+}", panel_archivo)
        montado["panel"] = str(panel.raiz)
    if web_dir:
        vista = Carpeta(web_dir)

        async def indice(request: web.Request) -> web.Response:
            return vista.respuesta(request, "index.html")

        async def sesion(request: web.Request) -> web.Response:
            return vista.respuesta(request, "sesion.html")

        async def archivo(request: web.Request) -> web.Response:
            return vista.respuesta(request, request.match_info["archivo"])

        app.router.add_get("/", indice)
        app.router.add_get("/s", sesion)
        app.router.add_get("/s/{resto:.*}", sesion)
        app.router.add_get("/{archivo:.+}", archivo)
        montado["web"] = str(vista.raiz)
    return montado
