"""El contrato: el validador de linea de comandos y los ejemplos."""
from __future__ import annotations

import json
import subprocess
import sys

from contracts import errores, errores_frame, leer_archivo
from hub.tests.util import EJEMPLOS, RAIZ, texto


def _validate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "contracts.validate", *args], cwd=RAIZ,
                          capture_output=True, text=True, encoding="utf-8")


def test_ejemplos_validan_con_exit_0():
    r = _validate("contracts/ejemplos/*.jsonl")  # el comodin lo expande el validador si el shell no
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TOTAL: 4 archivos, 58 mensajes, 0 errores" in r.stdout  # B2: + traduccion.jsonl (10)


def test_archivo_invalido_sale_distinto_de_0(tmp_path):
    malo = tmp_path / "malo.jsonl"
    m = texto("s", 1)
    m["t_emit"] = m["t_emit"] * 1000  # milisegundos por error
    malo.write_text(json.dumps(m) + "\n{no es json\n", encoding="utf-8")
    r = _validate(str(malo))
    assert r.returncode == 1
    assert "t_emit" in r.stdout and "JSON invalido" in r.stdout


def test_archivo_inexistente_sale_2():
    assert _validate("no-existe-*.jsonl").returncode == 2


def test_seq_no_creciente_en_un_archivo_falla(tmp_path):
    f = tmp_path / "desorden.jsonl"
    f.write_text("\n".join(json.dumps(texto("s", n)) for n in (1, 3, 2)) + "\n", encoding="utf-8")
    r = _validate(str(f))
    assert r.returncode == 1 and "no es mayor que el anterior" in r.stdout


def test_casete_solo_toma_emit(tmp_path):
    cas = tmp_path / "casete.jsonl"
    lineas = [
        {"casete": 1, "session_id": "c", "lang": "en"},
        {"t": 1.0, "dir": "client", "kind": "connect", "payload": {"session_id": "c"}},
        {"t": 2.0, "dir": "server", "kind": "x", "payload": {"serverContent": {}}},
        {"t": 3.0, "dir": "emit", "kind": "text", "payload": texto("c", 1)},
    ]
    cas.write_text("\n".join(json.dumps(x) for x in lineas) + "\n", encoding="utf-8")
    msgs = [m for _, m in leer_archivo(cas)]
    assert msgs == [texto("c", 1)]
    assert _validate(str(cas)).returncode == 0


def test_nulos_fuera_de_text_y_obligatorios_en_text():
    # Asi emite el worker real: todos los campos, null donde no aplica (ajuste 13:14).
    inicio = {"v": 1, "type": "session_start", "session_id": "s", "seq": 1, "lang": "en", "text": None,
              "translations": {}, "audio_start": None, "audio_end": None, "t_captured": None,
              "t_emit": 1790262000.0, "replay": False,
              "meta": {"title": "t", "source": {"file": "x.wav", "url": "u", "start_s": 0.0, "dur_s": 60.0}}}
    assert errores(inicio) == []
    t = texto("s", 2)
    t["audio_start"] = None
    assert any("audio_start" in e for e in errores(t))
    t = texto("s", 2)
    t["text"] = ""
    assert any("text" in e for e in errores(t))


def test_heartbeat_del_hub_es_mensaje_valido_e_init_valida_su_frame():
    hb = {"v": 1, "type": "heartbeat", "session_id": "s", "seq": None, "t_hub": 1790262000.5,
          "last_seq": 3, "state": "live"}
    assert errores(hb) == []
    init = {"type": "init", "v": 1, "session_id": "s", "lang": "es", "session_lang": "en", "last_seq": 2,
            "state": "live", "lines": [texto("s", 1), texto("s", 2)]}
    assert errores_frame(init, "init") == []
    init["lines"][0]["seq"] = "uno"
    assert errores_frame(init, "init")


def test_traduccion_fallida_debe_ir_marcada():
    t = texto("s", 1)
    t["translations"] = {"es": {"text": None, "ok": False}}
    assert errores(t) == []
    t["translations"] = {"es": {"text": "algo", "ok": False}}
    assert errores(t), "ok:false con texto no deberia validar"
    t["translations"] = {"es": {"text": None, "ok": True}}
    assert errores(t), "ok:true sin texto no deberia validar"


def test_ejemplos_son_sinteticos_y_rotulados():
    for f in EJEMPLOS.glob("*.jsonl"):
        for _, m in leer_archivo(f):
            assert m["replay"] is True
            assert (m.get("meta") or {}).get("source") == "ejemplo-contrato"
            if m["type"] == "text":
                assert "xample" in m["text"] or "jemplo" in m["text"], m["text"]
