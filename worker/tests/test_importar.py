"""B4: worker/importar.py (R17b) sin red: eleccion de la pista original y corte desde archivo local."""
import shutil
import wave

import numpy as np
import pytest

from worker.importar import elegir_pista, main


def test_elige_la_pista_original_y_no_el_doblaje():
    # metadatos con la forma de `yt-dlp -J` (formatos de un video con doblaje automatico)
    info = {"formats": [
        {"format_id": "140-0", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 129,
         "format_note": "English (US) - dubbed-auto, medium", "language_preference": -1},
        {"format_id": "140-1", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 129,
         "format_note": "Spanish (US) original (default), medium", "language_preference": 10},
        {"format_id": "251-1", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 140,
         "format_note": "Spanish (US) original (default), medium", "language_preference": 10},
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "abr": 96},
    ]}
    assert elegir_pista(info)["format_id"] == "140-1"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_corta_clip_local_a_pcm_16k_mono(tmp_path, capsys):
    src = tmp_path / "fuente.wav"
    sr = 44100
    x = (3000 * np.sin(2 * np.pi * 440 * np.arange(sr * 3) / sr)).astype("<i2")   # tono, no voz
    with wave.open(str(src), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(x, 2).tobytes())
    rc = main([str(src), "--slug", "prueba", "--inicio", "0.5", "--duracion", "1.5",
               "--clips-dir", str(tmp_path / "clips")])
    assert rc == 0
    out = tmp_path / "clips" / "prueba-0s-1s.wav"
    with wave.open(str(out), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert abs(w.getnframes() / 16000 - 1.5) < 0.05
    assert "| `prueba-0s-1s.wav` |" in capsys.readouterr().out
