"""Pedido cubierto ("hedge") del traductor (FINAL 25/09). Sin API, 0 min: transporte FALSO rotulado.

- Lote lento: 9 s en el modelo A, 1 s en el B, TRADUCTOR_HEDGE_S=4 => resultado en ~5 s con el texto de
  B, UNA sola emision, 2 llamadas (A cancelada), contadores correctos.
- hedge_s=0 => como antes: 9 s, una sola llamada.
- Sin modelo alterno sano => no se cubre.
- p95 simulado antes/despues con 10 % de lotes lentos (escala de tiempo 0,1: 1 s simulado = 0,1 s real).
"""
import asyncio
import json
import random
import time

from worker.traductor import Limitador, Lotes, Traductor

A, B = "modelo-a", "modelo-b"


class TransporteFalso:
    """[SIM] NO es Gemini: devuelve '[SIM] <x>' tras una demora por (lote, modelo)."""

    def __init__(self, demora):
        self.demora = demora            # f(textos, modelo) -> s
        self.llamadas = []
        self.canceladas = 0

    async def generar(self, modelo, prompt):
        arr = json.loads(prompt.rsplit("Input:\n", 1)[1])
        self.llamadas.append(modelo)
        try:
            await asyncio.sleep(self.demora(arr, modelo))
        except asyncio.CancelledError:
            self.canceladas += 1
            raise
        return json.dumps(["[SIM] " + modelo + " " + x for x in arr])


def _traductor(tr, hedge_s, modelos=(A, B), **kw):
    lim = Limitador(list(modelos), rpm=1e6, capacidad=1e6, tope_rpm=10 ** 9, rpd_tope=10 ** 9, log=None)
    return Traductor("en", "es", transporte=tr, modelos=list(modelos), limitador=lim, log=None,
                     timeout_s=20.0, hedge_s=hedge_s, logger=lambda s: None, **kw)


def _un_lote(hedge_s, modelos=(A, B)):
    tr = TransporteFalso(lambda arr, m: 9.0 if m == A else 1.0)
    emisiones = []
    t = _traductor(tr, hedge_s, modelos, lotes=Lotes(maximo=1, ventana_s=60),
                   on_resultado=lambda r: emisiones.append((time.monotonic(), r)))

    async def main():
        t0 = time.monotonic()
        t.agregar(1, "hello", time.time())
        await t.cerrar(timeout=30)
        return t0
    t0 = asyncio.run(main())
    return t, tr, emisiones, t0


def test_lote_lento_se_cubre_y_llega_en_5_s():
    t, tr, emisiones, t0 = _un_lote(4.0)
    assert len(emisiones) == 1                           # una sola emision
    dt, res = emisiones[0][0] - t0, emisiones[0][1]
    assert 4.8 <= dt <= 5.8, dt                          # 4 s de espera + 1 s de B
    assert res.ok and res.modelo == B and res.items[0]["text"] == "[SIM] modelo-b hello"
    assert tr.llamadas == [A, B] and tr.canceladas == 1  # A cancelada
    assert t.n_cubiertos == 1 and t.n_cubiertos_ganados == 1 and t.n_cancelados == 1
    assert t.n_llamadas == 2 and t.por_modelo == {A: 1, B: 1} and t.n_ok_false == 0
    assert [i["estado"] for i in res.intentos] == ["cancelado", "ok"]


def test_hedge_0_queda_como_antes():
    t, tr, emisiones, t0 = _un_lote(0.0)
    assert len(emisiones) == 1
    dt, res = emisiones[0][0] - t0, emisiones[0][1]
    assert 8.8 <= dt <= 9.8, dt
    assert res.ok and res.modelo == A and tr.llamadas == [A]
    assert t.n_cubiertos == 0 and t.n_llamadas == 1


def test_sin_alterno_sano_no_se_cubre():
    t, tr, emisiones, t0 = _un_lote(4.0, modelos=(A,))
    assert tr.llamadas == [A] and t.n_cubiertos == 0
    assert 8.8 <= emisiones[0][0] - t0 <= 9.8


def _p(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def test_p95_simulado_con_10_por_ciento_de_lotes_lentos(capsys):
    ESC = 0.1                              # 1 s simulado = 0,1 s real
    rnd = random.Random(25)
    n = 200
    # demora por (lote, modelo): 90 % rapida 1-3 s; 10 % lenta 9-15 s (la cola de 5xx/timeout)
    tabla = {(i, m): (rnd.uniform(9, 15) if rnd.random() < 0.10 else rnd.uniform(1, 3))
             for i in range(n) for m in (A, B)}

    def correr(hedge_s):
        tr = TransporteFalso(lambda arr, m: tabla[(int(arr[0][1:]), m)] * ESC)
        t = _traductor(tr, hedge_s * ESC)

        async def main():
            async def uno(i):
                t0 = time.monotonic()
                r = await t.traducir([f"L{i}"], [i])
                assert r.ok
                return (time.monotonic() - t0) / ESC
            return await asyncio.gather(*(uno(i) for i in range(n)))
        dts = asyncio.run(main())
        return dts, t
    antes, t0 = correr(0.0)
    despues, t1 = correr(4.0)
    r = {"n_lotes": n, "lentos_frac_por_llamada": 0.10,
         "antes": {"p50": round(_p(antes, .5), 2), "p95": round(_p(antes, .95), 2), "max": round(max(antes), 2),
                   "llamadas": t0.n_llamadas},
         "despues_hedge_4s": {"p50": round(_p(despues, .5), 2), "p95": round(_p(despues, .95), 2),
                              "max": round(max(despues), 2), "llamadas": t1.n_llamadas,
                              "cubiertos": t1.n_cubiertos, "cubiertos_ganados": t1.n_cubiertos_ganados,
                              "cancelados": t1.n_cancelados}}
    print("HEDGE_SIM " + json.dumps(r))
    assert r["antes"]["p95"] >= 9.0
    assert r["despues_hedge_4s"]["p95"] <= 7.5
    assert abs(r["despues_hedge_4s"]["p50"] - r["antes"]["p50"]) <= 0.5      # la mediana no cambia
    assert t1.n_llamadas == n + t1.n_cubiertos                                # 1 llamada extra por cubierto
    assert t1.n_cubiertos <= 0.15 * n
