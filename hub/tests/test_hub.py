"""El hub real (aiohttp) en un puerto efimero: ingesta, audiencia, HTTP, backlog, aislamiento."""
from __future__ import annotations

import asyncio
import json
import time

import aiohttp
import pytest

from contracts import errores, errores_frame
from hub.inyectar import cargar, inyectar
from hub.tests.util import EJEMPLOS, TOKEN, levantar, productor, recibir, texto

pytestmark = pytest.mark.asyncio


async def test_health_y_cors(hub, http):
    async with http.get(f"{hub.http}/health") as r:
        assert r.status == 200
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert (await r.json())["ok"] is True
    async with http.options(f"{hub.http}/api/sesiones",
                            headers={"Origin": "http://localhost:8101",
                                     "Access-Control-Request-Method": "GET"}) as r:
        assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "*"
    async with http.get(f"{hub.http}/api/sesiones/no-existe/historial") as r:
        assert r.status == 404 and r.headers["Access-Control-Allow-Origin"] == "*"


async def test_estado_inicial_al_conectar(hub, http):
    ws, ok = await productor(http, hub)
    assert ok["last_seq"] == {}
    for n in range(1, 16):
        await ws.send_str(json.dumps(texto("sala-t", n)))
    await ws.close()
    await asyncio.sleep(0.1)
    async with http.ws_connect(f"{hub.ws}/ws/sala-t?lang=es") as cli:
        init = json.loads((await cli.receive(timeout=5)).data)
    assert init["type"] == "init", init
    assert errores_frame(init, "init") == []
    assert init["session_id"] == "sala-t" and init["lang"] == "es" and init["session_lang"] == "en"
    assert init["last_seq"] == 15
    assert [m["seq"] for m in init["lines"]] == list(range(6, 16))  # ultimas 10, por seq
    assert all(errores(m) == [] and "t_hub" in m for m in init["lines"])


async def test_seq_creciente_inyectando_ejemplo_en(hub, http):
    async with http.ws_connect(f"{hub.ws}/ws/ejemplo-en?lang=es") as cli:
        init = json.loads((await cli.receive(timeout=5)).data)
        assert init["type"] == "init" and init["last_seq"] is None and init["lines"] == []
        t0 = time.monotonic()
        tarea = asyncio.create_task(recibir(cli, n=20, timeout=15))
        res = await inyectar(cargar(EJEMPLOS / "sesion-en.jsonl"), hub.ingest, TOKEN, velocidad=30)
        msgs = await tarea
        dur = time.monotonic() - t0
    assert res["enviados"] == 20 and not res["rechazados"]
    seqs = [m["seq"] for m in msgs]
    assert seqs == list(range(1, 21)), seqs
    assert all(errores(m) == [] for m in msgs)
    assert all(m["replay"] is True and m["t_hub"] >= m["t_emit"] for m in msgs)
    # 19 gaps de 3 s a velocidad 30 = 1,9 s: el inyector respeta los tiempos
    assert dur >= 1.7, dur


async def test_dos_sesiones_a_la_vez_no_se_mezclan(hub, http):
    """R21 a nivel hub: dos sesiones inyectadas en paralelo llegan cada una a SU cliente."""
    async with http.ws_connect(f"{hub.ws}/ws/ejemplo-en?lang=es") as c_en, \
            http.ws_connect(f"{hub.ws}/ws/ejemplo-es?lang=en") as c_es:
        for c in (c_en, c_es):
            assert json.loads((await c.receive(timeout=5)).data)["type"] == "init"
        r_en = asyncio.create_task(recibir(c_en, n=20, timeout=15))
        r_es = asyncio.create_task(recibir(c_es, n=20, timeout=15))
        await asyncio.gather(
            inyectar(cargar(EJEMPLOS / "sesion-en.jsonl"), hub.ingest, TOKEN, velocidad=40),
            inyectar(cargar(EJEMPLOS / "sesion-es.jsonl"), hub.ingest, TOKEN, velocidad=40))
        m_en, m_es = await r_en, await r_es
    assert [m["seq"] for m in m_en] == list(range(1, 21))
    assert [m["seq"] for m in m_es] == list(range(1, 21))
    assert {m["session_id"] for m in m_en} == {"ejemplo-en"} and {m["lang"] for m in m_en} == {"en"}
    assert {m["session_id"] for m in m_es} == {"ejemplo-es"} and {m["lang"] for m in m_es} == {"es"}
    async with http.get(f"{hub.http}/api/sesiones") as r:
        ses = {s["session_id"]: s for s in await r.json()}
    assert set(ses) == {"ejemplo-en", "ejemplo-es"}
    assert ses["ejemplo-en"]["last_seq"] == 20 and ses["ejemplo-es"]["last_seq"] == 20


async def test_token_invalido_se_rechaza(hub, http):
    # 1) token incorrecto
    ws = await http.ws_connect(hub.ingest)
    await ws.send_str(json.dumps({"type": "auth", "token": "no-es-el-token"}))
    r = await ws.receive(timeout=5)
    assert r.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
    assert ws.close_code == 4401
    await ws.close()
    # 2) sin auth: manda directamente un mensaje
    ws = await http.ws_connect(hub.ingest)
    await ws.send_str(json.dumps(texto("intrusa", 1)))
    r = await ws.receive(timeout=5)
    assert ws.close_code == 4401
    await ws.close()
    # 3) token en la query string: no sirve (nunca en la URL)
    ws = await http.ws_connect(f"{hub.ingest}?token={TOKEN}")
    await ws.send_str(json.dumps(texto("intrusa", 1)))
    await ws.receive(timeout=5)
    assert ws.close_code == 4401
    await ws.close()
    async with http.get(f"{hub.http}/api/sesiones") as r:
        assert await r.json() == []
    # HTTP protegido: Bearer
    async with http.get(f"{hub.http}/api/metricas") as r:
        assert r.status == 401
    async with http.get(f"{hub.http}/api/metricas", headers={"Authorization": "Bearer otro"}) as r:
        assert r.status == 401
    async with http.get(f"{hub.http}/api/metricas", headers={"Authorization": f"Bearer {TOKEN}"}) as r:
        assert r.status == 200
        assert (await r.json())["auth_fallidas"] == 3


async def test_backlog_reconexion_sin_duplicar(hub, http):
    async with http.ws_connect(f"{hub.ws}/ws/sala-b?lang=es") as cli:
        await cli.receive(timeout=5)  # init
        ws, ok = await productor(http, hub)
        for n in range(1, 11):
            await ws.send_str(json.dumps(texto("sala-b", n)))
        await ws.close()  # se corta el productor
        ws, ok = await productor(http, hub)
        assert ok["last_seq"] == {"sala-b": 10}
        # un productor torpe reenvia de mas (6..15): el hub ignora 6..10
        for n in range(6, 16):
            await ws.send_str(json.dumps(texto("sala-b", n)))
        await ws.close()
        msgs = await recibir(cli, n=15, timeout=5)
        extra = await recibir(cli, timeout=0.5)
    assert [m["seq"] for m in msgs] == list(range(1, 16)) and extra == []
    assert hub.nucleo.sesiones["sala-b"].duplicados == 5
    # el inyector, como productor, manda solo seq > last_seq
    msgs_arch = [texto("sala-b", n) for n in range(1, 21)]
    res = await inyectar(msgs_arch, hub.ingest, TOKEN, velocidad=0)
    assert res["salteados"] == 15 and res["enviados"] == 5
    assert hub.nucleo.sesiones["sala-b"].last_seq == 20


async def test_backlog_tras_reinicio_del_hub(http):
    """El hub reinicia vacio: auth_ok.last_seq = {} y el productor reenvia todo su backlog."""
    h1 = await levantar()
    ws, _ = await productor(http, h1)
    for n in range(1, 8):
        await ws.send_str(json.dumps(texto("sala-r", n)))
    await ws.close()
    await h1.parar()
    h2 = await levantar()
    try:
        backlog = [texto("sala-r", n) for n in range(1, 13)]  # el worker tiene 1..12
        res = await inyectar(backlog, h2.ingest, TOKEN, velocidad=0)
        assert res["last_seq_hub"] == {} and res["enviados"] == 12
        async with http.ws_connect(f"{h2.ws}/ws/sala-r?lang=es") as cli:
            init = json.loads((await cli.receive(timeout=5)).data)
        assert init["last_seq"] == 12 and [m["seq"] for m in init["lines"]] == list(range(3, 13))
    finally:
        await h2.parar()


async def test_mensaje_invalido_se_rechaza_y_no_se_reparte(hub, http):
    async with http.ws_connect(f"{hub.ws}/ws/sala-x") as cli:
        await cli.receive(timeout=5)
        ws, _ = await productor(http, hub)
        malo = texto("sala-x", 1)
        del malo["t_emit"]
        await ws.send_str(json.dumps(malo))
        await ws.send_str("esto no es json")
        r1 = json.loads((await ws.receive(timeout=5)).data)
        r2 = json.loads((await ws.receive(timeout=5)).data)
        await ws.send_str(json.dumps(texto("sala-x", 2)))
        msgs = await recibir(cli, n=1, timeout=3)
        await ws.close()
    assert r1["type"] == "rechazado" and r1["seq"] == 1 and any("t_emit" in e for e in r1["errores"])
    assert r2["type"] == "rechazado" and "JSON" in r2["errores"][0]
    assert [m["seq"] for m in msgs] == [2]


async def test_latido_hub_y_latido_worker(hub, http):
    async with http.ws_connect(f"{hub.ws}/ws/sala-h?lang=es") as cli:
        await cli.receive(timeout=5)
        ws, _ = await productor(http, hub)
        await ws.send_str(json.dumps(texto("sala-h", 1)))
        await ws.send_str(json.dumps({"v": 1, "type": "heartbeat", "session_id": "sala-h", "seq": None,
                                      "lang": "en", "t_emit": 1790262010.0, "replay": True,
                                      "meta": {"alive": True, "audio_seconds_sent": 3.0}}))
        latidos: list = []
        msgs = await recibir(cli, timeout=1.0, latidos=latidos)
        await ws.close()
    assert [m["type"] for m in msgs] == ["text"], "el heartbeat del worker no se reenvia"
    assert len(latidos) >= 3  # heartbeat_s=0.2 en tests
    assert all(errores(hb) == [] for hb in latidos)
    assert latidos[-1]["last_seq"] == 1 and latidos[-1]["state"] == "live"
    assert hub.nucleo.sesiones["sala-h"].latidos_worker == 1


async def test_sesiones_estado_historial_y_tipos(http):
    h = await levantar(live_s=0.5)
    try:
        res = await inyectar(cargar(EJEMPLOS / "tipos.jsonl"), h.ingest, TOKEN, velocidad=0)
        assert res["enviados"] == 8 and not res["rechazados"]
        res = await inyectar(cargar(EJEMPLOS / "sesion-es.jsonl"), h.ingest, TOKEN, velocidad=0)
        async with http.get(f"{h.http}/api/sesiones") as r:
            ses = {s["session_id"]: s for s in await r.json()}
        t = ses["ejemplo-tipos"]
        assert t["state"] == "ended" and t["last_seq"] == 7 and t["replay"] is True
        assert t["title"] == "Ejemplo de contrato: un mensaje de cada tipo"
        assert ses["ejemplo-es"]["state"] == "live" and ses["ejemplo-es"]["lang"] == "es"
        await asyncio.sleep(0.7)
        async with http.get(f"{h.http}/api/sesiones") as r:
            assert {s["session_id"]: s["state"] for s in await r.json()}["ejemplo-es"] == "idle"
        async with http.get(f"{h.http}/api/sesiones/ejemplo-tipos/historial?desde=0") as r:
            assert [m["seq"] for m in await r.json()] == [2, 6]
        async with http.get(f"{h.http}/api/sesiones/ejemplo-tipos/historial?desde=2") as r:
            assert [m["seq"] for m in await r.json()] == [6]
        async with http.get(f"{h.http}/api/sesiones/ejemplo-tipos/historial?desde=0&tipos=todos") as r:
            assert [m["seq"] for m in await r.json()] == [1, 2, 3, 4, 5, 6, 7]
        async with http.get(f"{h.http}/api/sesiones/ejemplo-es/historial?desde=abc") as r:
            assert r.status == 400
    finally:
        await h.parar()


async def test_inyectar_casete_toma_solo_emit_y_no_toca_tiempos(hub, http, tmp_path):
    original = texto("cas-1", 1)
    original["replay"] = False
    lineas = [{"casete": 1, "session_id": "cas-1"},
              {"t": 1.0, "dir": "server", "kind": "x", "payload": {"serverContent": {}}},
              {"t": 2.0, "dir": "emit", "kind": "text", "payload": original}]
    cas = tmp_path / "cas.jsonl"
    cas.write_text("\n".join(json.dumps(x) for x in lineas) + "\n", encoding="utf-8")
    msgs = cargar(cas)
    assert len(msgs) == 1 and msgs[0]["replay"] is True
    await inyectar(msgs, hub.ingest, TOKEN, velocidad=0)
    async with http.get(f"{hub.http}/api/sesiones/cas-1/historial") as r:
        (m,) = await r.json()
    assert m["replay"] is True
    assert m["t_emit"] == original["t_emit"] and m["t_captured"] == original["t_captured"]
    # --session-id reescribe la sesion (misma grabacion como otra sala)
    assert {x["session_id"] for x in cargar(cas, session_id="otra")} == {"otra"}


async def test_session_id_invalido_en_la_url(hub, http):
    async with http.get(f"{hub.http}/ws/Sala%201") as r:
        assert r.status == 400
    async with http.get(f"{hub.http}/ws/sala-1?lang=espanol") as r:
        assert r.status == 400
