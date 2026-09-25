"""Tests de ops/control.py (pytest, SIN API de Gemini: todas las salas usan
`--transporte casete:...` y `traducir_a=none`; ninguna llama a Gemini, ver skill cuota-gemini).

Un hub REAL (misma app que `python -m hub`, `hub.tests.util`-style) en un puerto efímero de
127.0.0.1 hace de destino; el servicio de control lanza `worker.run` de verdad como subproceso.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from hub.app import crear_app as crear_app_hub
from hub.config import Config as HubConfig

import ops.control as control

pytestmark = pytest.mark.asyncio

RAIZ = Path(__file__).resolve().parents[2]
CASETE = str(RAIZ / "fixtures" / "casetes" / "evidencia-25-09" / "simple-en-053454.jsonl")
ARCHIVO = "fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav"
TOKEN = "tok-control-test-1234567890abcdef"


def _auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _sala_body(sid: str = "sala-t1", **extra) -> dict:
    body = {
        "id": sid, "titulo": "Sala de test", "lang": "en", "traducir_a": "none",
        "fuente": {"tipo": "archivo", "valor": ARCHIVO}, "key": "GEMINI_API_KEY",
        "duracion_s": 6, "arrancar": True,
    }
    body.update(extra)
    return body


# --------------------------------------------------------------------------------- fixtures
@pytest_asyncio.fixture
async def hub(monkeypatch):
    """Hub real en un puerto efímero; HUB_TOKEN en el entorno para que control.py y worker.run
    (subproceso, hereda el entorno) usen el MISMO token."""
    monkeypatch.setenv("HUB_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "no-se-usa-con-transporte-casete")
    cfg = HubConfig(host="127.0.0.1", port=0, token=TOKEN, token_origen="explicito", heartbeat_s=0.5)
    app = crear_app_hub(cfg)
    runner = web.AppRunner(app, shutdown_timeout=2.0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield f"ws://127.0.0.1:{port}/ingest", f"http://127.0.0.1:{port}"
    await asyncio.wait_for(runner.cleanup(), timeout=15)


async def _levantar_control(hub_ws: str, persist: Path, *, transporte: str | None = None):
    app = control.crear_app(hub=hub_ws, transporte=transporte or f"casete:{CASETE}",
                             persist_path=persist)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return app, runner, f"http://127.0.0.1:{port}"


@pytest_asyncio.fixture
async def control_srv(hub, tmp_path):
    hub_ws, hub_http = hub
    persist = tmp_path / "salas.control.json"
    app, runner, base = await _levantar_control(hub_ws, persist)
    yield base, app, persist, hub_http
    ctrl: control.Control = app[control.CTRL_KEY]
    loop = asyncio.get_event_loop()
    for sp in ctrl.listar():
        await loop.run_in_executor(None, sp.detener)
    await asyncio.wait_for(runner.cleanup(), timeout=15)


@pytest_asyncio.fixture
async def http():
    async with aiohttp.ClientSession() as s:
        yield s


# --------------------------------------------------------------------------------- auth
async def test_salud_sin_auth(control_srv, http):
    base, *_ = control_srv
    async with http.get(f"{base}/api/control/salud") as r:
        assert r.status == 200
        data = await r.json()
    assert data == {"ok": True, "salas": 0}


async def test_401_sin_token(control_srv, http):
    base, *_ = control_srv
    for ruta in ("/api/control/fuentes", "/api/control/salas"):
        async with http.get(f"{base}{ruta}") as r:
            assert r.status == 401, ruta
            data = await r.json()
            assert "error" in data


async def test_401_token_incorrecto(control_srv, http):
    base, *_ = control_srv
    async with http.get(f"{base}/api/control/salas", headers=_auth("token-que-no-es")) as r:
        assert r.status == 401


# --------------------------------------------------------------------------------- fuentes
async def test_fuentes(control_srv, http):
    base, *_ = control_srv
    async with http.get(f"{base}/api/control/fuentes", headers=_auth()) as r:
        assert r.status == 200
        data = await r.json()
    assert ARCHIVO in data["archivos"]
    assert "GEMINI_API_KEY" in data["keys"]
    assert isinstance(data["microfonos"], list)
    codigos = {i["codigo"]: i for i in data["idiomas"]}
    assert set(codigos) == {"en", "es", "pt", "fr", "de", "it"}
    assert codigos["en"]["probado"] is True and codigos["es"]["probado"] is True
    assert codigos["pt"]["probado"] is False
    assert codigos["fr"]["nombre_es"] and codigos["fr"]["nombre_en"]


@pytest.mark.parametrize("lang,traducir_a", [
    ("en", "es,pt,fr"),
    ("en", "none"),
    ("es", "en"),
])
async def test_traducir_a_multilenguaje_valido(control_srv, http, lang, traducir_a):
    base, *_ = control_srv
    sid = f"multi-{lang}-{traducir_a.replace(',', '-')}"
    body = _sala_body(sid, arrancar=False, lang=lang, traducir_a=traducir_a,
                       fuente={"tipo": "url", "valor": "udp://127.0.0.1:9500"})
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 201, await r.text()
        creada = await r.json()
    assert creada["traducir_a"] == traducir_a


# --------------------------------------------------------------------------------- validaciones
@pytest.mark.parametrize("cambios,motivo", [
    ({"id": "ID-Mayuscula"}, "id invalido"),
    ({"lang": "xx"}, "lang invalido (no esta en LANGS_VALIDOS)"),
    ({"fuente": {"tipo": "otracosa", "valor": "x"}}, "tipo de fuente invalido"),
    ({"fuente": {"tipo": "archivo", "valor": "fixtures/audio/full/otra-cosa.wav"}}, "archivo fuera de la lista"),
    ({"fuente": {"tipo": "archivo", "valor": "../../etc/passwd"}}, "archivo con traversal"),
    ({"fuente": {"tipo": "url", "valor": "ftp://127.0.0.1/x"}}, "esquema de url invalido"),
    ({"fuente": {"tipo": "url", "valor": "udp://127.0.0.1 :9"}}, "url con espacio"),
    ({"fuente": {"tipo": "mic", "valor": "dispositivo-que-no-existe"}}, "microfono desconocido"),
    ({"key": "OTRA_VARIABLE"}, "key desconocida"),
    ({"duracion_s": "seis"}, "duracion_s no numerico"),
    ({"lang": "en", "traducir_a": "en"}, "traducir_a igual a lang"),
    ({"lang": "en", "traducir_a": "xx"}, "traducir_a con codigo fuera de LANGS_VALIDOS"),
    ({"lang": "en", "traducir_a": "es,es"}, "traducir_a con codigos repetidos"),
    ({"lang": "en", "traducir_a": "es,"}, "traducir_a con elemento vacio"),
    ({"lang": "en", "traducir_a": ""}, "traducir_a vacio"),
    ({"lang": "en", "traducir_a": "todos"}, "traducir_a con palabra suelta invalida"),
])
async def test_validaciones_400(control_srv, http, cambios, motivo):
    base, *_ = control_srv
    body = _sala_body("sala-invalida", arrancar=False, **cambios)
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 400, motivo
        data = await r.json()
        assert "error" in data


async def test_409_id_duplicado(control_srv, http):
    base, *_ = control_srv
    body = _sala_body("sala-dup", arrancar=False)
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 201
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 409


async def test_409_tope_20_salas(control_srv, http):
    base, *_ = control_srv
    for i in range(20):
        body = _sala_body(f"tope-{i}", arrancar=False,
                           fuente={"tipo": "url", "valor": f"udp://127.0.0.1:{9000 + i}"})
        async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
            assert r.status == 201, f"sala {i}: {await r.text()}"
    body = _sala_body("tope-20", arrancar=False, fuente={"tipo": "url", "valor": "udp://127.0.0.1:9099"})
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 409


# --------------------------------------------------------------------------------- ciclo real (subproceso)
async def test_crear_arrancar_aparece_en_hub_y_detener_termina_proceso(control_srv, http):
    base, app, persist, hub_http = control_srv
    body = _sala_body("sala-real-1")
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 201
        creada = await r.json()
    assert creada["estado"] in ("arrancando", "corriendo")
    assert creada["pid"] is not None or creada["estado"] == "arrancando"

    # aparece en /api/sesiones del hub (worker.run real conectado por WS)
    encontrada = None
    for _ in range(60):
        async with http.get(f"{hub_http}/api/sesiones") as r:
            sesiones = await r.json()
        encontrada = next((s for s in sesiones if s["session_id"] == "sala-real-1"), None)
        if encontrada is not None:
            break
        await asyncio.sleep(0.5)
    assert encontrada is not None, "la sala no aparecio en /api/sesiones del hub en 30s"
    assert encontrada["replay"] is True  # transporte de casete: rotulado replay

    async with http.get(f"{base}/api/control/salas", headers=_auth()) as r:
        listado = await r.json()
    sala_listada = next(s for s in listado["salas"] if s["id"] == "sala-real-1")
    assert sala_listada["pid"] is not None
    assert sala_listada["estado"] == "corriendo"

    # detener: SIGTERM/CTRL_BREAK, espera <=10s, despues kill; el handler espera a que termine
    async with http.post(f"{base}/api/control/salas/sala-real-1/detener", headers=_auth()) as r:
        assert r.status == 200
        detenida = await r.json()
    assert detenida["estado"] == "detenida"
    assert detenida["pid"] is None

    ctrl: control.Control = app[control.CTRL_KEY]
    sp = ctrl.obtener("sala-real-1")
    assert sp._proc is None


async def test_delete_204_y_desaparece(control_srv, http):
    base, *_ = control_srv
    body = _sala_body("sala-delete", arrancar=False)
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 201
    async with http.delete(f"{base}/api/control/salas/sala-delete", headers=_auth()) as r:
        assert r.status == 204
    async with http.get(f"{base}/api/control/salas", headers=_auth()) as r:
        listado = await r.json()
    assert all(s["id"] != "sala-delete" for s in listado["salas"])
    async with http.delete(f"{base}/api/control/salas/sala-delete", headers=_auth()) as r:
        assert r.status == 404


# --------------------------------------------------------------------------------- persistencia
async def test_persistencia_tras_reiniciar_el_servicio(hub, tmp_path):
    hub_ws, _ = hub
    persist = tmp_path / "salas.control.json"
    app1, runner1, base1 = await _levantar_control(hub_ws, persist)
    async with aiohttp.ClientSession() as http:
        body = _sala_body("sala-persistente", arrancar=False)
        async with http.post(f"{base1}/api/control/salas", headers=_auth(), json=body) as r:
            assert r.status == 201
    await runner1.cleanup()

    assert persist.exists()
    datos = json.loads(persist.read_text(encoding="utf-8"))
    assert any(d["id"] == "sala-persistente" for d in datos)

    # "reinicio del servicio": una app nueva contra el MISMO archivo de persistencia
    app2, runner2, base2 = await _levantar_control(hub_ws, persist)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base2}/api/control/salas", headers=_auth()) as r:
                assert r.status == 200
                listado = await r.json()
        recordada = next(s for s in listado["salas"] if s["id"] == "sala-persistente")
        assert recordada["estado"] == "detenida"
        assert recordada["fuente"] == body["fuente"]
    finally:
        await runner2.cleanup()


# --------------------------------------------------------------------------------- CORS
async def test_cors_preflight_origen_localhost_permitido(control_srv, http):
    base, *_ = control_srv
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization, Content-Type",
    }
    async with http.options(f"{base}/api/control/salas", headers=headers) as r:
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
        assert "POST" in r.headers["Access-Control-Allow-Methods"]
        assert "Authorization" in r.headers["Access-Control-Allow-Headers"]


async def test_cors_preflight_origen_no_local_sin_cabecera(control_srv, http):
    base, *_ = control_srv
    headers = {"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
    async with http.options(f"{base}/api/control/salas", headers=headers) as r:
        assert r.status == 204
        assert "Access-Control-Allow-Origin" not in r.headers


# --------------------------------------------------------------------------------- audio (addendum)
NOMBRE_CLIP = Path(ARCHIVO).name  # nerdearla-en-booch-300s-60s.wav


async def test_audio_sirve_clip_sin_auth(control_srv, http):
    base, *_ = control_srv
    async with http.get(f"{base}/api/control/audio/{NOMBRE_CLIP}") as r:  # SIN Authorization
        assert r.status == 200
        assert r.headers["Content-Type"] == "audio/wav"
        cuerpo = await r.read()
    assert len(cuerpo) == (RAIZ / ARCHIVO).stat().st_size


@pytest.mark.parametrize("nombre", [
    "no-existe.wav",
    "..%2f..%2fCLAUDE.md",
    "sub/otra.wav",
    "..\\otra.wav",
    "nerdearla-en-booch-300s-60s.wav%00.txt",
])
async def test_audio_allowlist_traversal_404(control_srv, http, nombre):
    base, *_ = control_srv
    async with http.get(f"{base}/api/control/audio/{nombre}") as r:
        assert r.status == 404, nombre


async def test_audio_inicio_y_video_origen_en_salas(control_srv, http):
    base, *_ = control_srv
    body = _sala_body("sala-video-origen")
    async with http.post(f"{base}/api/control/salas", headers=_auth(), json=body) as r:
        assert r.status == 201
        creada = await r.json()
    assert creada["video_origen"] == {
        "url": "https://www.youtube.com/watch?v=cPaqkFCqWeg", "inicio_s": 300,
    }
    audio_inicio = None
    for _ in range(60):
        async with http.get(f"{base}/api/control/salas", headers=_auth()) as r:
            listado = await r.json()
        sala = next(s for s in listado["salas"] if s["id"] == "sala-video-origen")
        audio_inicio = sala["audio_inicio"]
        if audio_inicio is not None:
            break
        await asyncio.sleep(0.5)
    assert audio_inicio is not None, "audio_inicio nunca se completo en 30s"
    assert isinstance(audio_inicio, float)
