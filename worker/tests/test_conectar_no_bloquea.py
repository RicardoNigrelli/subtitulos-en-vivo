"""Conectar (25/09): crear el cliente de Gemini NO frena el loop en una rotacion. Sin API.

Rotacion SIMULADA con el transporte de casete (worker/transporte_casete.py, rotulado test): el
casete EN de B1 con la conexion muda desde 20 s -> el watchdog pide reabrir ("mudo"/"atraso") y la
sala abre c2. Cada conexion hace, antes del conectar() del casete, el MISMO paso que
TransporteGemini.conectar(): crear el cliente genai, con `worker.gemini.cliente` reemplazado por uno
LENTO (time.sleep 1,0 s; medido real: 3,2-3,3 s con carga). Un latido de 10 ms mide el loop.
- antes (215550c: `c = cliente(...)` sincronico en el loop): el loop se congela >= 1 s en CADA
  conexion, tambien en la rotacion.
- despues (`await transporte.crear_cliente(...)` -> asyncio.to_thread): ningun hueco >= 0,5 s.
"""
import asyncio
import time

import worker.gemini as gemini
from worker import transporte
from worker.ingesta import trocear
from worker.session import ConfigReabrir, SessionWorker
from worker.tests.test_reabrir import EN, _Bus, _voz
from worker.transporte_casete import FabricaCasete

LENTO_S = 1.0


def _cliente_lento(nombre_key="GEMINI_API_KEY"):
    time.sleep(LENTO_S)                  # httpx + contexto SSL + bundle de CA (bloqueante)
    return object()


class _ConCliente:
    """Transporte de casete + el paso de TransporteGemini.conectar() que crea el cliente."""

    def __init__(self, tr, modo: str):
        self._tr = tr
        self._modo = modo

    def __getattr__(self, k):
        return getattr(self._tr, k)

    async def conectar(self) -> dict:
        if self._modo == "antes":
            gemini.cliente("GEMINI_API_KEY")                 # 215550c, verbatim: en el loop
        else:
            await transporte.crear_cliente("GEMINI_API_KEY")  # camino real de hoy
        return await self._tr.conectar()


def _correr(modo: str, monkeypatch, dur_s: float = 45.0, mudo_desde: float = 20.0):
    monkeypatch.setattr(gemini, "cliente", _cliente_lento)
    fab = FabricaCasete(str(EN), mudo_desde)
    fabrica = lambda corte_s: _ConCliente(fab(corte_s), modo)
    bus = _Bus()
    chs = list(trocear(_voz(dur_s), t0_epoch=0.0))
    caja, huecos = {}, []
    cfg = ConfigReabrir()
    cfg.drenaje_vieja_s, cfg.tick_s = 0.3, 0.05

    async def fuente():
        for c in chs:
            await asyncio.sleep(0)       # el loop gira en cada chunk (como la fuente real)
            yield c

    async def dormir(_s):
        await asyncio.sleep(0)

    async def latido(fin: asyncio.Event):
        t = time.monotonic()
        while not fin.is_set():
            await asyncio.sleep(0.01)
            a = time.monotonic()
            huecos.append(a - t)
            t = a

    async def main():
        fin = asyncio.Event()
        lat = asyncio.create_task(latido(fin))
        await asyncio.sleep(0.02)
        w = SessionWorker("sala-conectar", "en", fabrica(0.0), fuente(), emisor=bus,
                          heartbeat_s=10_000, espera_final_s=0.3, reloj=lambda: caja["w"].pos_s,
                          dormir=dormir, log=lambda s: None, fabrica=fabrica, reabrir=cfg)
        caja["w"] = w
        res = await w.correr()
        fin.set()
        await lat
        return res

    res = asyncio.run(main())
    rot = [m for m in bus.msgs if m["type"] == "rotation"]
    return res, rot, fab.n, huecos


def test_antes_crear_cliente_en_el_loop_lo_congela_en_la_rotacion(monkeypatch):
    res, rot, conexiones, huecos = _correr("antes", monkeypatch)
    assert rot and conexiones >= 2, (len(rot), conexiones)
    congelados = [h for h in huecos if h >= LENTO_S * 0.9]
    assert len(congelados) >= conexiones, (congelados, conexiones)   # c1 Y cada rotacion


def test_despues_crear_cliente_en_hilo_no_congela_el_loop(monkeypatch):
    res, rot, conexiones, huecos = _correr("despues", monkeypatch)
    assert rot and conexiones >= 2, (len(rot), conexiones)
    assert max(huecos) < 0.5, max(huecos)
