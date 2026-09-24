"""Mapeo texto -> ventanas contra el flujo REAL del server grabado en el casete de 60 s.

El casete trae un turno fusionado por el server (un ACTIVITY_END que cubre varias ventanas):
el texto tiene que quedar con el rango de TODAS las ventanas cubiertas.
"""
from pathlib import Path

import pytest

from worker.casete import leer
from worker.rederivar import asignaciones_de, rederivar

RAIZ = Path(__file__).resolve().parents[2]
CASETE_60S = RAIZ / "fixtures" / "casetes" / "b1-en-60s.jsonl"


def test_asignaciones_contra_el_casete_real():
    cab, evs = leer(CASETE_60S)
    asig = asignaciones_de(evs)
    textos_server = [(e["payload"]["serverContent"]["inputTranscription"]["text"])
                     for e in evs if e["dir"] == "server"
                     and (e["payload"].get("serverContent") or {}).get("inputTranscription", {}).get("text")]
    assert [a.text for a in asig] == textos_server
    starts = [a.audio_start for a in asig]
    assert starts == sorted(starts), starts
    for a in asig:
        assert a.audio_end > a.audio_start
        assert a.t_captured <= a.t + 1e-6
    # hay al menos un turno fusionado por el server (visto en el casete)
    assert any(len(a.ventanas) > 1 for a in asig)


def test_rederivar_cumple_contrato(tmp_path):
    contracts = pytest.importorskip("contracts", reason="contracts/ de backend no disponible")
    out = tmp_path / "r.jsonl"
    r = rederivar(str(CASETE_60S), str(out))
    assert r["textos"] > 0
    cab, evs = leer(out)
    assert cab["derived_from"] == CASETE_60S.name
    emits = [e["payload"] for e in evs if e["dir"] == "emit"]
    assert all(not contracts.errores(m) for m in emits)
    # server y client identicos al original
    _, orig = leer(CASETE_60S)
    assert [e for e in orig if e["dir"] != "emit"] == [e for e in evs if e["dir"] != "emit"]
