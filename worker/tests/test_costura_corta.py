"""Dedup de FRASE CORTA en la costura de una reapertura (prueba con microfono 25/09 09:09).

Casete REAL fixtures/casetes/evidencia-25-09/prueba-mic-20260925-090921.jsonl: rotacion por atasco
(c1 -> c2, reenvio_ventanas [4, 5, 6, 7]); c2 emitio "Bueno, muy" (ventana 4) y despues c1, drenando,
emitio OTRA VEZ "Bueno, muy" (ventanas 2-4, cruza_corte). Tiene 2 palabras / 8 caracteres compactos:
no alcanza ni la contencion (>= 10) ni la tirada (>= 6 palabras). Se reinyecta con el mismo helper de
test_costura_contenido (sin API, sin texto inventado; todo sale rotulado replay:true).
"""
from pathlib import Path


from worker.mapeo import Asignacion
from worker.session import SessionWorker, _Conexion
from worker.tests.test_costura_contenido import _ocurrencias, reinyectar

MIC = (Path(__file__).resolve().parents[2] / "fixtures" / "casetes" / "evidencia-25-09"
       / "prueba-mic-20260925-090921.jsonl")


class _Bus:
    def __init__(self):
        self.msgs = []

    def publicar(self, m):
        self.msgs.append(m)


def test_mic_reapertura_bueno_muy_no_se_duplica():
    textos, w = reinyectar(MIC)
    assert _ocurrencias(textos, "Bueno, muy") == 1, [m["text"] for m in textos]
    # no se pierde el texto nuevo de c2
    assert any("buenos días. La propuesta" in m["text"] for m in textos)
    assert w.res.dedup_descartados >= 1
    # seq sigue creciente
    seqs = [m["seq"] for m in textos]
    assert seqs == sorted(seqs)


def test_corto_repetido_minimo_dos_palabras_y_borde_de_palabra():
    from worker.dedup import corto_repetido
    rec = ["Bueno, muy", "buenos días. La propuesta que tenemos"]
    assert corto_repetido("Bueno, muy", rec)              # igual normalizado (byte a byte del casete)
    assert corto_repetido("bueno   MUY!", rec)            # minusculas, sin puntuacion ni espacios repetidos
    assert corto_repetido("La propuesta", rec)            # contenido
    assert not corto_repetido("Bueno", rec)               # 1 palabra: muletilla suelta, no se toca
    assert not corto_repetido("uenos día", rec)           # no corta palabras al medio
    assert not corto_repetido("Muy bien", rec)            # distinto


def _worker():
    bus = _Bus()
    w = SessionWorker("t", "es", None, None, emisor=bus, reloj=lambda: 0.0, log=lambda s: None,
                      rotulo="test-costura-corta")
    w.con = _Conexion(1, None, 0.0)
    return w, bus


def _a(text, a0, a1):
    return Asignacion(t=0.0, text=text, audio_start=a0, audio_end=a1, t_captured=0.0, ventanas=[])


def test_fuera_de_costura_frase_corta_repetida_se_emite():
    w, bus = _worker()
    w._emitir_asignaciones([_a("Bueno, muy", 0.0, 2.0)], w.con)
    w._emitir_asignaciones([_a("Otra cosa distinta", 2.0, 4.0)], w.con)
    w._emitir_asignaciones([_a("Bueno, muy", 4.0, 6.0)], w.con)
    assert [m["text"] for m in bus.msgs if m["type"] == "text"].count("Bueno, muy") == 2


def test_en_costura_fuera_de_los_ultimos_20_s_no_se_descarta():
    w, bus = _worker()
    w._emitir_asignaciones([_a("Bueno, muy", 0.0, 2.0)], w.con)
    w._emitir_asignaciones([_a("Texto largo intermedio de la charla", 2.0, 30.0)], w.con)
    vieja = w.con
    vieja.corte_nueva = 30.0
    vieja.limite_audio_end = 30.45
    w.con = _Conexion(2, None, 0.0)
    w.viejas.append(vieja)
    w.res.rotaciones.append({"corte_s": 30.0, "pos_s": 32.0})
    # audio 28-30.2 (vieja, drenando): "Bueno, muy" de hace > 20 s no es la costura
    w._emitir_asignaciones([_a("Bueno, muy", 28.0, 30.2)], vieja)
    assert [m["text"] for m in bus.msgs if m["type"] == "text"].count("Bueno, muy") == 2
