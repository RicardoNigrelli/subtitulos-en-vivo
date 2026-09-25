"""B8: fuentes de ingesta (archivo/url/mic), glosario desde la agenda (R8c) y su medidor."""
import sys
from pathlib import Path

import pytest

from worker.glosario import nombres_propios, vocabulario
from worker.ingesta import comando_ffmpeg, opciones_fuente
from worker.medir_glosario import medir

RAIZ = Path(__file__).resolve().parents[2]
AGENDA = RAIZ / "fixtures" / "agenda.json"


def test_url_http_reconecta_y_udp_no():
    ent, fmt, extra = opciones_fuente("url", "https://x/y.m3u8")
    assert ent == "https://x/y.m3u8" and fmt is None and "-reconnect" in extra
    assert opciones_fuente("url", "udp://127.0.0.1:9000") == ("udp://127.0.0.1:9000", None, [])


@pytest.mark.skipif(sys.platform != "win32", reason="dshow es de Windows")
def test_mic_windows_es_dshow_con_buffer_chico():
    ent, fmt, extra = opciones_fuente("mic", "Micrófono (X)")
    assert (ent, fmt, extra) == ("audio=Micrófono (X)", "dshow", ["-audio_buffer_size", "50"])
    cmd = comando_ffmpeg(ent, 5.0, 10.0, fmt, extra)
    i = cmd.index("-i")
    assert cmd[i - 2:i + 2] == ["-f", "dshow", "-i", "audio=Micrófono (X)"]
    assert "-ss" not in cmd and cmd[cmd.index("-t") + 1] == "10.000"      # un dispositivo no se busca


def test_archivo_busca_con_ss_y_no_normaliza():
    cmd = comando_ffmpeg("a.wav", 12.0, 30.0)
    assert cmd[cmd.index("-ss") + 1] == "12.000"
    assert not any(x.startswith(("loudnorm", "dynaudnorm", "volume")) for x in cmd)
    with pytest.raises(ValueError):
        opciones_fuente("otra", "x")


def test_glosario_de_la_agenda_real():
    v = vocabulario(AGENDA, "booch-en")
    for t in ("Nerdearla", "Grady Booch", "Booch", "IBM", "UML", "Jim Rumbaugh", "Ivar Jacobson"):
        assert t in v, t
    assert len(v) == len({x.lower() for x in v}) and len(v) <= 40
    p = vocabulario(AGENDA, "paez-es")
    assert "Michael Feathers" in p and "Vivimos" not in p           # inicio de oracion no cuenta
    with pytest.raises(KeyError):
        vocabulario(AGENDA, "no-existe")


def test_nombres_propios_siglas_y_secuencias():
    ns = nombres_propios("Charla en Nerdearla. Hablamos de UML con Grady Booch y de NicoPaez.")
    assert {"Nerdearla", "UML", "Grady Booch", "NicoPaez"} <= set(ns)
    assert "Charla" not in ns and "Hablamos" not in ns


def test_medir_glosario_sobre_casete_real():
    r = medir(RAIZ / "fixtures" / "casetes" / "b3-ab-manual.jsonl", ["Jim Rumbaugh", "Plato", "plato"])
    assert r["terminos"]["Jim Rumbaugh"]["bien_escrita"] == 1
    assert r["terminos"]["Plato"]["bien_escrita"] == 3
    assert r["terminos"]["plato"] == {"bien_escrita": 0, "sin_mayusculas": 3}
