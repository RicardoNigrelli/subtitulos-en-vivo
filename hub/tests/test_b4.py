"""B4 (aditivo): el hub sirve web/ y panel/; translations_langs desde session_start; source/test.

Hub REAL en puerto efimero (hub/tests/util.py). Mensajes SINTETICOS y rotulados (no son transcripciones).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from contracts import FUENTES_TEST, errores, errores_frame
from hub import estaticos
from hub.__main__ import main as hub_main
from hub.inyectar import cargar, inyectar
from hub.tests.util import EJEMPLOS, RAIZ, TOKEN, levantar, productor, texto

T0 = 1790262000.0


# ------------------------------------------------------------------------------ helpers
def _inicio(sid: str, source=None, langs=None, title: str = "Sesion de prueba del hub") -> dict:
    meta = {"title": title, "source": source}
    if langs is not None:
        meta["translations_langs"] = langs
    return {"v": 1, "type": "session_start", "session_id": sid, "seq": 1, "lang": "en",
            "t_emit": T0, "replay": True, "meta": meta}


def _traduccion(sid: str, seq: int, lang_to: str = "es", source=None) -> dict:
    meta = {"lang_to": lang_to, "model": "test-hub", "batch_ms": 10}
    if source is not None:
        meta["source"] = source
    return {"v": 1, "type": "translation", "session_id": sid, "seq": None, "lang": "en", "replay": True,
            "t_emit": T0 + 60.0, "text": None, "translations": {}, "meta": meta,
            "items": [{"seq": seq, "text": f"Traduccion de prueba del seq {seq}.", "ok": True}]}


def _texto(sid: str, seq: int, source=None, sin_meta: bool = False) -> dict:
    m = texto(sid, seq)
    m["translations"] = {}
    if sin_meta:
        m["meta"] = {}
    elif source is not None:
        m["meta"] = {"source": source}
    return m


async def _enviar(http, h, msgs: list[dict]) -> None:
    for m in msgs:
        assert errores(m) == [], (m, errores(m))
    ws, _ = await productor(http, h)
    for m in msgs:
        await ws.send_str(json.dumps(m))
    await ws.close()
    await asyncio.sleep(0.15)


async def _sesiones(http, h) -> dict[str, dict]:
    async with http.get(f"{h.http}/api/sesiones") as r:
        assert r.status == 200
        return {s["session_id"]: s for s in await r.json()}


async def _init(http, h, sid: str) -> dict:
    async with http.ws_connect(f"{h.ws}/ws/{sid}?lang=es") as cli:
        init = json.loads((await cli.receive(timeout=5)).data)
    assert init["type"] == "init" and errores_frame(init, "init") == [], init
    return init


def _carpetas(tmp_path: Path) -> tuple[Path, Path]:
    """web/ y panel/ SINTETICAS (contenido rotulado), mas archivos que NO se deben servir."""
    w, p = tmp_path / "w", tmp_path / "p"
    (w / "sub").mkdir(parents=True)
    p.mkdir()
    (w / "index.html").write_text("<!doctype html><title>indice de prueba</title>", encoding="utf-8")
    (w / "sesion.html").write_text("<!doctype html><title>sesion de prueba</title>", encoding="utf-8")
    (w / "app.js").write_text("// js de prueba\n", encoding="utf-8")
    (w / "estilo.css").write_text("/* css de prueba */\n", encoding="utf-8")
    (w / "sub" / "x.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (w / "servir.py").write_text("print('no se sirve')\n", encoding="utf-8")
    (w / ".gitkeep").write_text("", encoding="utf-8")
    (p / "index.html").write_text("<!doctype html><title>panel de prueba</title>", encoding="utf-8")
    (p / "app.js").write_text("// panel js de prueba\n", encoding="utf-8")
    (tmp_path / "fuera.js").write_text("// fuera de las carpetas: no se sirve\n", encoding="utf-8")
    return w, p


async def _crudo(port: int, ruta: str) -> str:
    """GET con la ruta TAL CUAL (sin que el cliente normalice '..' ni decodifique): status line."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {ruta} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    linea = (await asyncio.wait_for(reader.readline(), 5)).decode()
    writer.close()
    return linea


# ------------------------------------------------------------------------------ 1. estaticos
@pytest.mark.asyncio
async def test_estaticos_rutas_tipos_y_cache(tmp_path, http):
    w, p = _carpetas(tmp_path)
    h = await levantar(web_dir=str(w), panel_dir=str(p))
    try:
        casos = [("/", w / "index.html", "text/html"), ("/index.html", w / "index.html", "text/html"),
                 ("/s/cualquiera", w / "sesion.html", "text/html"), ("/s/", w / "sesion.html", "text/html"),
                 ("/s/sala-1?lang=es", w / "sesion.html", "text/html"),
                 ("/app.js", w / "app.js", "text/javascript"), ("/estilo.css", w / "estilo.css", "text/css"),
                 ("/sub/x.svg", w / "sub" / "x.svg", "image/svg+xml"),
                 ("/panel/", p / "index.html", "text/html"), ("/panel/app.js", p / "app.js", "text/javascript")]
        for ruta, archivo, tipo in casos:
            async with http.get(f"{h.http}{ruta}", allow_redirects=False) as r:
                assert r.status == 200, (ruta, r.status)
                assert await r.read() == archivo.read_bytes(), ruta
                assert r.headers["Content-Type"].startswith(tipo), (ruta, r.headers["Content-Type"])
                assert r.headers["Cache-Control"] == "no-cache", ruta
                etag = r.headers["ETag"]
            async with http.get(f"{h.http}{ruta}", headers={"If-None-Match": etag}) as r:
                assert r.status == 304, (ruta, r.status)
        # /panel sin barra -> /panel/ conservando la query (panel/index.html usa rutas relativas)
        async with http.get(f"{h.http}/panel?hub=localhost:8080", allow_redirects=False) as r:
            assert r.status == 302 and r.headers["Location"] == "/panel/?hub=localhost:8080"
        # lo que NO se sirve
        for ruta in ("/servir.py", "/.gitkeep", "/no-existe.js", "/panel/no-existe.css", "/sub/",
                     "/panel/panel-api/metricas", "/api/no-existe"):
            async with http.get(f"{h.http}{ruta}", allow_redirects=False) as r:
                assert r.status == 404, (ruta, r.status)
        for ruta in ("/%2e%2e/fuera.js", "/panel/%2e%2e/fuera.js", "/..%2ffuera.js", "/sub/..%2f..%2ffuera.js",
                     "/../fuera.js", "/panel/../../fuera.js"):
            linea = await _crudo(h.port, ruta)
            assert " 200 " not in linea, (ruta, linea)
        # un archivo modificado en disco se sirve nuevo sin reiniciar el hub
        (w / "app.js").write_text("// js de prueba, version 2 (mas larga)\n", encoding="utf-8")
        async with http.get(f"{h.http}/app.js") as r:
            assert await r.read() == (w / "app.js").read_bytes()
    finally:
        await h.parar()


@pytest.mark.asyncio
async def test_estaticos_no_tapan_la_api(tmp_path, http):
    w, p = _carpetas(tmp_path)
    h = await levantar(web_dir=str(w), panel_dir=str(p))
    try:
        async with http.get(f"{h.http}/health") as r:
            assert r.status == 200 and (await r.json())["ok"] is True
        async with http.get(f"{h.http}/api") as r:
            rutas = (await r.json())["rutas"]
            assert "GET /s/<session_id> (web/sesion.html)" in rutas and "GET /panel/ (panel/index.html)" in rutas
        await _enviar(http, h, [_inicio("sala-est", source="test-hub"), _texto("sala-est", 2)])
        assert (await _sesiones(http, h))["sala-est"]["last_seq"] == 2
        async with http.get(f"{h.http}/api/sesiones/sala-est/historial?desde=0&tipos=todos") as r:
            assert [m["seq"] for m in await r.json()] == [1, 2]
        init = await _init(http, h, "sala-est")
        assert init["last_seq"] == 2 and [m["seq"] for m in init["lines"]] == [2]
        async with http.get(f"{h.http}/api/metricas") as r:
            assert r.status == 401  # sigue pidiendo Bearer
    finally:
        await h.parar()


@pytest.mark.asyncio
async def test_sin_web_la_raiz_sigue_siendo_la_lista_de_rutas(hub, http):
    async with http.get(f"{hub.http}/") as r:
        assert r.status == 200 and r.headers["Content-Type"].startswith("application/json")
        assert "GET /api/sesiones" in (await r.json())["rutas"]
    async with http.get(f"{hub.http}/s/sala") as r:
        assert r.status == 404
    async with http.get(f"{hub.http}/panel/") as r:
        assert r.status == 404


@pytest.mark.asyncio
async def test_estaticos_con_las_carpetas_reales(http):
    h = await levantar(web_dir=str(RAIZ / "web"), panel_dir=str(RAIZ / "panel"))
    try:
        for ruta, archivo in (("/", "web/index.html"), ("/s/cualquiera", "web/sesion.html"),
                              ("/app.js", "web/app.js"), ("/estilo.css", "web/estilo.css"),
                              ("/panel/", "panel/index.html"), ("/panel/app.js", "panel/app.js"),
                              ("/panel/estilo.css", "panel/estilo.css")):
            async with http.get(f"{h.http}{ruta}") as r:
                assert r.status == 200, ruta
                assert await r.read() == (RAIZ / archivo).read_bytes(), ruta
        for ruta in ("/servir.py", "/panel/servir.py", "/panel/recalcular.py"):
            async with http.get(f"{h.http}{ruta}") as r:
                assert r.status == 404, ruta
    finally:
        await h.parar()


def test_validar_y_main_abortan_con_carpeta_mala(tmp_path, monkeypatch):
    w, p = _carpetas(tmp_path)
    assert estaticos.validar(str(w), str(p)) == []
    assert estaticos.validar(None, None) == []
    assert estaticos.validar(str(tmp_path / "nope"), None)
    (w / "sesion.html").unlink()
    errs = estaticos.validar(str(w), None)
    assert len(errs) == 1 and "sesion.html" in errs[0]
    assert estaticos.validar(None, str(tmp_path / "vacia-no-existe"))
    for clave in ("HUB_WEB_DIR", "HUB_PANEL_DIR"):
        monkeypatch.delenv(clave, raising=False)
    assert hub_main(["--web", str(tmp_path / "nope")]) == 2
    monkeypatch.setenv("HUB_PANEL_DIR", str(tmp_path / "nope"))
    assert hub_main([]) == 2  # tambien por variable de entorno


def test_resolver_rechaza_salidas_de_la_carpeta(tmp_path):
    w, _ = _carpetas(tmp_path)
    c = estaticos.Carpeta(w)
    assert c.resolver("app.js") == (w / "app.js").resolve()
    assert c.resolver("sub/x.svg") == (w / "sub" / "x.svg").resolve()
    for rel in ("../fuera.js", "..\\fuera.js", "sub/../../fuera.js", ".gitkeep", "sub/.x.js", "app.js::$DATA",
                "C:/Windows/win.ini", "/fuera.js", "servir.py", "", "sub/", "app.js\x00.png", "sub//x.svg"):
        assert c.resolver(rel) is None, rel


# ------------------------------------------------------------------------------ 2. translations_langs
@pytest.mark.asyncio
async def test_translations_langs_desde_session_start(hub, http):
    sid = "sala-langs"
    await _enviar(http, hub, [_inicio(sid, source="test-hub", langs=["es"])])
    s = (await _sesiones(http, hub))[sid]
    assert s["translations_langs"] == ["es"]  # antes de cualquier traduccion
    assert (await _init(http, hub, sid))["translations_langs"] == ["es"]
    await _enviar(http, hub, [_texto(sid, 2), _traduccion(sid, 2, lang_to="pt-BR")])
    assert (await _sesiones(http, hub))[sid]["translations_langs"] == ["es", "pt-BR"]  # union


@pytest.mark.asyncio
async def test_translations_langs_invalidos_se_ignoran(hub, http):
    # valores raros (incluso no hashables) y, en la MISMA conexion, un text: la ingesta no se cae
    await _enviar(http, hub, [_inicio("sala-langs-2", source="test-hub",
                                      langs=["es", "ESP", 3, "", {"lang": "en"}, ["fr"], None]),
                              _texto("sala-langs-2", 2)])
    s = (await _sesiones(http, hub))["sala-langs-2"]
    assert s["translations_langs"] == ["es"] and s["last_seq"] == 2
    await _enviar(http, hub, [_inicio("sala-langs-5", source="test-hub", langs={"es": True}),
                              _texto("sala-langs-5", 2)])
    s = (await _sesiones(http, hub))["sala-langs-5"]
    assert s["translations_langs"] == [] and s["last_seq"] == 2
    await _enviar(http, hub, [_inicio("sala-langs-3", source="test-hub", langs="es")])
    assert (await _sesiones(http, hub))["sala-langs-3"]["translations_langs"] == ["es"]
    await _enviar(http, hub, [_inicio("sala-langs-4", source="test-hub")])
    assert (await _sesiones(http, hub))["sala-langs-4"]["translations_langs"] == []


# ------------------------------------------------------------------------------ 3. source / test
@pytest.mark.asyncio
async def test_source_y_test(hub, http):
    fuente_worker = {"file": "fixtures/audio/clips/x.wav", "url": None, "start_s": 0, "dur_s": 60}
    await _enviar(http, hub, [
        # transporte de casete: session_start rotulado
        _inicio("t-casete", source="transporte-casete"), _texto("t-casete", 2, source="transporte-casete"),
        # sin session_start: vale el primer meta.source (de un text)
        _texto("t-ejemplo", 5, source="ejemplo-contrato"),
        # session_start con el objeto del worker y los text rotulados: source = objeto, test = True
        _inicio("t-mixta", source=fuente_worker), _texto("t-mixta", 2, source="transporte-casete"),
        # "real": objeto del worker y text sin meta.source -> test False
        _inicio("t-real", source=fuente_worker), _texto("t-real", 2, sin_meta=True),
        # el meta.source de un translation describe la traduccion: no rotula la sesion
        _traduccion("t-trad", 3, source={"casete": "x.jsonl", "offline": True}), _texto("t-trad", 3, source="otro"),
        # session_start con source null no borra el meta.source que ya se vio
        _texto("t-null", 1, source="transporte-casete"),
    ])
    null = _inicio("t-null", source=None)
    null["seq"] = 2
    await _enviar(http, hub, [null])
    s = await _sesiones(http, hub)
    assert (s["t-casete"]["source"], s["t-casete"]["test"]) == ("transporte-casete", True)
    assert (s["t-ejemplo"]["source"], s["t-ejemplo"]["test"]) == ("ejemplo-contrato", True)
    assert (s["t-mixta"]["source"], s["t-mixta"]["test"]) == (fuente_worker, True)
    assert (s["t-real"]["source"], s["t-real"]["test"]) == (fuente_worker, False)
    assert (s["t-trad"]["source"], s["t-trad"]["test"]) == ("otro", False)
    assert (s["t-null"]["source"], s["t-null"]["test"]) == ("transporte-casete", True)
    init = await _init(http, hub, "t-casete")
    assert init["source"] == "transporte-casete" and init["test"] is True
    assert FUENTES_TEST == {"transporte-casete", "ejemplo-contrato"}


@pytest.mark.asyncio
async def test_ejemplos_del_contrato_quedan_como_test(hub, http):
    res = await inyectar(cargar(EJEMPLOS / "sesion-en.jsonl"), hub.ingest, TOKEN, velocidad=0)
    assert res["enviados"] == 20 and not res["rechazados"]
    s = (await _sesiones(http, hub))["ejemplo-en"]
    assert s["test"] is True and s["source"] == "ejemplo-contrato" and s["replay"] is True
    await _enviar(http, hub, [texto("sala-no-test", 1)])  # meta.source "test-hub": no es de FUENTES_TEST
    assert (await _sesiones(http, hub))["sala-no-test"]["test"] is False
