"""Fan-out best-effort: un cliente muerto o colgado no frena a los vivos."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from hub.tests.util import levantar, productor, recibir, texto, ws_crudo

pytestmark = pytest.mark.asyncio

N_MSGS = 200
RELLENO = "relleno de prueba de carga " * 2300  # ~62 KB por mensaje: llena rapido los buffers TCP


async def test_cliente_muerto_y_colgado_no_frenan_al_vivo(http):
    h = await levantar(queue_max=10)
    try:
        sid = "sala-f"
        vivo = await http.ws_connect(f"{h.ws}/ws/{sid}?lang=es")
        assert json.loads((await vivo.receive(timeout=5)).data)["type"] == "init"
        # muerto: completa el handshake y corta el socket sin leer ni un frame
        _, w_muerto = await ws_crudo(h.port, f"/ws/{sid}?lang=es")
        # colgado: completa el handshake y despues NUNCA lee
        r_colgado, w_colgado = await ws_crudo(h.port, f"/ws/{sid}?lang=en")
        await asyncio.sleep(0.2)
        assert len(h.nucleo.sesiones[sid].clientes) == 3
        w_muerto.transport.abort()

        ws, _ = await productor(http, h)
        t_envio: dict[int, float] = {}
        tarea = asyncio.create_task(recibir(vivo, n=N_MSGS, timeout=15))
        t0 = time.monotonic()
        for n in range(1, N_MSGS + 1):
            t_envio[n] = time.monotonic()
            await ws.send_str(json.dumps(texto(sid, n, text=f"Mensaje de carga {n}. {RELLENO}")))
            if n % 20 == 0:
                await asyncio.sleep(0)  # deja correr al resto
        msgs = await tarea
        dur = time.monotonic() - t0
        await ws.close()

        seqs = [m["seq"] for m in msgs]
        assert seqs == list(range(1, N_MSGS + 1)), f"el vivo recibio {len(seqs)}/{N_MSGS}"
        s = h.nucleo.sesiones[sid]
        assert s.descartados_lentos >= 1, "el colgado deberia haber sido descartado por cola llena"
        assert len(s.clientes) == 1, f"quedan {len(s.clientes)} clientes (solo deberia quedar el vivo)"
        assert dur < 20, f"el vivo tardo {dur:.1f} s en recibir {N_MSGS} mensajes"
        print(f"\nvivo: {N_MSGS} mensajes de ~62 KB en {dur:.2f} s; descartados_lentos={s.descartados_lentos} "
              f"descartados_error={s.descartados_error}")
        w_colgado.transport.abort()
        await vivo.close()
    finally:
        await h.parar()


async def test_fanout_200_clientes_mismo_ultimo_seq():
    """C3 eje 2: 200 espectadores sobre una sesion + 10 muertos + 3 colgados: todos los vivos
    reciben 1..M en orden."""
    from hub.carga import correr
    from hub.tests.util import TOKEN

    h = await levantar()
    try:
        res = await correr(h.http, TOKEN, clientes=200, mensajes=30, intervalo=0.02,
                           muertos=10, colgados=3, sesion="carga-test", log=lambda *_: None)
        print(f"\ncarga: {res}")
        assert res["ok"], res
        assert res["ultimos_seq_distintos"] == [30]
        assert res["hub_max_clientes"] >= 200
    finally:
        await h.parar()
