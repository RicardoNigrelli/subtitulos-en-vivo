"""Traduccion INMEDIATA ADAPTATIVA (LATENCIA 25/09). Sin API, 0 min.

RELOJ FALSO + codigo real: Traductor (niveles inmediato / lote 2-4 s / lote 3-8 s), Limitador (token
bucket 12 RPM por modelo, 2 modelos, preferencia 3.5 primero) y Reservas con lock en UN archivo
compartido por las salas de la misma key. El "modelo" es un transporte FALSO rotulado [SIM] que responde
en 1,2 s de reloj falso (p50 medido de 3.5-flash-lite con lote de 2, reportes/cuota-texto.log 25/09).
Cadencia de textos: la del casete real fixtures/casetes/b3-ab-manual.jsonl en bucle, desfasada por sala.

Compara, sobre la MISMA cadencia, TRADUCTOR_MODO=lote (lo de hoy) contra inmediato:
- 1 sala por key: la mayoria (~60 %) sale inmediato y la demora texto->traduccion p50 baja 3,5 -> 1,4 s.
- 3 salas por key: degrada a lotes, nunca pasa de 12 reservas por (key, modelo) en 60 s y la cobertura
  no baja respecto de lote.
"""
import asyncio
import json

from worker.tests.sim_salas_traductor import cadencia
from worker.traductor import Limitador, Reservas, Traductor

MODELOS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
DEMORA_S = 1.2


class Reloj:
    def __init__(self):
        self.t = 1_758_800_000.0

    def __call__(self):
        return self.t

    async def dormir(self, s):
        fin = self.t + s
        while self.t < fin - 1e-9:
            await asyncio.sleep(0)


class ModeloSimulado:
    """[SIM] NO es Gemini: devuelve '[SIM] ' + cada fragmento tras DEMORA_S de reloj falso."""

    def __init__(self, reloj):
        self.reloj = reloj

    async def generar(self, modelo, prompt):
        await self.reloj.dormir(DEMORA_S)
        arr = json.loads(prompt.rsplit("Input:\n", 1)[1])
        return json.dumps(["[SIM] " + x for x in arr], ensure_ascii=False)


def _p(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


async def _simular(tmp_path, modo, salas, duracion=180.0, paso=0.1):
    reloj = Reloj()
    ruta = tmp_path / f"reservas-{modo}-{salas}.jsonl"
    cad = cadencia()
    vuelta = cad[-1][0] + 2.4
    trs, regs = [], []
    for i in range(salas):
        reg = {"t_texto": {}, "res": {}}

        def on_res(res, reg=reg):
            for it in res.items:
                reg["res"].setdefault(it["seq"], (it["ok"], reloj.t))
        lim = Limitador(MODELOS, log=None, reloj=reloj, dormir=reloj.dormir,
                        reservas=Reservas(ruta, reloj=reloj, key="KEY_0"), key="KEY_0", rotar=False)
        tr = Traductor("en", "es", transporte=ModeloSimulado(reloj), modelos=MODELOS, limitador=lim,
                       log=None, hedge_s=0, modo=modo, reloj=reloj, on_resultado=on_res,
                       logger=lambda s: None, key="KEY_0")
        trs.append(tr)
        regs.append(reg)
    agenda = []                                  # (t, sala, seq, texto)
    for i in range(salas):
        k, seq = 0, 0
        while True:
            v, j = divmod(k, len(cad))
            t = 0.9 * i + v * vuelta + cad[j][0]
            if t > duracion:
                break
            seq += 1
            agenda.append((t, i, seq, cad[j][1]))
            k += 1
    agenda.sort()
    t0, idx, n_pasos = reloj.t, 0, int((duracion + 60.0) / paso)
    for n in range(n_pasos):
        reloj.t = t0 + n * paso
        while idx < len(agenda) and t0 + agenda[idx][0] <= reloj.t:
            _t, i, seq, txt = agenda[idx]
            regs[i]["t_texto"][seq] = reloj.t
            trs[i].agregar(seq, txt, reloj.t)
            idx += 1
        for tr in trs:
            if n % 2 == 0 and tr.modo == "inmediato":
                tr.actualizar_carga()            # el vigia real lo hace cada 0,2 s en un hilo
            tr.tic(reloj.t)
            if reloj.t - t0 >= duracion + 1.0:
                lote = tr.lotes.vaciar(reloj.t)
                if lote:
                    tr._poner(lote)
        for _ in range(30):
            await asyncio.sleep(0)
    for tr in trs:                               # nada en vuelo al final
        assert not tr._vuelo, (modo, salas, len(tr._vuelo))
    total = sum(len(r["t_texto"]) for r in regs)
    oks = [(r["res"][s][1] - r["t_texto"][s]) for r in regs for s in r["t_texto"]
           if s in r["res"] and r["res"][s][0]]
    reservas = [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip()]
    maxv = 0
    for m in MODELOS:
        ts = sorted(d["t"] for d in reservas if d["modelo"] == m)
        j = 0
        for i2, t in enumerate(ts):
            while ts[j] <= t - 60.0:
                j += 1
            maxv = max(maxv, i2 - j + 1)
    niveles = {}
    for tr in trs:
        for k, v in tr.textos_por_nivel.items():
            niveles[k] = niveles.get(k, 0) + v
    return {"modo": modo, "salas": salas, "textos": total, "ok": len(oks),
            "cobertura": len(oks) / total, "p50": _p(oks, .5), "p95": _p(oks, .95),
            "llamadas": sum(tr.n_llamadas for tr in trs), "max_60s": maxv, "niveles": niveles}


def _correr(tmp_path, modo, salas):
    r = asyncio.run(_simular(tmp_path, modo, salas))
    print(json.dumps(r))
    return r


def test_una_sala_por_key_casi_todo_inmediato_y_mas_rapido(tmp_path):
    lote = _correr(tmp_path, "lote", 1)
    inm = _correr(tmp_path, "inmediato", 1)
    # umbrales 6/9: ~60 % sale solo y ya (con 9/11 era ~85 % pero 3 salas perdian cobertura)
    assert inm["niveles"].get("inmediato", 0) >= 0.55 * inm["textos"], inm
    assert inm["cobertura"] == 1.0 and lote["cobertura"] == 1.0, (inm, lote)
    assert inm["p50"] < lote["p50"] - 1.0, (inm, lote)     # sin la espera del lote
    assert inm["max_60s"] <= 12, inm


def test_tres_salas_por_key_degrada_sin_pasar_el_tope_ni_bajar_cobertura(tmp_path):
    lote = _correr(tmp_path, "lote", 3)
    inm = _correr(tmp_path, "inmediato", 3)
    assert inm["max_60s"] <= 12 and lote["max_60s"] <= 12, (inm, lote)
    assert set(inm["niveles"]) - {"inmediato"}, inm          # degrado a lotes
    assert inm["cobertura"] >= lote["cobertura"], (inm, lote)


def test_modo_lote_no_cambia_lo_de_hoy(tmp_path):
    tr = Traductor("en", "es", transporte=ModeloSimulado(Reloj()), modelos=MODELOS, log=None,
                   modo="lote", logger=lambda s: None)
    tr._ajustar_lotes()
    assert (tr.lotes.maximo, tr.lotes.ventana_s) == (2, 4.0) and tr.nivel == "lote"


def test_modo_invalido_falla_fuerte():
    import pytest
    with pytest.raises(ValueError):
        Traductor("en", "es", transporte=ModeloSimulado(Reloj()), modelos=MODELOS, log=None,
                  modo="rapido", logger=lambda s: None)
