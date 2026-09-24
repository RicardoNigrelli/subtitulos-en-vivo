"""B2 (aditivo): `translation` mergeado por session_id+seq, item pendiente, `partial` en vivo y
fuera del historial, `translations_langs`, HUB_HOST/HUB_TOKEN. Hub REAL en puerto efimero."""
from __future__ import annotations

import asyncio
import json

import pytest

from contracts import errores, errores_frame
from hub.cliente_ws import escuchar
from hub.inyectar import cargar, inyectar
from hub.tests.util import EJEMPLOS, TOKEN, levantar, productor, recibir, texto

# (async: marcados uno por uno; los tests sync no llevan la marca)

T0 = 1790262000.0


def texto_crudo(sid: str, seq: int, lang: str = "en") -> dict:
    """Como lo emite el worker en B2: el text sale YA, con translations vacio."""
    m = texto(sid, seq, lang=lang)
    m["translations"] = {}
    return m


def traduccion(sid: str, items: list[tuple[int, str | None, bool]], lang_to: str = "es",
               t_emit: float = T0 + 100.0, lang: str = "en") -> dict:
    return {"v": 1, "type": "translation", "session_id": sid, "seq": None, "lang": lang, "text": None,
            "translations": {}, "t_emit": t_emit, "replay": True,
            "meta": {"lang_to": lang_to, "model": "test-hub", "batch_ms": 800},
            "items": [{"seq": s, "text": t, "ok": ok} for s, t, ok in items]}


def parcial(sid: str, txt: str, t_emit: float = T0 + 50.0) -> dict:
    return {"v": 1, "type": "partial", "session_id": sid, "seq": None, "lang": "en", "text": txt,
            "audio_start": 0.0, "t_captured": t_emit - 0.2, "t_emit": t_emit, "replay": True, "meta": {}}


async def _historial(http, h, sid: str, extra: str = "") -> dict[int, dict]:
    async with http.get(f"{h.http}/api/sesiones/{sid}/historial?desde=0{extra}") as r:
        assert r.status == 200
        return {m["seq"]: m for m in await r.json()}


async def _sesion(http, h, sid: str) -> dict:
    async with http.get(f"{h.http}/api/sesiones") as r:
        return {s["session_id"]: s for s in await r.json()}[sid]


# --------------------------------------------------------------------------- contrato
def test_contrato_translation_y_partial():
    tr = traduccion("s", [(1, "Uno.", True), (2, None, False)])
    assert errores(tr) == []
    assert errores(parcial("s", "so the")) == []
    assert errores(parcial("s", "")) == []  # un partial vacio es valido (limpia el gris)
    malos = {
        "translation con seq entero": {**tr, "seq": 7},
        "partial con seq entero": {**parcial("s", "x"), "seq": 7},
        "item ok:true sin texto": traduccion("s", [(1, None, True)]),
        "item ok:false con texto": traduccion("s", [(1, "algo", False)]),
        "items vacio": {**tr, "items": []},
        "sin items": {k: v for k, v in tr.items() if k != "items"},
        "sin meta.lang_to": {**tr, "meta": {"model": "m", "batch_ms": 1}},
        "lang_to invalido": {**tr, "meta": {"lang_to": "ES!", "model": "m", "batch_ms": 1}},
        "item sin seq": {**tr, "items": [{"text": "x", "ok": True}]},
        "partial sin text": {k: v for k, v in parcial("s", "x").items() if k != "text"},
        "text viejo con seq null (sigue prohibido)": {**texto("s", 1), "seq": None},
    }
    for caso, m in malos.items():
        assert errores(m), f"deberia ser invalido: {caso}"


# --------------------------------------------------------------------------- merge
@pytest.mark.asyncio
async def test_merge_por_seq_idempotente_y_en_vivo(hub, http):
    sid = "merge-t"
    async with http.ws_connect(f"{hub.ws}/ws/{sid}?lang=es") as cli:
        assert json.loads((await cli.receive(timeout=5)).data)["type"] == "init"
        ws, _ = await productor(http, hub)
        for n in (1, 2):
            await ws.send_str(json.dumps(texto_crudo(sid, n)))
        tr = traduccion(sid, [(1, "Uno, traducido.", True), (2, None, False)])
        await ws.send_str(json.dumps(tr))
        vivos = await recibir(cli, n=3, timeout=5)
        assert [m["type"] for m in vivos] == ["text", "text", "translation"], vivos
        assert all(m["translations"] == {} for m in vivos[:2])  # salieron antes que la traduccion
        ev = vivos[2]
        assert ev["items"] == tr["items"] and ev["meta"] == tr["meta"] and "t_hub" in ev  # tal cual + t_hub
        assert errores(ev) == []

        h = await _historial(http, hub, sid)
        assert h[1]["translations"]["es"] == {"text": "Uno, traducido.", "ok": True}
        assert h[2]["translations"]["es"] == {"text": None, "ok": False}
        assert all(errores(m) == [] for m in h.values())

        # idempotente: el mismo evento otra vez no cambia nada y NO se reenvia
        await ws.send_str(json.dumps(tr))
        # un ok:false no pisa un ok:true (reintento fallido) -> sin cambio, no se reenvia
        await ws.send_str(json.dumps(traduccion(sid, [(1, None, False)])))
        assert await recibir(cli, n=1, timeout=0.8) == []
        assert (await _historial(http, hub, sid))[1]["translations"]["es"]["ok"] is True

        # un ok:true reemplaza al ok:false (reintento exitoso) y se reenvia
        await ws.send_str(json.dumps(traduccion(sid, [(2, "Dos, reintento.", True)])))
        otra = await recibir(cli, n=1, timeout=5)
        assert otra and otra[0]["type"] == "translation" and otra[0]["items"][0]["seq"] == 2
        assert (await _historial(http, hub, sid))[2]["translations"]["es"] == {"text": "Dos, reintento.", "ok": True}
        await ws.close()

    # un cliente NUEVO recibe las lineas ya mergeadas en el init; last_seq no lo toco la traduccion
    async with http.ws_connect(f"{hub.ws}/ws/{sid}?lang=es") as cli2:
        init = json.loads((await cli2.receive(timeout=5)).data)
    assert errores_frame(init, "init") == []
    assert init["last_seq"] == 2 and init["translations_langs"] == ["es"]
    assert [ln["translations"]["es"]["text"] for ln in init["lines"]] == ["Uno, traducido.", "Dos, reintento."]
    met = hub.nucleo.sesiones[sid].metricas()
    assert met["traducciones"] == 4 and met["traducciones_sin_cambio"] == 2 and met["items_aplicados"] == 3


@pytest.mark.asyncio
async def test_item_pendiente_llega_antes_que_el_text(hub, http):
    sid = "pend-t"
    async with http.ws_connect(f"{hub.ws}/ws/{sid}?lang=es") as cli:
        assert json.loads((await cli.receive(timeout=5)).data)["type"] == "init"
        ws, _ = await productor(http, hub)
        await ws.send_str(json.dumps(texto_crudo(sid, 1)))
        await ws.send_str(json.dumps(traduccion(sid, [(1, "Uno.", True), (2, "Dos, llego antes.", True)])))
        vivos = await recibir(cli, n=2, timeout=5)
        assert [m["type"] for m in vivos] == ["text", "translation"]
        assert hub.nucleo.sesiones[sid].metricas()["pendientes_ahora"] == 1
        # llega el text 2 SIN traduccion: el hub lo reparte ya mergeado con el item pendiente
        await ws.send_str(json.dumps(texto_crudo(sid, 2)))
        t2 = await recibir(cli, n=1, timeout=5)
        assert t2[0]["type"] == "text" and t2[0]["seq"] == 2
        assert t2[0]["translations"] == {"es": {"text": "Dos, llego antes.", "ok": True}}
        await ws.close()
    h = await _historial(http, hub, sid)
    assert h[2]["translations"]["es"]["text"] == "Dos, llego antes."
    met = hub.nucleo.sesiones[sid].metricas()
    assert met["pendientes_ahora"] == 0 and met["items_pendientes"] == 1 and met["items_aplicados"] == 2


@pytest.mark.asyncio
async def test_item_pendiente_vence_y_huerfano(http):
    h = await levantar(pendiente_s=0.3, heartbeat_s=0.1)
    try:
        sid = "vence-t"
        ws, _ = await productor(http, h)
        await ws.send_str(json.dumps(texto_crudo(sid, 1)))
        await ws.send_str(json.dumps({"v": 1, "type": "session_end", "session_id": sid, "seq": 2, "lang": "en",
                                      "t_emit": T0 + 9.0, "replay": True, "meta": {}}))
        # seq 9 no existe todavia (pendiente) · seq 2 existe pero NO es text (huerfano)
        await ws.send_str(json.dumps(traduccion(sid, [(9, "Nueve.", True), (2, "No es un text.", True)])))
        await asyncio.sleep(0.8)  # > pendiente_s: el item del seq 9 vence
        await ws.send_str(json.dumps(texto_crudo(sid, 9)))
        await asyncio.sleep(0.2)
        await ws.close()
        hist = await _historial(http, h, sid)
        assert hist[9]["translations"] == {}
        met = h.nucleo.sesiones[sid].metricas()
        assert met["items_vencidos"] == 1 and met["items_huerfanos"] == 1 and met["pendientes_ahora"] == 0
    finally:
        await h.parar()


# --------------------------------------------------------------------------- partial
@pytest.mark.asyncio
async def test_partial_en_vivo_y_ausente_del_historial(hub, http):
    sid = "parcial-t"
    async with http.ws_connect(f"{hub.ws}/ws/{sid}?lang=en") as cli:
        assert json.loads((await cli.receive(timeout=5)).data)["type"] == "init"
        ws, _ = await productor(http, hub)
        await ws.send_str(json.dumps(texto_crudo(sid, 1)))
        await ws.send_str(json.dumps(parcial(sid, "Mensaje de prueba parcial")))
        await ws.send_str(json.dumps(parcial(sid, "Mensaje de prueba parcial que crece", T0 + 51.0)))
        vivos = await recibir(cli, n=3, timeout=5)
        assert [m["type"] for m in vivos] == ["text", "partial", "partial"]
        assert vivos[2]["text"] == "Mensaje de prueba parcial que crece" and "t_hub" in vivos[2]
        assert vivos[1]["seq"] is None and errores(vivos[1]) == []
        await ws.close()
    todos = await _historial(http, hub, sid, "&tipos=todos")
    assert list(todos) == [1] and todos[1]["type"] == "text"  # el partial NO se guardo
    s = await _sesion(http, hub, sid)
    assert s["last_seq"] == 1  # el partial no toca last_seq
    _, ok = await productor(http, hub)
    assert ok["last_seq"][sid] == 1
    async with http.ws_connect(f"{hub.ws}/ws/{sid}") as cli2:
        init = json.loads((await cli2.receive(timeout=5)).data)
    assert init["last_seq"] == 1 and [ln["type"] for ln in init["lines"]] == ["text"]
    assert hub.nucleo.sesiones[sid].metricas()["parciales"] == 2


# --------------------------------------------------------------------------- /api/sesiones
@pytest.mark.asyncio
async def test_translations_langs(hub, http):
    ws, _ = await productor(http, hub)
    await ws.send_str(json.dumps(texto_crudo("langs-en", 1)))
    await ws.send_str(json.dumps(texto("langs-es", 1, lang="es") | {"translations": {"en": {"text": "One.", "ok": True}}}))
    await ws.send_str(json.dumps(texto_crudo("langs-nada", 1)))
    await asyncio.sleep(0.1)
    assert (await _sesion(http, hub, "langs-en"))["translations_langs"] == []
    await ws.send_str(json.dumps(traduccion("langs-en", [(1, "Uno.", True)])))
    await ws.send_str(json.dumps(traduccion("langs-en", [(1, "Um.", True)], lang_to="pt-BR")))
    await asyncio.sleep(0.2)
    await ws.close()
    assert (await _sesion(http, hub, "langs-en"))["translations_langs"] == ["es", "pt-BR"]
    assert (await _sesion(http, hub, "langs-es"))["translations_langs"] == ["en"]  # visto en el text
    assert (await _sesion(http, hub, "langs-nada"))["translations_langs"] == []


# --------------------------------------------------------------------------- ejemplo + cliente_ws
@pytest.mark.asyncio
async def test_ejemplo_traduccion_por_el_hub_con_cliente_ws(hub, http):
    """contracts/ejemplos/traduccion.jsonl (sintetico) por /ingest; hub.cliente_ws lo mira en vivo."""
    lineas: list[str] = []
    escucha = asyncio.create_task(escuchar(f"{hub.ws}/ws/ejemplo-traduccion?lang=es", "es", None, 3.0, tras_fin=0.5,
                                           out=lineas.append))
    await asyncio.sleep(0.3)
    res_iny = await inyectar(cargar(EJEMPLOS / "traduccion.jsonl"), hub.ingest, TOKEN, velocidad=50)
    res = await escucha
    assert res_iny["enviados"] == 10 and not res_iny["rechazados"]
    assert res["problemas"] == [], res["problemas"]
    assert res["traducciones"] == 2 and res["parciales"] == 3
    textos = {m["seq"]: m for m in res["mensajes"] if m["type"] == "text"}
    # seq 4: su traduccion llego antes -> el text llega mergeado
    assert textos[4]["translations"]["es"]["ok"] is True
    h = await _historial(http, hub, "ejemplo-traduccion")
    assert h[2]["translations"]["es"]["ok"] is True
    assert h[3]["translations"]["es"] == {"text": None, "ok": False}
    assert h[4]["translations"]["es"]["text"].startswith("Ejemplo de contrato")


# --------------------------------------------------------------------------- config
def test_hub_host_default_y_token_desde_dotenv(monkeypatch, tmp_path):
    import hub.config as config

    for k in ("HUB_HOST", "HUB_TOKEN", "HUB_PENDIENTE_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "RAIZ", tmp_path)
    assert config.cargar_config().host == "127.0.0.1"
    (tmp_path / ".env").write_text("HUB_TOKEN=token-desde-dotenv\nHUB_PENDIENTE_S=45\n"
                                   "GEMINI_API_KEY=no-se-lee\n", encoding="utf-8")
    c = config.cargar_config()
    assert (c.token, c.token_origen, c.pendiente_s) == ("token-desde-dotenv", ".env", 45.0)
    assert "no-se-lee" not in repr(config._dotenv())  # solo claves HUB_*
    monkeypatch.setenv("HUB_HOST", "0.0.0.0")
    monkeypatch.setenv("HUB_TOKEN", "token-del-entorno")
    c = config.cargar_config()
    assert (c.host, c.token, c.token_origen) == ("0.0.0.0", "token-del-entorno", "entorno")
