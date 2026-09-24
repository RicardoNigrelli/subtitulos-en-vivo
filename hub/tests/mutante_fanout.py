"""MUTANTE: fan-out SERIAL, un solo enviador que espera a cada cliente en orden.
Si el test del repo tiene dientes, con este hub tiene que FALLAR (exit 1).
No se llama test_*.py a proposito: la suite no lo junta. Se corre a mano:
    python -m pytest hub/tests/mutante_fanout.py -q    ->  1 failed, exit 1 (esperado)
"""
import asyncio
import aiohttp
import pytest
import pytest_asyncio
from hub import nucleo
from hub.tests import test_fanout


@pytest_asyncio.fixture
async def http():
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture(autouse=True)
def mutar(monkeypatch):
    def difundir(self, s, data):
        cola = getattr(self, "_cola_serial", None)
        if cola is None:
            self._cola_serial = cola = asyncio.Queue()
            async def consumidor():
                while True:
                    s_, d_ = await cola.get()
                    for c in list(s_.clientes):
                        try:
                            await c.ws.send_str(d_)
                        except Exception:
                            pass
            self._t_serial = asyncio.ensure_future(consumidor())
        cola.put_nowait((s, data))
    monkeypatch.setattr(nucleo.Hub, "difundir", difundir)


test_mutante = pytest.mark.asyncio(test_fanout.test_cliente_muerto_y_colgado_no_frenan_al_vivo)
