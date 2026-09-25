"""B8: el limitador del traductor cuenta llamadas INICIADAS por TODOS los procesos (reservas con lock).

Dos procesos reales (subprocess) compiten por el mismo archivo de reservas con una ventana corta
(2 s en vez de 60 s, para que el test dure segundos). Se verifica sobre el archivo: ninguna ventana
de 2 s tiene mas reservas por modelo que el limite, y los dos procesos reservaron.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

HIJO_RESERVAS = r"""
import sys, time
from pathlib import Path
from worker.traductor import Reservas
ruta, inicio, dur, etiqueta = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
r = Reservas(ruta, ventana_s=2.0, etiqueta=etiqueta)
while time.time() < inicio:
    time.sleep(0.005)
ok = 0
while time.time() < inicio + dur:
    ok += r.reservar("m-a", 5)
    time.sleep(0.01)
print(ok)
"""

HIJO_LIMITADOR = r"""
import asyncio, sys, time
from pathlib import Path
from worker.traductor import Limitador, Reservas
ruta, inicio, dur, etiqueta = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
async def main():
    L = Limitador(["m-a", "m-b"], log=None, rpm=4, capacidad=100, tope_rpm=1000,
                  reservas=Reservas(ruta, ventana_s=2.0, etiqueta=etiqueta))
    while time.time() < inicio:
        await asyncio.sleep(0.005)
    n = {"m-a": 0, "m-b": 0}
    while time.time() < inicio + dur:
        m, _motivo = await L.elegir(espera_max=0.05)
        if m:
            n[m] += 1
        await asyncio.sleep(0.01)
    print(n["m-a"] + n["m-b"])
asyncio.run(main())
"""


def _correr_dos(tmp_path, codigo, dur=3.0):
    ruta = tmp_path / "cuota-texto-reservas.jsonl"
    env = dict(os.environ, PYTHONPATH=str(RAIZ))
    inicio = time.time() + 1.5
    ps = [subprocess.Popen([sys.executable, "-c", codigo, str(ruta), f"{inicio:.3f}", str(dur), f"p{i}"],
                           cwd=RAIZ, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
          for i in range(2)]
    outs = [p.communicate(timeout=60) for p in ps]
    for p, (o, e) in zip(ps, outs):
        assert p.returncode == 0, e
    filas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
    return filas, [int(o.strip()) for o, _ in outs]


def _max_en_ventana(filas, modelo, ventana_s=2.0):
    ts = sorted(f["t"] for f in filas if f["modelo"] == modelo)
    return max((sum(1 for u in ts if t - ventana_s < u <= t) for t in ts), default=0)


def test_dos_procesos_no_pasan_el_limite_compartido(tmp_path):
    filas, oks = _correr_dos(tmp_path, HIJO_RESERVAS)
    assert sum(oks) == len(filas)                      # cada reserva aceptada quedo escrita
    assert len({f["pid"] for f in filas}) == 2         # reservaron los DOS procesos
    assert _max_en_ventana(filas, "m-a") <= 5          # nunca mas de 5 en 2 s, sumando procesos
    assert len(filas) >= 10                            # y el limite se usa (no es un candado mudo)


def test_limitador_de_dos_procesos_cuenta_llamadas_iniciadas_de_ambos(tmp_path):
    filas, oks = _correr_dos(tmp_path, HIJO_LIMITADOR)
    assert sum(oks) == len(filas)
    assert len({f["pid"] for f in filas}) == 2
    for m in ("m-a", "m-b"):                           # rpm=4 -> limite 4 por modelo en la ventana
        assert _max_en_ventana(filas, m) <= 4, m
    assert {f["modelo"] for f in filas} == {"m-a", "m-b"}


def test_sin_log_no_hay_reservas():
    from worker.traductor import Limitador
    assert Limitador(["m-a"], log=None).reservas is None


def test_log_propio_reserva_al_lado(tmp_path):
    from worker.traductor import Limitador
    L = Limitador(["m-a"], log=tmp_path / "cuota-texto.log")
    assert L.reservas.ruta == tmp_path / "cuota-texto-reservas.jsonl"
