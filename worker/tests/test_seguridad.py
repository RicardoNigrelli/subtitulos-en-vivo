"""Seguridad del worker (adversario final: A2, M5, B7, OPENBLAS; addendum HUB_TOKEN). Sin API, 0 min.

- A2: una URL de stream con credenciales NO llega al casete (incluido el session_start que el worker
  emite y graba), ni al log de stderr (comando ffmpeg impreso), ni a reportes/cuota-audio.log.
  `worker.run.correr` corre EN PROCESO con el transporte de casete (rotulado, sin Gemini) y una fuente
  falsa (tono) en lugar de ffmpeg contra el host inexistente.
- M5: `--sesion` fuera del slug => exit 2 con mensaje claro.
- B7: el subproceso (ffmpeg/yt-dlp) recibe un entorno filtrado: un `python -c` con ese entorno no ve
  ninguna variable con KEY o TOKEN en el nombre.
- OPENBLAS_NUM_THREADS=1 queda fijado al importar worker.run si no estaba definido.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from worker import cuota, run
from worker.ingesta import SR, trocear
from worker.seguridad import entorno_subproceso, sin_credenciales, validar_sesion

RAIZ = Path(__file__).resolve().parents[2]
CASETE_EN = RAIZ / "fixtures" / "casetes" / "b1-en-60s.jsonl"
RTMP = "rtmp://usuario:CLAVE@host/live/STREAMKEY"
SRT = "srt://host:9000?passphrase=X"
SECRETOS = ("usuario", "CLAVE", "STREAMKEY", "passphrase", "=X")


def test_sin_credenciales_casos():
    assert sin_credenciales(RTMP) == "rtmp://host/live/***"
    assert sin_credenciales(SRT) == "srt://host:9000"
    assert sin_credenciales("https://u:p@cdn.x/hls/a.m3u8?token=abc#f") == "https://cdn.x/hls/a.m3u8"
    assert sin_credenciales("https://www.youtube.com/watch?v=cPaqkFCqWeg&si=xyz") == \
        "https://www.youtube.com/watch?v=cPaqkFCqWeg"
    assert sin_credenciales("udp://127.0.0.1:9000") == "udp://127.0.0.1:9000"
    for local in (None, "", "fixtures/audio/clips/x.wav", r"C:\audio\x.wav", "audio=Microphone (X)"):
        assert sin_credenciales(local) == local


class _FuenteFalsa:
    """Reemplaza a FuenteReabrible (no hay host rtmp/srt): 4 s de tono, sin tiempo real."""

    def __init__(self, entrada, formato=None, opciones=None, **kw):
        self.entrada = entrada
        self.caidas = 0
        n = 4 * SR
        x = (3000 * np.sin(2 * np.pi * 220 * np.arange(n) / SR)).astype("<i2").tobytes()
        self._chunks = list(trocear(x))

    def parar(self):
        pass

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            await asyncio.sleep(0.01)
            yield c


@pytest.mark.parametrize("url", [RTMP, SRT])
def test_url_con_credenciales_no_llega_a_casete_session_start_log_ni_cuota(url, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "FuenteReabrible", _FuenteFalsa)
    casete = tmp_path / "c.jsonl"
    a = run._args(["--fuente", "url", "--url", url, "--sesion", "sala-segura", "--lang", "en",
                   "--duracion", "4", "--transporte", f"casete:{CASETE_EN}", "--casete", str(casete),
                   "--traducir-a", "none", "--drenaje-s", "1"])
    asyncio.run(asyncio.wait_for(run.correr(a), 60))
    err = capsys.readouterr().err
    texto = casete.read_text(encoding="utf-8")
    evs = [json.loads(l) for l in texto.splitlines() if l.strip()]
    ss = [e["payload"] for e in evs if e.get("dir") == "emit" and e["payload"]["type"] == "session_start"]
    assert len(ss) == 1                                    # el session_start existe y se grabo
    assert "[run] fuente url:" in err                      # el comando ffmpeg SI se imprimio
    for s in SECRETOS:
        assert s not in texto, f"{s!r} en el casete"
        assert s not in json.dumps(ss[0]), f"{s!r} en session_start"
        assert s not in err, f"{s!r} en el log"
    # con el transporte de casete meta.source es el rotulo de test y el original va a source_original
    assert ss[0]["meta"]["source_original"]["url"] == sin_credenciales(url)
    assert ss[0]["meta"]["title"].startswith("[TEST] url: " + sin_credenciales(url))
    # cuota: la linea que escribiria la corrida real
    log = tmp_path / "cuota-audio.log"
    cuota.registrar("sala-segura", 12.3, url, log=log)
    linea = log.read_text(encoding="utf-8")
    assert sin_credenciales(url) in linea
    for s in SECRETOS:
        assert s not in linea


@pytest.mark.parametrize("sid", ["../../x", "Sala 1", "", "-x", "a" * 65, "sala/uno", "SALA"])
def test_sesion_invalida_sale_con_2(sid, capsys):
    with pytest.raises(SystemExit) as e:
        run._args(["--archivo", "x.wav", "--sesion", sid, "--lang", "en"])
    assert e.value.code == 2
    assert "--sesion" in capsys.readouterr().err
    assert not validar_sesion(sid)


def test_sesion_valida_pasa():
    for sid in ("sala-1", "a", "video_final-en", "a" * 64):
        assert run._args(["--archivo", "x.wav", "--sesion", sid, "--lang", "en"]).sesion == sid


def test_sesion_invalida_por_cli_exit_2():
    p = subprocess.run([sys.executable, "-m", "worker.run", "--archivo", "x.wav", "--sesion", "../../x",
                        "--lang", "en"], cwd=RAIZ, capture_output=True, text=True)
    assert p.returncode == 2 and "--sesion '../../x' invalida" in p.stderr


def test_subproceso_no_ve_credenciales():
    base = {**os.environ, "GEMINI_API_KEY": "AIzaFALSA", "GEMINI_API_KEY_RESERVA": "AIzaFALSA2",
            "HUB_TOKEN": "tok", "CARTESIA_API_KEY": "sk_car_falsa"}
    p = subprocess.run([sys.executable, "-c",
                        "import os;print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))"],
                       capture_output=True, text=True, env=entorno_subproceso(base))
    assert p.returncode == 0 and p.stdout.strip() == "[]", p.stdout
    # y los modulos que lanzan ffmpeg / yt-dlp lo usan
    for mod in ("worker/ingesta.py", "worker/importar.py"):
        src = (RAIZ / mod).read_text(encoding="utf-8")
        n_llamadas = src.count("subprocess.run(") + src.count("create_subprocess_exec(")
        assert src.count("env=entorno_subproceso()") == n_llamadas, mod


def test_openblas_un_hilo_antes_de_numpy():
    code = ("import os; os.environ.pop('OPENBLAS_NUM_THREADS', None); import worker.run; "
            "print(os.environ.get('OPENBLAS_NUM_THREADS'))")
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    p = subprocess.run([sys.executable, "-c", code], cwd=RAIZ, capture_output=True, text=True, env=env)
    assert p.stdout.strip() == "1", p.stderr
    src = (RAIZ / "worker" / "run.py").read_text(encoding="utf-8")
    assert src.index('setdefault("OPENBLAS_NUM_THREADS"') < src.index("from worker")


def test_hub_token_ausente_avisa_una_vez(monkeypatch, capsys):
    from worker import emisor, gemini
    monkeypatch.setattr(gemini, "_cargar_env", lambda: None)
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    monkeypatch.setattr(emisor, "_AVISO_DEV_TOKEN", False)
    assert emisor.token_hub() == "dev-token" and emisor.token_hub() == "dev-token"
    assert capsys.readouterr().err.count("HUB_TOKEN no definido: se usa dev-token") == 1
