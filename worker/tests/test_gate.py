"""Compuerta (qa/out/gate/, 25/09): casete REAL gate-es (qa/out/smoke-gate-gate-es.casete.jsonl, copiado sin
editar a fixtures/casetes/gate-es.jsonl). Sin API.

1. DUPLICADO: tras la rotacion por atasco, seq 3 (vieja, 0,0-28,8) y seq 4 (nueva, 14,5-41,8) comparten 19
   palabras seguidas EN EL MEDIO (bordes distintos: "sereste"/"este", "ahípodemos"/"ahíposiblemente").
   worker/dedup.py::tiradas_repetidas las quita del texto entrante (>= 6 palabras) en la costura.
2. ARRANQUE LENTO: ATASCO_ARRANQUE_S (apagado por defecto) en replay: conexion vieja = lineas REALES de c1
   de gate-es (el server no cerro el primer turno hasta +39,7 s); nuevas = sesion sana del mismo clip
   (b8-trad-vivo-es). Es un modelo: NO dice nada de la mejora en vivo.
"""
import unicodedata
from pathlib import Path

import pytest

import worker.session as S
from worker.dedup import MIN_TIRADA, _palabras, _tirada_mas_larga
from worker.session import ConfigReabrir
from worker.tests.test_costura_contenido import reinyectar

CASETES = Path(__file__).resolve().parents[2] / "fixtures" / "casetes"
GATE_ES = CASETES / "gate-es.jsonl"
VF_EN = CASETES / "vf-smoke-final-en.jsonl"
VF_ES = CASETES / "vf-smoke-final-es.jsonl"
B5R = CASETES / "b5r-en-traducciones-defectuosas.jsonl"


def _tiradas(textos, minimo=MIN_TIRADA):
    """(seq_a, seq_b, largo) de cada par de textos emitidos con una tirada comun >= minimo palabras."""
    toks = [(m["seq"], [w for w, _, _ in _palabras(m["text"])]) for m in textos]
    out = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            largo, _ = _tirada_mas_larga(toks[j][1], toks[i][1])
            if largo >= minimo:
                out.append((toks[i][0], toks[j][0], largo))
    return out


def _compacto(s):
    return "".join(c for c in unicodedata.normalize("NFC", s or "").casefold() if c.isalnum())


def _perdidas(antes, despues):
    """Palabras de `antes` que no aparecen en `despues` ni siquiera pegadas a otra (tokens fusionados)."""
    pal_d = {w for m in despues for w, _, _ in _palabras(m["text"])}
    todo_d = "".join(_compacto(m["text"]) for m in despues)
    return {w for m in antes for w, _, _ in _palabras(m["text"]) if w not in pal_d and _compacto(w) not in todo_d}


def _sin_tiradas(monkeypatch):
    monkeypatch.setattr(S, "tiradas_repetidas", lambda nuevo, recientes, *a, **k: None)


def test_gate_es_tirada_de_19_palabras_antes_y_0_despues(monkeypatch):
    despues, _ = reinyectar(GATE_ES)
    with monkeypatch.context() as m:
        _sin_tiradas(m)
        antes, _ = reinyectar(GATE_ES)
    # el defecto de la compuerta, reproducido (la reinyeccion numera solo los textos: seq 3/4 reales = 1/2)
    assert [t[2] for t in _tiradas(antes)] == [19], _tiradas(antes)
    assert _tiradas(despues) == []                             # 0 tiradas >= 6 palabras repetidas
    assert _perdidas(antes, despues) == set()                  # lo quitado sigue en el otro texto
    assert len(despues) == len(antes)                          # ningun texto entero desaparecio


@pytest.mark.parametrize("casete", [VF_EN, VF_ES, B5R], ids=["vf-en", "vf-es", "b5r"])
def test_otros_casetes_reales_no_pierden_palabras(monkeypatch, casete):
    despues, _ = reinyectar(casete)
    with monkeypatch.context() as m:
        _sin_tiradas(m)
        antes, _ = reinyectar(casete)
    assert _perdidas(antes, despues) == set()
    assert _tiradas(despues) == []


def _arranque(cfg: ConfigReabrir, tmp_path, nombre):
    from worker.umbrales import FabricaCompuesta, correr, solo_conexion
    from worker.transporte_casete import FabricaCasete
    c1 = solo_conexion(GATE_ES, "c1", tmp_path / "gate-es-solo-c1.jsonl")
    fab = FabricaCompuesta(FabricaCasete(str(c1)), FabricaCasete(str(CASETES / "b8-trad-vivo-es.jsonl")))
    res, msgs, _ = correr(c1, 60.0, cfg, tmp_path / f"arranque-{nombre}.jsonl", fab=fab)
    textos = [m for m in msgs if m["type"] == "text"]
    return res, textos


def test_arranque_apagado_por_defecto_y_en_replay_reabre_antes_sin_perder_audio(tmp_path):
    assert ConfigReabrir().arranque_s == 0.0 and ConfigReabrir.desde_env().arranque_s == 0.0
    res0, t0 = _arranque(ConfigReabrir(), tmp_path, "default")
    res12, t12 = _arranque(ConfigReabrir(arranque_s=12.0), tmp_path, "12")
    r0, r12 = res0.rotaciones[0], res12.rotaciones[0]
    print("default:", r0["detalle"], r0["pos_s"], r0["audio_lost_s"], "| 12:", r12["detalle"], r12["pos_s"],
          r12["audio_lost_s"], "| 1er texto pos:", t0[0]["audio_start"] if t0 else None, t12[0]["audio_start"] if t12 else None)
    assert r0["detalle"] != "arranque"                         # apagado: no dispara
    assert r12["detalle"] == "arranque" and r12["pos_s"] < r0["pos_s"]
    assert r12["audio_lost_s"] <= r0["audio_lost_s"]
