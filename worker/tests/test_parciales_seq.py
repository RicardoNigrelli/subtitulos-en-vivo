"""B2: parciales derivados de un casete REAL, seq tras reinicio, replay de translation."""
import json
from pathlib import Path

from worker.casete import leer
from worker.contrato import mensaje
from worker.rederivar import parciales_de
from worker.replay import rotular
from worker.session import SessionWorker

CASETES = Path(__file__).resolve().parents[2] / "fixtures" / "casetes"


def test_parciales_desde_casete_real_es():
    cab, evs = leer(CASETES / "b1-es-60s.jsonl")
    crudos = [e for e in evs if e["dir"] == "server"
              and ((e["payload"].get("serverContent") or {}).get("interimInputTranscription") or {}).get("text")]
    p = parciales_de(evs, cab["session_id"], cab["lang"])
    assert len(p) == len(crudos) == 84
    for e, c in zip(p, crudos):
        m = e["payload"]
        assert m["type"] == "partial" and m["seq"] is None and e["t"] == c["t"] == m["t_emit"]
        assert m["text"] == c["payload"]["serverContent"]["interimInputTranscription"]["text"]
        assert m["t_captured"] is None or m["t_captured"] <= m["t_emit"]
    starts = [e["payload"]["audio_start"] for e in p if e["payload"]["audio_start"] is not None]
    assert len(starts) >= 80 and starts[0] == 0.0 and max(starts) > 40


def test_parciales_casete_sin_interinos():
    cab, evs = leer(CASETES / "b1-en-60s-rederivado.jsonl")
    assert parciales_de(evs, cab["session_id"], cab["lang"]) == []


def test_seq_sigue_desde_last_seq_del_hub():
    w = SessionWorker("sala-x", "en", None, None, seq_inicial=13)
    a = w.emitir("text", text="t", audio_start=0.0, audio_end=1.0, t_captured=w.reloj())
    p = w.emitir("partial", text="p", audio_start=0.0, t_captured=None)
    t = w.emitir("translation", items=[{"seq": a["seq"], "text": "x", "ok": True}],
                 meta={"lang_to": "es", "model": "m", "batch_ms": 1})
    b = w.emitir("text", text="t2", audio_start=1.0, audio_end=2.0, t_captured=w.reloj())
    assert (a["seq"], p["seq"], t["seq"], b["seq"]) == (14, None, None, 15)
    assert t["items"][0]["seq"] == 14 and "items" not in a


def test_replay_renumera_items_de_translation():
    m = mensaje("translation", "s", None, "en", items=[{"seq": 3, "text": "x", "ok": True},
                                                       {"seq": 4, "text": None, "ok": False}],
                meta={"lang_to": "es", "model": "m", "batch_ms": 5}, t_emit=100.0)
    r = rotular(m, sesion=None, base_seq=10, delta_t=1.0, nombre_casete="c")
    assert [i["seq"] for i in r["items"]] == [13, 14] and r["seq"] is None and r["replay"] is True
    assert [i["seq"] for i in m["items"]] == [3, 4]          # el original no se toca
