"""B5: cortacircuito por modelo del traductor (worker/traductor.py). SIN API.

TRANSPORTE FALSO ROTULADO, SOLO TESTS: no es el camino real (el real es TransporteGenAI) y no genera
texto que parezca de Gemini: devuelve "[FALSO-es] <entrada>". La respuesta depende del MODELO, para
simular "gemini-3.1-flash-lite falla, el otro anda" (ESTADO.md: 3.1 fallo 8 de 11 en vivo).
Reloj falso inyectado en el Limitador: los 60 s / 120 s / 8 min de corte no se esperan de verdad.
"""
import asyncio
import json
import time

from worker.traductor import (LOTE_MAX, LOTE_S, Error429, Error5xx, Limitador, Traductor, leer_log,
                              meta_traduccion)


class TransporteFalsoPorModelo:
    """FALSO, SOLO TESTS. fallas = {modelo: "5xx" | "429" | "lento"}; el resto responde ok."""

    def __init__(self, fallas=None):
        self.fallas = dict(fallas or {})
        self.llamadas = []

    async def generar(self, modelo, prompt):
        entrada = json.loads(prompt.split("Input:\n", 1)[1])
        modo = self.fallas.get(modelo, "ok")
        self.llamadas.append((modelo, modo))
        if modo == "5xx":
            raise Error5xx("503 UNAVAILABLE (falso)")
        if modo == "429":
            raise Error429("429 RESOURCE_EXHAUSTED (falso)")
        if modo == "lento":
            await asyncio.sleep(10)
        return json.dumps([f"[FALSO-es] {x}" for x in entrada], ensure_ascii=False)


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def dormir(self, s):
        self.t += s


def _lim(r, **kw):
    return Limitador(["m-a", "m-b"], log=None, reloj=r, dormir=r.dormir, **kw)


def _trad(tmp_path, L, fallas, **kw):
    tr = TransporteFalsoPorModelo(fallas)
    t = Traductor("en", "es", transporte=tr, modelos=["m-a", "m-b"], log=tmp_path / "cuota-texto.log",
                  limitador=L, logger=lambda s: None, **kw)
    return t, tr


def _cortes(L, modelo):
    return [float(e["detalle"].split(" s ")[0]) for e in L.eventos
            if e["modelo"] == modelo and e["tipo"] == "corte"]


# ---- 3 fallas seguidas => corte => los lotes siguientes van al otro modelo ----
def test_3_fallas_seguidas_cortan_al_modelo_y_los_lotes_siguientes_van_al_otro(tmp_path):
    async def go():
        r = Reloj()
        L = _lim(r)
        t, tr = _trad(tmp_path, L, {"m-b": "5xx"})
        antes = []
        for i in range(40):
            antes.append(await t.traducir([f"x{i}"], [i]))
            if L.m["m-b"].cortado_hasta > r.t:
                break
        t_corte = [e["t"] for e in L.eventos if e["tipo"] == "corte"][0]
        n0 = len(tr.llamadas)
        despues = [await t.traducir([f"y{i}"], [100 + i]) for i in range(6)]
        return r, L, t, tr, antes, despues, n0, t_corte

    r, L, t, tr, antes, despues, n0, t_corte = asyncio.run(go())
    llamadas_b = [m for m, _ in tr.llamadas[:n0] if m == "m-b"]
    assert len(llamadas_b) == 3                                  # exactamente 3 fallas seguidas
    assert L.m["m-b"].cortado_hasta - t_corte == 60.0            # TRADUCTOR_CORTE_S default
    assert _cortes(L, "m-b") == [60.0] and L.sanos() == ["m-a"]
    # los siguientes: TODOS a m-a, todos ok, y el corte sigue vigente (no fue que se venció)
    assert [m for m, _ in tr.llamadas[n0:]] == ["m-a"] * 6
    assert all(res.ok for res in despues) and r.t < L.m["m-b"].cortado_hasta
    # los lotes de antes del corte: el 5xx de m-b se reintentó y salió ok
    assert all(res.ok for res in antes) and t.n_reintentos == 3
    # log: linea ROTULADA que NO cuenta como llamada
    log = tmp_path / "cuota-texto.log"
    lineas = log.read_text(encoding="utf-8").splitlines()
    rotuladas = [x for x in lineas if x.startswith("# cortacircuito")]
    assert len(rotuladas) == 1 and " | m-b | corte | 60 s " in rotuladas[0]
    assert "5xx,5xx,5xx" in rotuladas[0] and rotuladas[0].endswith("sanos: m-a")
    assert len(leer_log(log)) == len(tr.llamadas) == len(lineas) - 1
    assert Limitador(["m-a", "m-b"], log=log).m["m-b"].hoy == 3   # el RPD no cuenta la rotulada


def test_timeout_y_429_tambien_cuentan_cantidad_y_otros_errores_no():
    r = Reloj()
    L = _lim(r)
    L.fallo("m-b", "timeout")
    L.fallo("m-b", "429")
    assert L.sanos() == ["m-a", "m-b"]
    L.fallo("m-b", "5xx")
    assert L.sanos() == ["m-a"]
    for _ in range(6):
        L.fallo("m-a", "error:RuntimeError")          # no cuenta: solo backoff
    assert "m-a" in L.sanos() and L.m["m-a"].fallas_seguidas == 0


# ---- recuperacion: reingreso a prueba, duplicacion hasta 8 min, una ok reinicia ----
def test_recuperacion_reingreso_a_prueba_duplica_hasta_8_min_y_una_ok_reinicia():
    r = Reloj()
    L = _lim(r)
    for _ in range(3):
        L.fallo("m-b", "5xx")
    assert L.sanos() == ["m-a"]
    for esperado in (120.0, 240.0, 480.0, 480.0):      # corte vencido -> reingresa a prueba -> 1 falla
        r.t = L.m["m-b"].cortado_hasta
        assert L.sanos() == ["m-a", "m-b"]            # reingreso
        L.fallo("m-b", "timeout")
        assert L.sanos() == ["m-a"] and L.m["m-b"].cortado_hasta - r.t == esperado
    assert _cortes(L, "m-b") == [60.0, 120.0, 240.0, 480.0, 480.0]      # tope 8 min
    assert [e["tipo"] for e in L.eventos].count("reingreso") == 4
    # vence, reingresa y la llamada de prueba sale ok => recuperacion: contador y duplicacion a cero
    r.t = L.m["m-b"].cortado_hasta
    assert L.sanos() == ["m-a", "m-b"]
    L.exito("m-b")
    assert L.eventos[-1]["tipo"] == "recuperacion" and L.eventos[-1]["modelo"] == "m-b"
    assert L.m["m-b"].fallas_seguidas == 0 and L.m["m-b"].cortes == 0
    L.fallo("m-b", "5xx")
    L.fallo("m-b", "5xx")
    assert L.sanos() == ["m-a", "m-b"]                # vuelven a hacer falta 3
    L.fallo("m-b", "5xx")
    assert _cortes(L, "m-b")[-1] == 60.0              # la duplicacion tambien se reinicio


def test_una_ok_de_una_llamada_en_vuelo_levanta_el_corte():
    r = Reloj()
    L = _lim(r)
    for _ in range(3):
        L.fallo("m-b", "5xx")
    assert L.sanos() == ["m-a"]
    L.exito("m-b")                                     # respuesta ok de una llamada lanzada antes
    assert L.sanos() == ["m-a", "m-b"] and L.eventos[-1]["tipo"] == "recuperacion"


def test_recuperacion_en_el_traductor_el_modelo_vuelve_a_la_rotacion(tmp_path):
    async def go():
        r = Reloj()
        L = _lim(r)
        t, tr = _trad(tmp_path, L, {"m-b": "5xx"})
        for i in range(40):
            await t.traducir([f"x{i}"], [i])
            if L.m["m-b"].cortado_hasta > r.t:
                break
        tr.fallas = {}                                  # m-b se arregla
        r.t = L.m["m-b"].cortado_hasta + 1              # vence el corte
        n0 = len(tr.llamadas)
        res = [await t.traducir([f"y{i}"], [100 + i]) for i in range(6)]
        return L, tr, res, n0
    L, tr, res, n0 = asyncio.run(go())
    assert all(x.ok for x in res)
    assert {m for m, _ in tr.llamadas[n0:]} == {"m-a", "m-b"}     # rota otra vez entre los dos
    assert [e["tipo"] for e in L.eventos] == ["corte", "reingreso", "recuperacion"]
    lineas = (tmp_path / "cuota-texto.log").read_text(encoding="utf-8").splitlines()
    assert [x.split(" | ")[3] for x in lineas if x.startswith("# cortacircuito")] == \
        ["corte", "reingreso", "recuperacion"]


# ---- ninguno sano: ok:false de inmediato, sin llamar, meta.reason "sin_modelo" ----
def test_ninguno_sano_ok_false_de_inmediato_sin_llamar_con_reason_sin_modelo(tmp_path):
    from contracts import errores
    from worker.contrato import mensaje
    r = Reloj()
    L = _lim(r)
    t, tr = _trad(tmp_path, L, {})
    for m in ("m-a", "m-b"):
        for _ in range(3):
            L.fallo(m, "5xx")
    assert L.sanos() == []
    t_falso, t0 = r.t, time.monotonic()
    res = asyncio.run(t.traducir(["a", "b"], [1, 2]))
    assert time.monotonic() - t0 < 0.5 and r.t == t_falso         # no espero nada
    assert tr.llamadas == []                                       # no llamo
    assert not res.ok and res.items == [{"seq": 1, "text": None, "ok": False},
                                        {"seq": 2, "text": None, "ok": False}]
    assert res.intentos == [{"modelo": None, "estado": "sin_modelo", "ms": 0}]
    assert res.reason == "sin_modelo" and t.n_sin_modelo == 1
    meta = meta_traduccion(res, "es", {"vivo": True, "intentos": res.intentos})
    assert meta["reason"] == "sin_modelo" and meta["model"] == "ninguno"
    m = mensaje("translation", "s", None, "en", items=res.items, meta=meta)
    assert errores(m) == []                                        # campo agregado: valida


def test_ninguno_sano_en_vivo_el_lote_sale_ya_marcado(tmp_path):
    async def go():
        L = Limitador(["m-a", "m-b"], log=None)
        t, tr = _trad(tmp_path, L, {})
        for m in ("m-a", "m-b"):
            for _ in range(3):
                L.fallo(m, "5xx")
        out = []
        t.on_resultado = out.append
        t.iniciar()
        t0 = time.monotonic()
        t.agregar(1, "a", time.time())
        t.agregar(2, "b", time.time())                 # lote lleno (2): sale ya
        while not out and time.monotonic() - t0 < 2:
            await asyncio.sleep(0.01)
        dt = time.monotonic() - t0
        await t.cerrar(0.5)
        return out, dt, tr
    out, dt, tr = asyncio.run(go())
    assert dt < 0.5 and tr.llamadas == []
    assert [i["seq"] for i in out[0].items] == [1, 2] and out[0].reason == "sin_modelo"


def test_con_los_dos_sanos_no_hay_reason_en_la_meta(tmp_path):
    L = _lim(Reloj())
    t, tr = _trad(tmp_path, L, {})
    res = asyncio.run(t.traducir(["a"], [1]))
    assert res.ok and res.reason is None and "reason" not in meta_traduccion(res, "es")


# ---- un solo modelo sano: lote 3 ventanas u 8 s, limitador 14 RPM, tope 15 en 60 s ----
def test_un_solo_modelo_sano_lotes_de_3_u_8s_y_limitador_14rpm(tmp_path):
    async def go():
        r = Reloj()
        L = _lim(r)
        assert L.modo() == (12, 2)
        for _ in range(3):
            L.fallo("m-b", "5xx")
        L.m["m-b"].cortado_hasta = float("inf")            # corte largo: solo cuenta m-a en el test
        assert L.modo() == (14, 1)
        t0, ts = r.t, []
        while r.t - t0 < 120:
            m = await L.tomar(espera_max=120)
            assert m == "m-a"
            if r.t - t0 < 120:
                ts.append(r.t)
        return L, ts
    L, ts = asyncio.run(go())
    assert len(ts) >= 27                  # 14 RPM (con 12 RPM y capacidad 2 serian 25 en 120 s)
    assert all(ts[i + 15] - ts[i] >= 60.0 - 1e-6 for i in range(len(ts) - 15))   # nunca 16 en 60 s

    r = Reloj()
    L = _lim(r)
    t, tr = _trad(tmp_path, L, {})
    assert (t.lotes.maximo, t.lotes.ventana_s) == (LOTE_MAX, LOTE_S)     # B8: defaults por env
    for _ in range(3):
        L.fallo("m-b", "5xx")
    t._ajustar_lotes()
    assert (t.lotes.maximo, t.lotes.ventana_s) == (3, 8.0) and t.lote_solo
    assert t.lotes.agregar(1, "a", 0.0) == [] and t.lotes.agregar(2, "b", 3.0) == []
    lote = t.lotes.agregar(3, "c", 6.0)
    assert [i.seq for i in lote[0].items] == [1, 2, 3] and lote[0].motivo == "lleno"
    t.lotes.agregar(4, "d", 10.0)
    assert t.lotes.vencido(17.9) is None and [i.seq for i in t.lotes.vencido(18.0).items] == [4]
    L.exito("m-b")                                          # vuelve el segundo modelo
    t._ajustar_lotes()
    assert (t.lotes.maximo, t.lotes.ventana_s) == (LOTE_MAX, LOTE_S)     # B8: defaults por env and not t.lote_solo


def test_tope_duro_15_en_60s_aunque_el_limitador_permita_mas():
    async def go():
        r = Reloj()
        L = Limitador(["m-a"], log=None, reloj=r, dormir=r.dormir, rpm_solo=600, capacidad_solo=50)
        t0, ts = r.t, []
        while r.t - t0 < 180:
            await L.tomar(espera_max=200)
            ts.append(r.t)
        return ts
    ts = asyncio.run(go())
    assert all(ts[i + 15] - ts[i] >= 60.0 - 1e-6 for i in range(len(ts) - 15))
    assert sum(1 for x in ts if x - ts[0] < 60) == 15
