"""Tests del traductor por lotes (B2). Unit tests con un TRANSPORTE FALSO ROTULADO: no es el camino
real (el camino real es worker.traductor.TransporteGenAI) y no genera texto que parezca de Gemini:
devuelve "[FALSO-es] <entrada>" para que sea evidente en cualquier salida."""
import argparse
import asyncio
import json
from pathlib import Path

import pytest

from worker.traductor import Error5xx, Error429, Limitador, Lotes, Traductor, parsear
from worker.traducir_casete import traducir_casete

CASETES = Path(__file__).resolve().parents[2] / "fixtures" / "casetes"


class TransporteFalsoRotulado:
    """FALSO, SOLO TESTS. modos por llamada: 'ok' | 'cantidad' | '429' | 'lento' | 'error'."""

    def __init__(self, modos=None):
        self.modos = list(modos or [])
        self.llamadas = []

    async def generar(self, modelo, prompt):
        entrada = json.loads(prompt.split("Input:\n", 1)[1])
        modo = self.modos.pop(0) if self.modos else "ok"
        self.llamadas.append((modelo, len(entrada), modo))
        if modo == "429":
            raise Error429("429 RESOURCE_EXHAUSTED (falso)")
        if modo == "5xx":
            raise Error5xx("503 UNAVAILABLE (falso)")
        if modo == "error":
            raise RuntimeError("falso")
        if modo == "lento":
            await asyncio.sleep(10)
        if modo == "cantidad":
            return json.dumps(["[FALSO-es] solo uno"])
        return json.dumps([f"[FALSO-es] {x}" for x in entrada], ensure_ascii=False)


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def dormir(self, s):
        self.t += s


def lim(reloj=None, **kw):
    r = reloj or Reloj()
    return Limitador(["m-a", "m-b"], log=None, reloj=r, dormir=r.dormir, **kw), r


# ---- lotes ----
def test_lote_lleno_a_las_2_ventanas():
    L = Lotes()
    assert L.agregar(1, "a", 0.0) == []
    out = L.agregar(2, "b", 3.0)
    assert len(out) == 1 and [i.seq for i in out[0].items] == [1, 2] and out[0].motivo == "lleno"


def test_lote_por_tiempo_5s_desde_el_primero():
    L = Lotes(ventana_s=5.0)               # B8: el default paso a TRADUCTOR_LOTE_S (ver test de abajo)
    L.agregar(1, "a", 0.0)
    assert L.vencido(4.9) is None
    lote = L.vencido(5.0)
    assert [i.seq for i in lote.items] == [1] and lote.t_cierre == 5.0
    L.agregar(2, "b", 10.0)
    out = L.agregar(3, "c", 15.5)          # llega despues de 5 s: cierra el anterior y abre otro
    assert [i.seq for i in out[0].items] == [2] and out[0].motivo == "tiempo"
    assert [i.seq for i in L.vaciar(20.0).items] == [3]


# ---- limitador ----
def test_limitador_token_bucket_12rpm_y_rotacion():
    async def go():
        L, r = lim(capacidad=2)
        usados = [await L.tomar() for _ in range(4)]      # 2 tokens por modelo, alternando
        t_antes = r.t
        quinto = await L.tomar()                           # sin tokens: espera la recarga (5 s)
        return usados, quinto, r.t - t_antes
    usados, quinto, esperado = asyncio.run(go())
    assert sorted(usados) == ["m-a", "m-a", "m-b", "m-b"] and usados[0] != usados[1]
    assert quinto in ("m-a", "m-b") and 4.9 <= esperado <= 5.5


def test_limitador_nunca_pasa_12_en_60s_mas_capacidad():
    async def go():
        L, r = lim(capacidad=2)
        t0, n = r.t, 0
        while r.t - t0 < 60:
            m = await L.tomar(espera_max=120)
            if r.t - t0 < 60:
                n += 1
        return n
    n = asyncio.run(go())
    assert n <= 2 * (12 + 2)           # dos modelos: <= 14 cada uno en 60 s (< 15 RPM free tier)


def test_limitador_tope_diario_y_backoff():
    async def go():
        L, r = lim(rpd_tope=1)
        a = await L.tomar()
        b = await L.tomar()
        c = await L.tomar()          # los dos agotaron el tope diario
        return a, b, c
    a, b, c = asyncio.run(go())
    assert {a, b} == {"m-a", "m-b"} and c is None
    L, r = lim()
    assert L.fallo("m-a") == 1.0 and L.fallo("m-a") == 2.0
    for _ in range(10):
        L.fallo("m-a")
    assert L.m["m-a"].backoff_s == 30.0 and L.espera("m-a") >= 29.9


# ---- traductor ----
def _trad(tmp_path, modos, **kw):
    tr = TransporteFalsoRotulado(modos)
    t = Traductor("en", "es", transporte=tr, modelos=["m-a", "m-b"], log=tmp_path / "cuota-texto.log",
                  limitador=Limitador(["m-a", "m-b"], log=None), logger=lambda s: None, **kw)
    return t, tr


def test_traduce_un_lote_en_una_llamada(tmp_path):
    t, tr = _trad(tmp_path, ["ok"])
    res = asyncio.run(t.traducir(["one", "two", "three"], [7, 8, 9]))
    assert res.ok and [i["seq"] for i in res.items] == [7, 8, 9]
    assert res.items[0]["text"] == "[FALSO-es] one" and len(tr.llamadas) == 1
    lineas = (tmp_path / "cuota-texto.log").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 1 and lineas[0].split(" | ")[1:4] == ["m-a", "3", "ok"]


def test_cantidad_distinta_da_ok_false_sin_reintento(tmp_path):
    # B4: reintento SOLO ante 429/5xx (el reintento duplica carga)
    t, tr = _trad(tmp_path, ["cantidad", "ok"])
    res = asyncio.run(t.traducir(["a", "b"], [1, 2]))
    assert not res.ok and res.items == [{"seq": 1, "text": None, "ok": False},
                                        {"seq": 2, "text": None, "ok": False}]
    assert len(tr.llamadas) == 1 and t.n_ok_false == 1


def test_timeout_da_ok_false_sin_reintento(tmp_path):
    t, tr = _trad(tmp_path, ["lento", "ok"], timeout_s=0.2)
    res = asyncio.run(t.traducir(["a"], [5]))
    assert not res.ok and [i["estado"] for i in res.intentos] == ["timeout"]
    assert len(tr.llamadas) == 1 and t.n_timeout == 1


def test_5xx_reintenta_con_el_otro_modelo(tmp_path):
    t, tr = _trad(tmp_path, ["5xx", "ok"])
    res = asyncio.run(t.traducir(["a"], [5]))
    assert res.ok and [i["estado"] for i in res.intentos] == ["5xx", "ok"]
    assert tr.llamadas[0][0] != tr.llamadas[1][0] and t.n_reintentos == 1


def test_429_y_timeout_dan_ok_false_con_backoff_y_no_levantan(tmp_path):
    t, tr = _trad(tmp_path, ["429", "lento"], timeout_s=0.2)
    res = asyncio.run(t.traducir(["a"], [5]))
    assert not res.ok and [i["estado"] for i in res.intentos] == ["429", "timeout"]
    assert tr.llamadas[0][0] != tr.llamadas[1][0]            # el reintento rota de modelo
    assert all(e.backoff_s >= 1.0 for e in t.lim.m.values())
    estados = [l.split(" | ")[3] for l in (tmp_path / "cuota-texto.log").read_text().splitlines()]
    assert estados == ["429", "timeout"]


def test_parsear():
    assert parsear('["a", "b"]', 2) == ["a", "b"]
    assert parsear('```json\n["a"]\n```', 1) == ["a"]
    assert parsear('["a"]', 2) is None and parsear("no json", 1) is None


# ---- traducir_casete (transporte falso, casete REAL) ----
def test_traducir_casete_real_con_transporte_falso(tmp_path):
    src = CASETES / "b1-es-60s.jsonl"
    out = tmp_path / "x-trad.jsonl"
    t, tr = _trad(tmp_path, [])
    r = asyncio.run(traducir_casete(str(src), "en", str(out), traductor=t))
    lineas = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    cab, evs = lineas[0], lineas[1:]
    assert cab["derived_from"] == "b1-es-60s.jsonl" and cab["traduccion"]["lang_to"] == "en"
    textos = [e for e in evs if e["dir"] == "emit" and e["kind"] == "text"]
    trads = [e for e in evs if e["dir"] == "emit" and e["kind"] == "translation"]
    parc = [e for e in evs if e["dir"] == "emit" and e["kind"] == "partial"]
    assert len(textos) == 14 and r["textos"] == 14
    assert sorted(i["seq"] for e in trads for i in e["payload"]["items"]) == \
        sorted(e["payload"]["seq"] for e in textos)
    t_text = {e["payload"]["seq"]: e["t"] for e in textos}
    for e in trads:                                   # t despues del ultimo text del lote
        assert e["t"] >= max(t_text[i["seq"]] for i in e["payload"]["items"])
        assert e["payload"]["seq"] is None and e["payload"]["meta"]["lang_to"] == "en"
    assert len(tr.llamadas) == len(trads) == r["llamadas"]
    assert all(len(e["payload"]["items"]) <= 3 for e in trads)
    assert len(parc) == 84                            # = interimInputTranscription crudos
    ts = [e["t"] for e in evs]
    assert ts == sorted(ts)
    orig = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines()[1:]]
    assert [e for e in evs if e["kind"] not in ("translation", "partial")] == orig


def test_cerrar_marca_ok_false_lo_que_quedo_en_vuelo(tmp_path):
    import time

    async def go():
        t, tr = _trad(tmp_path, ["lento", "lento", "lento"], timeout_s=5)
        out = []
        t.on_resultado = out.append
        t.iniciar()
        for s, x in [(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")]:  # 2 lotes en vuelo, 5 pendiente
            t.agregar(s, x, time.time())
        await asyncio.sleep(0.1)
        await t.cerrar(0.3)
        return out
    out = asyncio.run(go())
    assert sorted(i["seq"] for r in out for i in r.items) == [1, 2, 3, 4, 5]
    assert all(i["ok"] is False and i["text"] is None for r in out for i in r.items)


def test_lotes_en_paralelo_un_lote_lento_no_bloquea_al_siguiente(tmp_path):
    """B4: cada lote es una tarea independiente. El lote 1 (lento) sigue en vuelo cuando el lote 2
    ya volvio traducido; la unica compuerta es el limitador."""
    import time

    async def go():
        t, tr = _trad(tmp_path, ["lento", "ok"], timeout_s=5)
        out = []
        t.on_resultado = out.append
        t.iniciar()
        for s, x in [(1, "a"), (2, "b"), (3, "c"), (4, "d")]:
            t.agregar(s, x, time.time())
        await asyncio.sleep(0.3)
        antes_de_cerrar = [(r.ok, [i["seq"] for i in r.items]) for r in out]
        en_vuelo = len(t._vuelo)
        await t.cerrar(0.1)
        return antes_de_cerrar, en_vuelo, t.max_en_vuelo, out
    antes, en_vuelo, maxv, out = asyncio.run(go())
    assert antes == [(True, [3, 4])], antes          # el 2do lote no espero al 1ro
    assert en_vuelo == 1 and maxv == 2
    assert [i["ok"] for i in out[-1].items] == [False, False]   # el lento sale marcado al cerrar


def test_translation_sin_modelo_valida_contra_el_contrato():
    from contracts import errores
    from worker.contrato import mensaje
    from worker.traductor import ResultadoLote, meta_traduccion
    res = ResultadoLote([{"seq": 3, "text": None, "ok": False}], None, 0, [{"modelo": None, "estado": "cierre"}])
    m = mensaje("translation", "s", None, "en", items=res.items, meta=meta_traduccion(res, "es", {"vivo": True}))
    assert errores(m) == [] and m["meta"]["model"] == "ninguno"


def test_lote_default_sale_de_traductor_lote_s():
    from worker import traductor as T
    assert (Lotes().maximo, Lotes().ventana_s) == (T.LOTE_MAX, T.LOTE_S)


# ---- B10: --a generico (no una lista cerrada de {es,en}) ----
def test_lang_destino_acepta_cualquier_iso_y_rechaza_formato_invalido():
    from worker.traducir_casete import lang_destino
    assert lang_destino("es") == "es" and lang_destino("en") == "en"
    assert lang_destino("pt") == "pt" and lang_destino("pt-BR") == "pt-BR"       # R8b: mas idiomas
    for malo in ("por", "PT", "p", "pt_BR", "pt-br", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            lang_destino(malo)


def test_cli_parser_real_acepta_a_pt_y_rechaza_invalido_sin_llamar_api():
    """Prueba el parser REAL de main() (construir_parser), no una copia: si alguien reintroduce
    choices=["es","en"] en --a, este test lo detecta."""
    from worker.traducir_casete import construir_parser
    ap = construir_parser()
    ns = ap.parse_args(["x.jsonl", "--a", "pt"])
    assert ns.a == "pt"
    with pytest.raises(SystemExit):
        ap.parse_args(["x.jsonl", "--a", "not-a-lang"])


def test_traducir_casete_a_pt_generico_transporte_falso(tmp_path):
    """El mismo casete real (ES) traducido a PT: no hay ningun camino especial para {es,en} adentro
    de traducir_casete/Traductor, y el mensaje resultante valida contra el contrato congelado
    (lang_destino admite cualquier codigo de 2 letras, ver contracts/esquema.json)."""
    from contracts import errores
    src = CASETES / "b1-es-60s.jsonl"
    out = tmp_path / "x-trad-pt.jsonl"
    t, tr = _trad(tmp_path, [])
    r = asyncio.run(traducir_casete(str(src), "pt", str(out), traductor=t))
    assert r["a"] == "pt" and r["items_ok"] == r["textos"] == 14
    lineas = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    cab, evs = lineas[0], lineas[1:]
    assert cab["traduccion"]["lang_to"] == "pt"
    trads = [e for e in evs if e["dir"] == "emit" and e["kind"] == "translation"]
    assert trads and all(e["payload"]["meta"]["lang_to"] == "pt" for e in trads)
    assert all(errores(e["payload"]) == [] for e in trads)      # contrato congelado, sin cambios
