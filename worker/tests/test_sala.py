"""Operacion en sala (sin API, 0 min): parada limpia y reapertura de la FUENTE.

- test_parada_limpia_*: SessionWorker con el transporte de casete (rotulado, sin API) y una fuente SIN
  FIN; la "senal" se simula llamando a parar("stop") (lo mismo que hace el handler de worker/run.py).
- test_fuente_udp_*: FuenteReabrible contra un ffmpeg REAL que manda mpegts por UDP local, se corta y
  vuelve (el receptor tambien es ffmpeg real).
- test_run_sala_udp_*: `python -m worker.run --fuente url udp://... --transporte casete:...` como
  proceso aparte; el emisor UDP se corta y vuelve; al final se le manda una senal REAL
  (CTRL_BREAK_EVENT en Windows, SIGTERM en POSIX) y tiene que salir con exit 0, session_end "stop".
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from worker.ingesta import SR, Chunk, EventoFuente, FuenteReabrible, trocear
from worker.session import ConfigReabrir, SessionWorker
from worker.transporte_casete import FabricaCasete

RAIZ = Path(__file__).resolve().parents[2]
CASETE_EN = RAIZ / "fixtures" / "casetes" / "b1-en-60s.jsonl"
FFMPEG = shutil.which("ffmpeg")
# sondeo corto SOLO en el test de la fuente (el default de ffmpeg sondea ~5 s antes del primer byte)
SONDEO_CORTO = ["-probesize", "32768", "-analyzeduration", "500000"]


class _Bus:
    def __init__(self):
        self.msgs = []

    def publicar(self, m):
        self.msgs.append(m)


def _tono(total_s: float) -> bytes:
    rng = np.random.default_rng(7)
    partes, t = [], 0.0
    while t < total_s:
        for seg, amp in ((1.8, 4000), (1.2, 0)):
            n = int(round(seg * SR))
            x = amp * np.sin(2 * np.pi * 220 * np.arange(n) / SR) + rng.normal(0, 30, n)
            partes.append(np.clip(x, -32768, 32767).astype("<i2"))
            t += seg
    return np.concatenate(partes).tobytes()[: int(total_s * SR) * 2]


def test_parada_limpia_session_end_stop_y_conexion_cerrada():
    fab = FabricaCasete(str(CASETE_EN))
    bus = _Bus()
    base = list(trocear(_tono(30.0), t0_epoch=0.0))
    caja = {}

    async def fuente_sin_fin():
        # fuente de sala: no termina sola; a los 20 s de audio llega la "senal" (parar)
        i = 0
        while True:
            b = base[i % len(base)]
            c = Chunk(i, b.data, round(i * 0.1, 3), round(i * 0.1 + 0.1, 3), b.t_captured, b.rms)
            if i == 200:
                caja["w"].parar("stop")
            yield c
            i += 1
            await asyncio.sleep(0)

    async def dormir(_s):
        await asyncio.sleep(0)

    cfg = ConfigReabrir()
    cfg.drenaje_vieja_s, cfg.tick_s = 0.3, 0.05

    async def main():
        w = SessionWorker("sala-stop", "en", fab(0.0), fuente_sin_fin(), emisor=bus, heartbeat_s=10_000,
                          espera_final_s=30.0, reloj=lambda: caja["w"].pos_s, dormir=dormir,
                          log=lambda s: None, fabrica=fab, reabrir=cfg)
        caja["w"] = w
        t0 = time.monotonic()
        res = await asyncio.wait_for(w.correr(), 60)
        return w, res, time.monotonic() - t0

    w, res, dur = asyncio.run(main())
    fin = [m for m in bus.msgs if m["type"] == "session_end"]
    assert len(fin) == 1 and fin[0]["meta"]["reason"] == "stop"
    assert res.motivo_fin == "stop"
    assert 19.9 <= w.pos_s <= 20.2     # dejo de leer la fuente al parar (segundos_enviados suma solapes)
    assert w.con.tr.cerrado            # conexion cerrada en orden (cerrar())
    seqs = [m["seq"] for m in bus.msgs if m["seq"] is not None]
    assert seqs == list(range(1, len(seqs) + 1))
    assert dur < 30.0                  # drenaje corto de parada, no los 30 s


def _puerto_udp() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _url_udp(puerto: int) -> str:
    return "udp" + "://127.0.0.1:" + str(puerto)


def _emisor_udp(puerto: int) -> subprocess.Popen:
    """ffmpeg real: tono de 16 kHz en mpegts por UDP local, a ritmo real, sin fin (se corta con kill)."""
    return subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-re", "-f", "lavfi", "-i",
         "sine=frequency=220:sample_rate=16000", "-ac", "1", "-c:a", "mp2", "-f", "mpegts",
         _url_udp(puerto) + "?pkt_size=1316"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cortar(p: subprocess.Popen) -> None:
    p.kill()
    p.wait(10)


@pytest.mark.skipif(FFMPEG is None, reason="sin ffmpeg")
def test_fuente_udp_se_cae_y_se_reabre_con_idx_continuo():
    puerto = _puerto_udp()
    procs = []

    async def main():
        f = FuenteReabrible(_url_udp(puerto), opciones_entrada=SONDEO_CORTO, timeout_s=1.5,
                            log=lambda s: None)
        procs.append(_emisor_udp(puerto))
        eventos, idxs, t0 = [], [], time.monotonic()
        cortado = False
        async for c in f:
            if time.monotonic() - t0 > 45:
                f.parar()
            if isinstance(c, EventoFuente):
                eventos.append((c.tipo, c.detalle))
                if c.tipo == "caida" and cortado and len(procs) == 1:
                    procs.append(_emisor_udp(puerto))          # el stream vuelve
                continue
            idxs.append(c.idx)
            if len(idxs) == 30 and not cortado:
                _cortar(procs[0])                              # "se desenchufa el cable"
                cortado = True
            if any(t == "reabierta" for t, _ in eventos) and len(idxs) >= 60:
                f.parar()
        return eventos, idxs

    try:
        eventos, idxs = asyncio.run(asyncio.wait_for(main(), 60))
    finally:
        for p in procs:
            if p.poll() is None:
                _cortar(p)
    tipos = [e[0] for e in eventos]
    assert "caida" in tipos and "reabierta" in tipos, eventos
    assert tipos.index("caida") < tipos.index("reabierta")
    assert idxs == list(range(len(idxs))) and len(idxs) >= 60     # la linea de tiempo sigue pareja
    primera = next(d for t, d in eventos if t == "caida")
    assert "sin bytes" in primera["motivo"] and primera["reintento_en_s"] == 1.0


@pytest.mark.skipif(FFMPEG is None, reason="sin ffmpeg")
def test_run_sala_udp_caida_reapertura_y_senal_parada_limpia(tmp_path):
    puerto = _puerto_udp()
    casete = tmp_path / "sala.jsonl"
    # timeout de fuente > sondeo por defecto de ffmpeg (~5 s): el run usa las opciones de produccion
    env = {**os.environ, "FUENTE_TIMEOUT_S": "7", "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "-m", "worker.run", "--fuente", "url", "--url", _url_udp(puerto),
           "--sesion", "sala-udp", "--lang", "en", "--transporte", f"casete:{CASETE_EN}",
           "--casete", str(casete), "--traducir-a", "none", "--drenaje-s", "30"]
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
    tx = _emisor_udp(puerto)
    w = subprocess.Popen(cmd, cwd=RAIZ, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    tx2 = None
    try:
        time.sleep(16)                 # arranque (~3 s) + sondeo (~5 s) + ~8 s de audio
        _cortar(tx)                    # se cae el stream
        time.sleep(9)                  # > FUENTE_TIMEOUT_S (7) + backoff (1): al menos una caida
        tx2 = _emisor_udp(puerto)      # vuelve
        time.sleep(14)                 # sondeo + ~8 s de audio despues de reabrir
        w.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM)
        out, err = w.communicate(timeout=90)
    finally:
        for p in (tx, tx2):
            if p is not None and p.poll() is None:
                _cortar(p)
        if w.poll() is None:
            w.kill()
    (tmp_path / "stderr.log").write_bytes(err)
    assert w.returncode == 0, err.decode("utf-8", "replace")[-2000:]
    evs = [json.loads(l) for l in casete.read_text(encoding="utf-8").splitlines() if l.strip()]
    emit = [e["payload"] for e in evs if e.get("dir") == "emit"]
    cliente = [e.get("kind") for e in evs if e.get("dir") == "client"]
    assert [m["type"] for m in emit].count("session_start") == 1
    assert {m["session_id"] for m in emit} == {"sala-udp"}
    errores = [m for m in emit if m["type"] == "error"]
    assert errores and all(m["meta"]["code"] == "fuente" for m in errores)
    assert "fuente_caida" in cliente and "fuente_reabierta" in cliente
    i_re = cliente.index("fuente_reabierta")
    assert "ventana" in cliente[i_re:]                     # llego audio despues de reabrir
    assert emit[-1]["type"] == "session_end" and emit[-1]["meta"]["reason"] == "stop"
    seqs = [m["seq"] for m in emit if m["seq"] is not None]
    assert seqs == list(range(1, len(seqs) + 1))
    resumen = json.loads(out.decode("utf-8").strip().splitlines()[-1])
    assert resumen["motivo_fin"] == "stop" and resumen["caidas_fuente"] >= 1
