"""Doble (25/09): la reserva entre procesos del traductor NO frena el loop de asyncio.

El loop es el mismo que pauta la fuente (ingesta.py: espera = t0 + (idx+1)*0,1 - loop.time()) y
envia el audio: si algo bloquea N s, los chunks salen tarde y en rafaga. Se sostiene el lock de
reservas desde OTRO handle (como otro proceso) 1,2 s y se mide el hueco maximo de un latido de 10 ms
mientras el limitador intenta reservar.
- antes (camino sincronico `_reservar`, el de 215550c): el latido se frena ~1,2 s.
- despues (`elegir` -> `_reservar_async` -> asyncio.to_thread): hueco < 0,2 s y la reserva sale
  apenas se libera el lock.
"""
import asyncio
import threading
import time

from worker.traductor import Limitador, LockArchivo, Reservas

SOSTENER_S = 1.2


def _sostener_lock(ruta_lock, listo: threading.Event, dur: float):
    with LockArchivo(ruta_lock):
        listo.set()
        time.sleep(dur)


async def _latido(parar: asyncio.Event, huecos: list):
    t = time.monotonic()
    while not parar.is_set():
        await asyncio.sleep(0.01)
        ahora = time.monotonic()
        huecos.append(ahora - t)
        t = ahora


async def _medir(tmp_path, usar_async: bool):
    res = Reservas(tmp_path / "cuota-texto-reservas.jsonl", ventana_s=60.0, etiqueta="test")
    L = Limitador(["m-a"], log=None, rpm=10, capacidad=10, tope_rpm=1000, reservas=res)
    listo = threading.Event()
    hilo = threading.Thread(target=_sostener_lock, args=(res.lock, listo, SOSTENER_S))
    hilo.start()
    assert listo.wait(5)
    parar, huecos = asyncio.Event(), []
    lat = asyncio.create_task(_latido(parar, huecos))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    if usar_async:
        modelo, motivo = await L.elegir(espera_max=5.0)
    else:
        ok = L._reservar("m-a", 10)          # camino de 215550c: LockArchivo con time.sleep en el loop
        modelo, motivo = ("m-a" if ok else None), "sync"
    dur = time.monotonic() - t0
    await asyncio.sleep(0.05)
    parar.set()
    await lat
    hilo.join()
    return max(huecos), dur, modelo, motivo


def test_antes_reserva_sincronica_frena_el_loop(tmp_path):
    hueco, dur, modelo, _ = asyncio.run(_medir(tmp_path, usar_async=False))
    assert modelo == "m-a"
    assert hueco >= SOSTENER_S * 0.7, hueco        # el latido estuvo frenado mientras esperaba el lock


def test_despues_reserva_en_hilo_no_frena_el_loop(tmp_path):
    hueco, dur, modelo, motivo = asyncio.run(_medir(tmp_path, usar_async=True))
    assert (modelo, motivo) == ("m-a", "ok")
    assert dur >= SOSTENER_S * 0.7                # espero al lock (no lo salteo)...
    assert hueco < 0.2, hueco                     # ...sin frenar el loop
