"""Utilidades de qa: leer casetes, percentiles, levantar/matar un hub PROPIO, escuchar /ws.

No es código de la solución: sólo mide desde afuera. Percentil = nearest-rank (valor observado en la
posición ceil(q/100*n) de la lista ordenada). NUNCA promedio.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
OUT = RAIZ / "qa" / "out"
PY = str(RAIZ / ".venv" / "Scripts" / "python.exe")


def leer_casete(path) -> tuple[dict, list[dict]]:
    lineas = Path(path).read_text(encoding="utf-8").splitlines()
    cab = json.loads(lineas[0])
    return cab, [json.loads(x) for x in lineas[1:] if x.strip()]


def pct(vals, q):
    v = sorted(vals)
    if not v:
        return None
    return v[max(1, math.ceil(q / 100 * len(v))) - 1]


def resumen(vals) -> dict:
    v = sorted(vals)
    r3 = lambda x: None if x is None else round(x, 3)
    return {"n": len(v), "p50": r3(pct(v, 50)), "p95": r3(pct(v, 95)),
            "min": r3(v[0]) if v else None, "max": r3(v[-1]) if v else None,
            "metodo": "nearest-rank", "valores_ordenados": [round(x, 3) for x in v]}


def puerto_ocupado(port: int) -> str | None:
    """None si nadie escucha. Chequea netstat (LISTENING) y un connect a 127.0.0.1 / ::1."""
    try:
        ns = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
        for ln in ns.splitlines():
            p = ln.split()
            if len(p) >= 4 and p[0] == "TCP" and p[1].endswith(f":{port}") and "LISTEN" in p[3]:
                return f"netstat: {ln.strip()}"
    except Exception:
        pass
    for h in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((h, port), timeout=0.5):
                return f"connect ok en {h}"
        except OSError:
            continue
    return None


def health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def levantar_hub(port: int, log_path, espera_s: float = 15.0) -> subprocess.Popen:
    env = dict(os.environ, HUB_PORT=str(port), PYTHONIOENCODING="utf-8")
    f = open(log_path, "a", encoding="utf-8")
    p = subprocess.Popen([PY, "-m", "hub"], cwd=str(RAIZ), env=env, stdout=f, stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    while time.monotonic() - t0 < espera_s:
        if p.poll() is not None:
            raise RuntimeError(f"el hub en {port} salió con {p.returncode} (ver {log_path})")
        if health(port):
            return p
        time.sleep(0.2)
    p.kill()
    raise RuntimeError(f"el hub en {port} no respondió /health en {espera_s} s")


def matar(p: subprocess.Popen) -> int | None:
    if p.poll() is None:
        p.kill()  # TerminateProcess en Windows: caída dura, no apagado prolijo
    try:
        return p.wait(10)
    except Exception:
        return None


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


async def escuchar(url: str, registro: list, parar: asyncio.Event, reconectar: bool = False,
                   log=lambda s: None) -> None:
    """Registra CADA frame con t_receive (time.time() al recibir) y los eventos de conexión.
    registro: lista de dicts {"ev": "frame"|"conectado"|"desconectado"|"error_conexion", ...}."""
    import aiohttp
    async with aiohttp.ClientSession() as http:
        while not parar.is_set():
            try:
                async with http.ws_connect(url, heartbeat=20.0) as ws:
                    registro.append({"ev": "conectado", "t": time.time()})
                    log(f"cliente conectado a {url}")
                    while not parar.is_set():
                        try:
                            r = await ws.receive(timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        if r.type != aiohttp.WSMsgType.TEXT:
                            registro.append({"ev": "desconectado", "t": time.time(),
                                             "tipo": r.type.name, "codigo": ws.close_code})
                            log(f"cliente: el hub cerró ({r.type.name}, código {ws.close_code})")
                            break
                        registro.append({"ev": "frame", "t_receive": time.time(), "frame": json.loads(r.data)})
            except Exception as e:  # hub caído o todavía no levantado
                registro.append({"ev": "error_conexion", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:200]})
            if not reconectar:
                return
            await asyncio.sleep(0.3)


def guardar_jsonl(path, filas) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for x in filas:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")


def leer_jsonl(path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def ahora_ar() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
