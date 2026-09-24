"""Casete JSONL: round-trip de lineas, flush por linea, y el casete real de B1 cumple el contrato."""
import json
from pathlib import Path

import pytest

from worker.casete import Grabador, kind_server, leer, textos_server

RAIZ = Path(__file__).resolve().parents[2]
CASETE_60S = RAIZ / "fixtures" / "casetes" / "b1-en-60s.jsonl"


def test_round_trip(tmp_path):
    p = tmp_path / "c.jsonl"
    g = Grabador(p, {"session_id": "s1", "lang": "en", "model": "m", "config": {"a": 1},
                     "source": {"file": "x.wav", "url": None, "start_s": 0}, "started_at": 100.0})
    srv = {"serverContent": {"inputTranscription": {"text": "ñandú \"comillas\""}}}
    g.server(kind_server(srv), srv, t=101.0)
    g.client("activity_start", {"ventana": 0, "audio_start": 0.0}, t=100.5)
    msg = {"v": 1, "type": "text", "session_id": "s1", "seq": 1, "text": "hola"}
    g.emit(msg, t=102.0, ventana=0)
    # flush por linea: se puede leer antes de cerrar
    cab, evs = leer(p)
    assert len(evs) == 3
    g.cerrar()
    cab, evs = leer(p)
    assert cab == {"casete": 1, "session_id": "s1", "lang": "en", "model": "m", "config": {"a": 1},
                   "source": {"file": "x.wav", "url": None, "start_s": 0}, "started_at": 100.0}
    assert evs[0] == {"t": 101.0, "dir": "server", "kind": "serverContent", "payload": srv}
    assert evs[1]["dir"] == "client" and evs[1]["kind"] == "activity_start"
    assert evs[2] == {"t": 102.0, "dir": "emit", "kind": "text", "payload": msg, "ventana": 0}
    # formato de linea: json.dumps por defecto (grep '"dir": "server"' funciona)
    lineas = p.read_text(encoding="utf-8").splitlines()
    assert '"dir": "server"' in lineas[1]
    assert textos_server(evs) == [(101.0, 'ñandú "comillas"')]


def test_cabecera_obligatoria(tmp_path):
    p = tmp_path / "malo.jsonl"
    p.write_text(json.dumps({"t": 1, "dir": "server"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        leer(p)


def test_casete_real_b1_tiene_texto_de_gemini_en_server_y_emit():
    assert CASETE_60S.exists(), "falta el casete real de 60 s"
    cab, evs = leer(CASETE_60S)
    assert cab["model"] and cab["config"]["realtime_input_config"]["activity_handling"] == "NO_INTERRUPTION"
    assert cab["config"]["realtime_input_config"]["automatic_activity_detection"]["disabled"] is True
    srv = textos_server(evs)
    emits_texto = [e["payload"] for e in evs if e["dir"] == "emit" and e["kind"] == "text"]
    assert len(srv) >= 3
    # cada texto emitido salio de un mensaje server (mismo texto, mismo orden)
    assert [m["text"] for m in emits_texto] == [t for _, t in srv]


def test_casete_real_b1_cumple_el_contrato():
    contracts = pytest.importorskip("contracts", reason="contracts/ de backend no disponible")
    cab, evs = leer(CASETE_60S)
    emits = [e["payload"] for e in evs if e["dir"] == "emit"]
    assert emits
    errs = {i: contracts.errores(m) for i, m in enumerate(emits)}
    assert all(not e for e in errs.values()), {i: e for i, e in errs.items() if e}
    seqs = [m["seq"] for m in emits if m["seq"] is not None]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs) and seqs[0] == 1
