"""Arreglos de seguridad/robustez del 25/09 (reportes/adversario-final-seguridad.md: A1, M2, M3, M4,
B1, B2, B5; reportes/adversario-final-escala.md sec. 2: historial sin limite)."""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import pytest

from hub.__main__ import main as hub_main
from hub.config import Config, cargar_config, revisar_token
from hub.tests.util import RAIZ, levantar, productor, recibir, texto

pytestmark = pytest.mark.asyncio

TOKEN_BUENO = "x" * 32
ANIDADO = "[" * 20000 + "]" * 20000  # 40 KB (< 64 KiB): json.loads -> RecursionError. El de 400 KB del
# adversario ahora ni llega a json.loads: lo corta max_msg_bytes.


# ------------------------------------------------------------------ A1: token debil fuera de localhost
@pytest.mark.parametrize("token,host,fatal,aviso", [
    ("dev-token", "0.0.0.0", True, False),
    ("corto-15-chars!", "0.0.0.0", True, False),   # 15 caracteres
    ("dev-token", "192.168.1.10", True, False),
    (TOKEN_BUENO, "0.0.0.0", False, False),
    ("dev-token", "127.0.0.1", False, True),        # arranca, pero avisa
    ("dev-token", "localhost", False, True),
    ("corto", "::1", False, True),
    (TOKEN_BUENO, "127.0.0.1", False, False),
])
async def test_a1_revisar_token(token, host, fatal, aviso):
    f, a = revisar_token(Config(host=host, token=token, token_origen=".env"))
    assert bool(f) is fatal and bool(a) is aviso, (f, a)
    for texto_ in (f, a):
        if texto_:
            assert "secrets.token_urlsafe(24)" in texto_
            if token != "dev-token":
                assert token not in texto_  # nunca imprime el valor


async def test_a1_devtoken_avisa_venga_de_donde_venga(monkeypatch):
    """Antes solo avisaba con origen 'default'. Ahora con entorno (y .env) tambien."""
    monkeypatch.setenv("HUB_TOKEN", "dev-token")
    monkeypatch.setenv("HUB_HOST", "127.0.0.1")
    c = cargar_config()
    assert c.token_origen == "entorno"
    f, a = revisar_token(c)
    assert f is None and a and "dev-token" in a and "entorno" in a


async def test_a1_main_no_arranca_con_devtoken_en_0000(monkeypatch, caplog):
    monkeypatch.setenv("HUB_TOKEN", "dev-token")
    monkeypatch.setenv("HUB_HOST", "0.0.0.0")
    monkeypatch.setenv("HUB_PORT", "8199")  # no llega a bindear: sale antes
    monkeypatch.delenv("HUB_WEB_DIR", raising=False)
    monkeypatch.delenv("HUB_PANEL_DIR", raising=False)
    with caplog.at_level(logging.ERROR, logger="hub"):
        assert hub_main([]) == 2
    assert any("No se levanta" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ M4 + B1
async def test_m4_primer_frame_anidado_da_4401_sin_traceback(hub, http, caplog):
    antes = hub.nucleo.auth_fallidas
    with caplog.at_level(logging.WARNING):
        ws = await http.ws_connect(hub.ingest, max_msg_size=0)
        await ws.send_str(ANIDADO)
        r = await ws.receive(timeout=5)
        assert r.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
        assert ws.close_code == 4401
        await ws.close()
    assert hub.nucleo.auth_fallidas == antes + 1
    assert not any(r.exc_info for r in caplog.records), "hubo traceback"


async def test_m4_mensaje_anidado_se_rechaza_y_la_conexion_sigue(hub, http):
    ws, _ = await productor(http, hub)
    await ws.send_str(ANIDADO)
    r = json.loads((await ws.receive(timeout=5)).data)
    assert r["type"] == "rechazado" and "RecursionError" in r["errores"][0]
    await ws.send_str(json.dumps(texto("sala-m4", 1)))  # la conexion sigue viva
    await asyncio.sleep(0.1)
    assert hub.nucleo.sesiones["sala-m4"].last_seq == 1
    await ws.close()


async def test_b1_rechazo_y_log_truncados(hub, http, caplog):
    ws, _ = await productor(http, hub)
    m = texto("sala-b1", 1)
    m["seq"] = "9" * 30000  # string largo: el esquema lo repite en el mensaje de error
    with caplog.at_level(logging.WARNING, logger="hub"):
        await ws.send_str(json.dumps(m))
        r = await ws.receive(timeout=5)
    assert len(r.data) < 3000, len(r.data)  # antes: ~3x el valor (90 KB)
    f = json.loads(r.data)
    assert f["type"] == "rechazado" and len(f["seq"]) < 400 and all(len(e) < 400 for e in f["errores"])
    lineas = [x.getMessage() for x in caplog.records if "rechazado" in x.getMessage()]
    assert lineas and max(map(len, lineas)) < 1500
    await ws.close()


# ------------------------------------------------------------------ M2: tamano, campos extra, tope de salas
async def test_m2_max_msg_bytes_64k():
    assert Config().max_msg_bytes == 64 * 1024


async def test_m2_mensaje_de_100k_corta_solo_esa_conexion(hub, http):
    ws, _ = await productor(http, hub)
    m = texto("sala-grande", 1)
    m["meta"]["relleno"] = "x" * 100_000
    await ws.send_str(json.dumps(m))
    r = await ws.receive(timeout=5)
    assert r.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR)
    assert "sala-grande" not in hub.nucleo.sesiones
    async with http.get(f"{hub.http}/health") as h:
        assert h.status == 200


async def test_m2_campos_extra_no_se_guardan_ni_se_reparten(hub, http):
    async with http.ws_connect(f"{hub.ws}/ws/sala-extra?lang=es") as cli:
        await recibir(cli, 1)  # init
        ws, _ = await productor(http, hub)
        m = texto("sala-extra", 1)
        m["basura"] = "x" * 20000
        await ws.send_str(json.dumps(m))
        vivo = await recibir(cli, 1)
        await ws.close()
    assert vivo and vivo[0]["seq"] == 1 and "basura" not in vivo[0]
    guardado = hub.nucleo.sesiones["sala-extra"].historial[-1]
    assert "basura" not in guardado and guardado["meta"] == {"source": "test-hub"}
    async with http.get(f"{hub.http}/api/sesiones/sala-extra/historial?tipos=todos") as r:
        assert all("basura" not in x for x in await r.json())


async def test_m2_tope_de_sesiones():
    h = await levantar(max_sesiones=2)
    try:
        n = h.nucleo
        assert n.ingerir(texto("sala-1", 1))[0] == "ok"
        assert n.ingerir(texto("sala-2", 1))[0] == "ok"
        estado, errs = n.ingerir(texto("sala-3", 1))
        assert estado == "rechazado" and "tope de sesiones" in errs[0]
        assert "sala-3" not in n.sesiones
        assert n.ingerir(texto("sala-1", 2))[0] == "ok"  # las que ya existen siguen
    finally:
        await h.parar()


# ------------------------------------------------------------------ historial con limit
async def test_historial_limit(hub, http):
    for i in range(1, 601):
        assert hub.nucleo.ingerir(texto("sala-h", i))[0] == "ok"
    base = f"{hub.http}/api/sesiones/sala-h/historial"
    async with http.get(base) as r:
        d = await r.json()
        assert len(d) == 500 and d[0]["seq"] == 1 and d[-1]["seq"] == 500
        assert r.headers["X-Historial-Truncado"] == "100"
    async with http.get(base + "?desde=500") as r:  # paginar con desde = ultimo recibido
        d = await r.json()
        assert [x["seq"] for x in d] == list(range(501, 601)) and "X-Historial-Truncado" not in r.headers
    async with http.get(base + "?limit=5&desde=10") as r:
        assert [x["seq"] for x in await r.json()] == [11, 12, 13, 14, 15]
    async with http.get(base + "?limit=1000") as r:
        assert len(await r.json()) == 600
    for malo in ("0", "1001", "abc", "-3"):
        async with http.get(base + f"?limit={malo}") as r:
            assert r.status == 400, malo


# ------------------------------------------------------------------ M3: topes de audiencia y de salas en espera
async def test_m3_tope_de_audiencia_1013():
    h = await levantar(max_audiencia=3)
    try:
        async with aiohttp.ClientSession() as http:
            abiertas = [await http.ws_connect(f"{h.ws}/ws/sala-a?lang=es") for _ in range(3)]
            for w in abiertas:
                assert (await recibir(w, 1))[0]["type"] == "init"
            extra = await http.ws_connect(f"{h.ws}/ws/sala-a?lang=es")
            r = await extra.receive(timeout=5)
            assert r.type == aiohttp.WSMsgType.CLOSE and extra.close_code == 1013
            assert "HUB_MAX_AUDIENCIA" in (r.extra or "")
            await abiertas[0].close()
            await asyncio.sleep(0.1)
            otra = await http.ws_connect(f"{h.ws}/ws/sala-a?lang=es")  # libero un lugar: entra
            assert (await recibir(otra, 1))[0]["type"] == "init"
            for w in abiertas[1:] + [otra, extra]:
                await w.close()
    finally:
        await h.parar()


async def test_m3_tope_de_salas_en_espera_1013():
    h = await levantar(max_espera=2)
    try:
        h.nucleo.ingerir(texto("sala-real", 1))  # una conocida no cuenta como "en espera"
        async with aiohttp.ClientSession() as http:
            a = await http.ws_connect(f"{h.ws}/ws/slug-a")
            b = await http.ws_connect(f"{h.ws}/ws/slug-b")
            await recibir(a, 1), await recibir(b, 1)
            c = await http.ws_connect(f"{h.ws}/ws/slug-c")
            r = await c.receive(timeout=5)
            assert r.type == aiohttp.WSMsgType.CLOSE and c.close_code == 1013
            assert "HUB_MAX_ESPERA" in (r.extra or "")
            a2 = await http.ws_connect(f"{h.ws}/ws/slug-a")      # sala en espera que ya existe: entra
            real = await http.ws_connect(f"{h.ws}/ws/sala-real")  # sala conocida: entra
            assert (await recibir(a2, 1))[0]["type"] == "init"
            assert (await recibir(real, 1))[0]["type"] == "init"
            assert len(h.nucleo.sesiones) == 3
            for w in (a, b, c, a2, real):
                await w.close()
    finally:
        await h.parar()


# ------------------------------------------------------------------ B2 + B5: cabeceras
async def test_b2_cabeceras_de_seguridad():
    h = await levantar(panel_dir=str(RAIZ / "panel"), web_dir=str(RAIZ / "web"))
    try:
        async with aiohttp.ClientSession() as http:
            for ruta, marco in (("/health", False), ("/api/sesiones/<script>/historial", False),
                                ("/", False), ("/panel/", True)):
                async with http.get(h.http + ruta, allow_redirects=False) as r:
                    assert r.headers.get("X-Content-Type-Options") == "nosniff", ruta
                    assert r.headers.get("Referrer-Policy") == "no-referrer", ruta
                    assert r.headers.get("Server") == "vibeathon-hub", (ruta, r.headers.get("Server"))
                    assert ("X-Frame-Options" in r.headers) is marco, ruta
                    if marco:
                        assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
            async with http.get(h.http + "/api/sesiones") as r:  # CORS * sigue en la lectura
                assert r.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        await h.parar()


async def test_m3_rechazos_no_quedan_vivos_en_memoria():
    """aiohttp re-arma el heartbeat al cerrar desde el server: sin el arreglo, cada WS rechazado (1013)
    o con auth fallida (4401) quedaba vivo ~30 s (medido: 300 de 300 a 1 s)."""
    import gc
    h = await levantar(max_espera=0)
    try:
        async with aiohttp.ClientSession() as http:
            for i in range(30):
                c = await http.ws_connect(f"{h.ws}/ws/nadie-{i}")
                await c.receive(timeout=5)
                await c.close()
                c = await http.ws_connect(h.ingest)
                await c.send_str(json.dumps({"type": "auth", "token": "malo"}))
                await c.receive(timeout=5)
                await c.close()
            await asyncio.sleep(0.5)
        gc.collect()
        vivos = sum(type(o).__name__ == "WebSocketResponse" for o in gc.get_objects())
        assert vivos == 0, vivos
    finally:
        await h.parar()
