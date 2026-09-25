"""--monitor-udp: cada chunk de la fuente sale tambien por UDP local, una vez y en orden, sin frenar
el envio. Sin API: transporte de casete (rotulado)."""
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from worker.monitor_udp import MonitorUDP, parsear

RAIZ = Path(__file__).resolve().parents[2]
CASETE = "fixtures/casetes/evidencia-25-09/simple-en-053454.jsonl"
CLIP = "fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav"


def _cmd(destino: str, casete_out: Path) -> list[str]:
    return [sys.executable, "-m", "worker.run", "--sesion", "t-monitor", "--lang", "en",
            "--transporte", f"casete:{CASETE}", "--archivo", CLIP, "--duracion", "5",
            "--drenaje-s", "1", "--traducir-a", "none", "--casete", str(casete_out),
            "--monitor-udp", destino]


def _puerto_libre() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.skipif(not (RAIZ / CASETE).exists() or not (RAIZ / CLIP).exists(), reason="sin fixtures")
def test_recibe_50_datagramas_en_5_s(tmp_path):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(20)
    puerto = rx.getsockname()[1]
    proc = subprocess.Popen(_cmd(f"127.0.0.1:{puerto}", tmp_path / "c.jsonl"), cwd=RAIZ,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tams, t = [], []
    try:
        while True:
            d, _ = rx.recvfrom(65536)
            tams.append(len(d))
            t.append(time.monotonic())
            rx.settimeout(3)
    except socket.timeout:
        pass
    finally:
        rx.close()
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err.decode(errors="replace")[-2000:]
    assert 45 <= len(tams) <= 52, len(tams)          # 5 s / 100 ms, una vez por chunk (sin solape)
    assert all(n == 3200 for n in tams[:-1]) and 0 < tams[-1] <= 3200
    assert 3.5 <= t[-1] - t[0] <= 7.0, t[-1] - t[0]   # pautado en tiempo real, no en rafaga
    assert b"monitor UDP: datagramas=" in err


@pytest.mark.skipif(not (RAIZ / CASETE).exists() or not (RAIZ / CLIP).exists(), reason="sin fixtures")
def test_puerto_sin_receptor_no_rompe(tmp_path):
    r = subprocess.run(_cmd(f"127.0.0.1:{_puerto_libre()}", tmp_path / "c.jsonl"), cwd=RAIZ,
                       capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode(errors="replace")[-2000:]
    assert b'"segundos_enviados"' in r.stdout


def test_host_no_local_exit_2(tmp_path):
    r = subprocess.run(_cmd("192.168.0.10:5000", tmp_path / "c.jsonl"), cwd=RAIZ,
                       capture_output=True, timeout=30)
    assert r.returncode == 2
    assert b"--monitor-udp" in r.stderr


@pytest.mark.parametrize("v,esperado", [("127.0.0.1:9", ("127.0.0.1", 9)),
                                        ("localhost:5000", ("localhost", 5000)),
                                        ("[::1]:5000", ("::1", 5000)), ("::1:5000", ("::1", 5000))])
def test_parsear_ok(v, esperado):
    assert parsear(v) == esperado


@pytest.mark.parametrize("v", ["0.0.0.0:5000", "10.0.0.1:5000", "127.0.0.1", "127.0.0.1:0",
                               "127.0.0.1:70000", "127.0.0.1:x", "example.com:5000", ""])
def test_parsear_rechaza(v):
    with pytest.raises(ValueError):
        parsear(v)


def test_monitor_ignora_errores():
    m = MonitorUDP("127.0.0.1", _puerto_libre())
    for _ in range(20):
        m(b"\0" * 3200)            # sin receptor: nunca levanta
    m.cerrar()
    m(b"\0" * 3200)                # cerrado: tampoco
    assert m.enviados + m.errores == 20
