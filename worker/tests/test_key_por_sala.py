"""Key por sala (25/09): `--key X` llega a la Live API Y al traductor de texto. Sin API.

Se arma todo por el MISMO camino que `worker.run.correr()`: `_args(argv)` -> `armar_transportes(a,
vocab)` (conexion inicial y fabrica de rotaciones) y `armar_traductor(a)`. Nada se conecta: los
constructores solo guardan el NOMBRE de la variable de la key.
"""
from worker import run
from worker.traductor import TransporteGenAI
from worker.transporte import TransporteGemini


def _a(*extra):
    return run._args(["--archivo", "x.wav", "--sesion", "t-key", "--lang", "en", *extra])


def test_key_x_llega_a_live_rotaciones_y_texto():
    a = _a("--key", "GEMINI_API_KEY_B")
    tr, fabrica = run.armar_transportes(a, ["Nerdearla"])
    assert isinstance(tr, TransporteGemini) and tr.nombre_key == "GEMINI_API_KEY_B"
    nueva = fabrica(42.0)                                   # la conexion de una rotacion
    assert isinstance(nueva, TransporteGemini) and nueva.nombre_key == "GEMINI_API_KEY_B"
    t = run.armar_traductor(a)
    assert isinstance(t.tr, TransporteGenAI) and t.tr.nombre_key == "GEMINI_API_KEY_B"
    assert (t.de, t.a) == ("en", "es")


def test_sin_key_las_dos_usan_la_default():
    a = _a("--lang", "es")
    tr, _ = run.armar_transportes(a, [])
    assert tr.nombre_key == "GEMINI_API_KEY"
    t = run.armar_traductor(a)
    assert t.tr.nombre_key == "GEMINI_API_KEY" and (t.de, t.a) == ("es", "en")


def test_traducir_none_no_arma_traductor():
    assert run.armar_traductor(_a("--traducir-a", "none")) is None
