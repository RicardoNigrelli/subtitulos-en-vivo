"""Reservas de texto POR KEY (FINAL 25/09). Sin API, 0 min. Limitador y Reservas reales con lock.

Hoy (antes): el tope de 12 llamadas/modelo/60 s se contaba por archivo de reservas = por maquina, sin
importar la key. Ahora cada linea lleva `key` (el NOMBRE de la variable) y el conteo es por (key, modelo).
La simulacion de N salas (tarda 90 s reales) es `python -m worker.tests.sim_salas_traductor`.
"""
import asyncio
import json

from worker.traductor import KEY_DEFAULT, Limitador, Reservas, Traductor

M = "gemini-3.5-flash-lite"


def test_linea_lleva_key_y_el_conteo_es_por_key_y_modelo(tmp_path):
    ruta = tmp_path / "reservas.jsonl"
    a = Reservas(ruta, key="GEMINI_API_KEY")
    b = Reservas(ruta, key="GEMINI_API_KEY_RESERVA")
    a2 = Reservas(ruta, key="GEMINI_API_KEY")            # otra sala con la MISMA key
    for _ in range(12):
        assert a.reservar(M, 12)
    assert not a.reservar(M, 12)                           # key A llena
    assert not a2.reservar(M, 12)                          # misma key: tambien limitada
    for _ in range(12):
        assert b.reservar(M, 12)                           # otra key: no la limita A
    assert not b.reservar(M, 12)
    assert a.reservar("otro-modelo", 12)                   # otro modelo: cuenta aparte
    lineas = [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines()]
    assert all("key" in d for d in lineas)
    assert {d["key"] for d in lineas} == {"GEMINI_API_KEY", "GEMINI_API_KEY_RESERVA"}
    assert a.contar(M) == 12 and b.contar(M) == 12
    txt = ruta.read_text(encoding="utf-8")
    assert "AIza" not in txt                               # solo el NOMBRE de la variable


def test_linea_vieja_sin_key_cuenta_como_la_principal(tmp_path):
    import time
    ruta = tmp_path / "r.jsonl"
    ruta.write_text("".join(json.dumps({"t": time.time(), "modelo": M, "pid": 1}) + "\n" for _ in range(5)),
                    encoding="utf-8")
    assert Reservas(ruta, key=KEY_DEFAULT).contar(M) == 5
    assert Reservas(ruta, key="GEMINI_API_KEY_RESERVA").contar(M) == 0


def test_traductor_toma_la_key_del_transporte(tmp_path):
    class T:
        nombre_key = "GEMINI_API_KEY_RESERVA"

        async def generar(self, modelo, prompt):
            return json.dumps(["x"])
    tr = Traductor("en", "es", transporte=T(), modelos=[M], log=tmp_path / "cuota-texto.log",
                   logger=lambda s: None)
    assert tr.key == "GEMINI_API_KEY_RESERVA" and tr.lim.reservas.key == "GEMINI_API_KEY_RESERVA"
    r = asyncio.run(tr.traducir(["hello"]))
    assert r.ok
    d = json.loads((tmp_path / "cuota-texto-reservas.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert d["key"] == "GEMINI_API_KEY_RESERVA"


def test_run_pasa_la_key_de_la_sala_al_traductor():
    from worker import run
    a = run._args(["--archivo", "x.wav", "--sesion", "s", "--lang", "en", "--key", "GEMINI_API_KEY_B"])
    t = run.armar_traductor(a)
    assert t.key == "GEMINI_API_KEY_B" and t.lim.reservas.key == "GEMINI_API_KEY_B"
