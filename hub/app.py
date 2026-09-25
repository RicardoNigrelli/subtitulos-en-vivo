"""App aiohttp del hub: /ingest (productores, token en el primer frame), /ws/<session_id> (audiencia,
publica), /api/sesiones, /api/sesiones/<id>/historial, /api/metricas (Bearer), /health.
B4 (aditivo): GET /api (lista de rutas) y, si se configuraron, los estaticos de web/ (/, /s/<id>) y
panel/ (/panel/) -> hub/estaticos.py. Sin --web, GET / sigue devolviendo la lista de rutas.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import time

from aiohttp import WSMsgType, web

from . import estaticos
from .config import Config
from .nucleo import Hub, dumps

log = logging.getLogger("hub")

HUB_KEY = web.AppKey("hub", Hub)
LATIDOS_KEY = web.AppKey("latidos", asyncio.Task)
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LANG = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
CIERRE_AUTH = 4401
CIERRE_LLENO = 1013   # M3: "try again later" (tope de audiencia o de salas en espera)
MAX_ERROR = 300       # B1: cada error/valor repetido en `rechazado` y en el log se corta aca
HISTORIAL_LIMIT = 500      # M2/escala: default de ?limit= en GET .../historial
HISTORIAL_LIMIT_MAX = 1000


def _corto(v, n: int = MAX_ERROR):
    """B1: valor apto para repetir en un rechazo o en el log: escalares cortos tal cual, strings
    truncados, contenedores solo por tipo (un repr de algo muy anidado tambien puede recursar)."""
    if v is None or isinstance(v, (bool, int, float)):
        return v if not isinstance(v, int) or abs(v) < 10 ** 18 else f"<int de {len(str(v))} digitos>"
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f" [cortado: +{len(v) - n} caracteres]"
    return f"<{type(v).__name__}>"


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=dumps(data), status=status, content_type="application/json", charset="utf-8")


def _error(status: int, mensaje: str) -> web.Response:
    return _json({"error": mensaje}, status)


def _token_ok(dado: object, config: Config) -> bool:
    return isinstance(dado, str) and hmac.compare_digest(dado.encode(), config.token.encode())


def _bearer_ok(request: web.Request, config: Config) -> bool:
    tipo, _, valor = request.headers.get("Authorization", "").partition(" ")
    return tipo.lower() == "bearer" and _token_ok(valor.strip(), config)


@web.middleware
async def cors(request: web.Request, handler):
    """CORS abierto para lectura: la web corre en otro origen (8101). No toca los WebSocket."""
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            e.headers["Access-Control-Allow-Origin"] = "*"
            raise
    if isinstance(resp, web.WebSocketResponse) or resp.prepared:
        return resp
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Max-Age"] = "600"
    return resp


# ---------------------------------------------------------------------------- ingesta
async def ws_ingest(request: web.Request) -> web.WebSocketResponse:
    hub = request.app[HUB_KEY]
    cfg = hub.config
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=cfg.max_msg_bytes)
    await ws.prepare(request)
    peer = request.remote
    try:
        primero = await ws.receive(timeout=cfg.auth_timeout_s)
    except asyncio.TimeoutError:
        primero = None
    frame = None
    if primero is not None and primero.type == WSMsgType.TEXT:
        try:
            frame = json.loads(primero.data)
        except (ValueError, RecursionError):  # M4: muy anidado -> RecursionError, no traceback
            frame = None
    if not (isinstance(frame, dict) and frame.get("type") == "auth" and _token_ok(frame.get("token"), cfg)):
        hub.auth_fallidas += 1
        log.warning("ingesta: auth invalida o ausente desde %s; se cierra con %d", peer, CIERRE_AUTH)
        await ws.close(code=CIERRE_AUTH, message=b"auth invalida")
        # aiohttp 3.14 re-arma el heartbeat al leer la respuesta al close que inicia el server: cada
        # intento fallido quedaba vivo ~30 s (~75 KB). Se cancela a mano (API privada, con getattr).
        getattr(ws, "_cancel_heartbeat", lambda: None)()
        return ws

    hub.productores.add(ws)
    log.info("ingesta: productor conectado desde %s (productores=%d)", peer, len(hub.productores))
    try:
        await ws.send_str(dumps({"type": "auth_ok", "v": 1, "last_seq": hub.last_seq(), "t_hub": time.time()}))
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    obj = json.loads(msg.data)
                except (ValueError, RecursionError) as e:  # M4
                    await _rechazar(ws, None, [f"JSON invalido: {type(e).__name__}: {e}"])
                    continue
                try:
                    estado, errs = hub.ingerir(obj)
                except RecursionError:  # M4: valido como JSON pero demasiado anidado para validarlo
                    estado, errs = "rechazado", ["mensaje demasiado anidado"]
                if estado == "rechazado":
                    errs = [_corto(e) for e in errs]  # B1
                    log.warning("ingesta: mensaje rechazado (%s seq=%s): %s",
                                _corto(obj.get("session_id")) if isinstance(obj, dict) else "?",
                                _corto(obj.get("seq")) if isinstance(obj, dict) else "?", "; ".join(errs[:3]))
                    await _rechazar(ws, obj, errs)
            elif msg.type == WSMsgType.BINARY:
                await _rechazar(ws, None, ["frame binario: se esperaba un mensaje JSON en texto"])
            elif msg.type == WSMsgType.ERROR:
                log.warning("ingesta: error de conexion desde %s: %s", peer, ws.exception())
                break
    finally:
        hub.productores.discard(ws)
        log.info("ingesta: productor desconectado desde %s (productores=%d)", peer, len(hub.productores))
    return ws


async def _rechazar(ws: web.WebSocketResponse, obj, errs: list[str]) -> None:
    frame = {"type": "rechazado",
             "session_id": _corto(obj.get("session_id")) if isinstance(obj, dict) else None,
             "seq": _corto(obj.get("seq")) if isinstance(obj, dict) else None,
             "errores": [_corto(e) for e in errs]}
    try:
        await asyncio.wait_for(ws.send_str(dumps(frame)), timeout=2.0)
    except Exception:
        pass  # el productor puede no estar leyendo: el rechazo igual queda en el log y en las metricas


# ---------------------------------------------------------------------------- audiencia
async def ws_audiencia(request: web.Request) -> web.StreamResponse:
    hub = request.app[HUB_KEY]
    sid = request.match_info["session_id"]
    lang = request.query.get("lang") or None
    if not SLUG.fullmatch(sid):
        return _error(400, "session_id invalido: se espera un slug ^[a-z0-9][a-z0-9_-]{0,63}$")
    if lang is not None and not LANG.fullmatch(lang):
        return _error(400, "lang invalido: se espera un codigo como es, en o pt-BR")
    lleno = hub.admitir(sid)  # M3: tope global de audiencia y de salas en espera (NO por IP: NAT)
    if lleno:
        # SIN heartbeat: con heartbeat, aiohttp deja vivo cada WS rechazado (timer de ping/pong) y un
        # flood de rechazos crecia ~75 KB por intento (medido: 1951 rechazos -> +150 MB).
        ws = web.WebSocketResponse(max_msg_size=1024)
        await ws.prepare(request)
        log.warning("audiencia: %s rechazado con %d: %s", sid, CIERRE_LLENO, lleno)
        await ws.close(code=CIERRE_LLENO, message=lleno.encode()[:120])
        return ws
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=64 * 1024)
    await ws.prepare(request)
    c = hub.suscribir(ws, sid, lang, request.transport)
    c.tarea = asyncio.create_task(hub.enviador(c))
    try:
        async for _msg in ws:
            pass  # la audiencia no manda nada que el hub necesite
    finally:
        if not c.cerrado:
            hub.desconectar(c, "cliente_cerro")
    return ws


# ---------------------------------------------------------------------------- http
async def health(request: web.Request) -> web.Response:
    hub = request.app[HUB_KEY]
    ahora = time.time()
    return _json({"ok": True, "v": 1, "t_hub": ahora, "uptime_s": round(ahora - hub.t_inicio, 1),
                  "sesiones": len(hub.conocidas()), "productores": len(hub.productores),
                  "clientes": sum(len(s.clientes) for s in hub.sesiones.values())})


async def api_sesiones(request: web.Request) -> web.Response:
    hub = request.app[HUB_KEY]
    ahora = time.time()
    return _json([s.resumen(ahora, hub.config.live_s) for s in hub.conocidas()])


async def api_historial(request: web.Request) -> web.Response:
    hub = request.app[HUB_KEY]
    sid = request.match_info["session_id"]
    s = hub.sesiones.get(sid)
    if s is None or not s.conocida:
        return _error(404, f"sesion desconocida: {sid}")
    try:
        desde = int(request.query.get("desde", "-1"))
    except ValueError:
        return _error(400, "desde debe ser un entero (seq)")
    try:
        limit = int(request.query.get("limit", str(HISTORIAL_LIMIT)))
    except ValueError:
        return _error(400, "limit debe ser un entero")
    if not 1 <= limit <= HISTORIAL_LIMIT_MAX:
        return _error(400, f"limit fuera de rango: de 1 a {HISTORIAL_LIMIT_MAX} (default {HISTORIAL_LIMIT})")
    todos = request.query.get("tipos") == "todos"
    msgs = sorted((m for m in s.historial if m["seq"] > desde and (todos or m["type"] == "text")),
                  key=lambda m: m["seq"])
    # los `limit` MAS VIEJOS despues de `desde`: se pagina con desde=<ultimo seq recibido>
    resp = _json(msgs[:limit])
    if len(msgs) > limit:
        resp.headers["X-Historial-Truncado"] = str(len(msgs) - limit)  # cuantos quedaron afuera
    return resp


async def api_metricas(request: web.Request) -> web.Response:
    hub = request.app[HUB_KEY]
    if not _bearer_ok(request, hub.config):
        resp = _error(401, "falta Authorization: Bearer <HUB_TOKEN>")
        resp.headers["WWW-Authenticate"] = "Bearer"
        return resp
    return _json(hub.metricas())


async def raiz(request: web.Request) -> web.Response:
    cfg = request.app[HUB_KEY].config
    rutas = ["GET /health", "GET /api", "GET /api/sesiones",
             "GET /api/sesiones/<id>/historial?desde=<seq>[&tipos=todos][&limit=<1-1000, default 500>]",
             "GET /api/metricas (Bearer)", "WS /ws/<session_id>?lang=<xx>", "WS /ingest (auth en el primer frame)"]
    if cfg.web_dir:
        rutas += ["GET / (web/index.html)", "GET /s/<session_id> (web/sesion.html)", "GET /<archivo> (web/)"]
    if cfg.panel_dir:
        rutas += ["GET /panel/ (panel/index.html)", "GET /panel/<archivo> (panel/)"]
    return _json({"hub": "vibeathon", "v": 1, "rutas": rutas})


# ---------------------------------------------------------------------------- app
def _silenciar_resets(loop: asyncio.AbstractEventLoop, contexto: dict) -> None:
    """En Windows (ProactorEventLoop) cada espectador que corta de golpe (un celular que sale de la
    sala) deja un traceback de ConnectionResetError en _call_connection_lost. Es inocuo: se baja a
    DEBUG para que el log del evento no se llene de ruido. El resto va al handler por defecto."""
    if isinstance(contexto.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
        log.debug("conexion cortada por el cliente: %s", contexto.get("message"))
        return
    loop.default_exception_handler(contexto)


async def _cabeceras(request: web.Request, resp: web.StreamResponse) -> None:
    """B2/B5: en TODA respuesta (API, estaticos, errores, upgrade de WS). `Server` sin version (aiohttp
    hace setdefault despues de esta senal). X-Frame-Options solo en /panel/ (tiene el campo del token);
    la vista queda enmarcable a proposito (OBS / embebidos)."""
    resp.headers["Server"] = "vibeathon-hub"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.path == "/panel" or request.path.startswith("/panel/"):
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"


async def _al_arrancar(app: web.Application) -> None:
    asyncio.get_running_loop().set_exception_handler(_silenciar_resets)
    app[LATIDOS_KEY] = asyncio.create_task(app[HUB_KEY].latidos())


async def _al_apagar(app: web.Application) -> None:
    await app[HUB_KEY].apagar()


async def _al_limpiar(app: web.Application) -> None:
    t = app.get(LATIDOS_KEY)
    if t is not None:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)


def crear_app(config: Config) -> web.Application:
    app = web.Application(middlewares=[cors])
    app[HUB_KEY] = Hub(config)
    if not config.web_dir:
        app.router.add_get("/", raiz)  # con --web, "/" es web/index.html (lo registra estaticos.montar)
    app.router.add_get("/health", health)
    app.router.add_get("/ingest", ws_ingest)
    app.router.add_get("/ws/{session_id}", ws_audiencia)
    app.router.add_get("/api", raiz)
    app.router.add_get("/api/sesiones", api_sesiones)
    app.router.add_get("/api/sesiones/{session_id}/historial", api_historial)
    app.router.add_get("/api/metricas", api_metricas)
    # B4: estaticos DESPUES de la API (el comodin de web va ultimo y no tapa /api, /ws, /ingest, /health)
    estaticos.montar(app, config.web_dir, config.panel_dir)
    app.on_response_prepare.append(_cabeceras)
    app.on_startup.append(_al_arrancar)
    app.on_shutdown.append(_al_apagar)
    app.on_cleanup.append(_al_limpiar)
    return app
