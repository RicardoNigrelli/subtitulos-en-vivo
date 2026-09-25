"""Re-corrida de reportes/adv-final/pruebas_hub.py contra el hub ARREGLADO (25/09, backend-seguridad).

El script del adversario asume que la conexion del productor sigue abierta despues de un mensaje de
900 KB / 1 MB; con max_msg_bytes=64 KiB el hub la corta (lo esperado), y el script se cae en el paso
siguiente. Aca se reusan SUS funciones (secciones 1, 2, 6, 7 y el flood) y se reescriben solo las
partes que dependian de esa conexion: fuera de contrato (una conexion por caso) y memoria.

    .venv/Scripts/python hub/tests/adv_postarreglo.py <puerto> <pid> [n_flood]
No es un test de pytest (no empieza con test_). Solo contra un hub propio (no 8100).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

ADV = Path(__file__).resolve().parents[2] / "reportes" / "adv-final" / "pruebas_hub.py"
fuente = ADV.read_text(encoding="utf-8").rsplit("asyncio.run(main())", 1)[0]
ns: dict = {"__name__": "adv"}
exec(compile(fuente, str(ADV), "exec"), ns)  # define PORT/PID/TOKEN desde sys.argv y las secciones
p, WS, TOKEN, mem_kb, raw_get, health_ok, base_text = (ns[k] for k in (
    "p", "WS", "TOKEN", "mem_kb", "raw_get", "health_ok", "base_text"))


async def productor(s):
    ws = await s.ws_connect(WS + "/ingest", max_msg_size=0)
    await ws.send_str(json.dumps({"type": "auth", "token": TOKEN}))
    await ws.receive(timeout=5)
    return ws


async def caso(s, nombre, data):
    ws = await productor(s)
    try:
        await ws.send_str(data)
        m = await ws.receive(timeout=5)
        d = m.data if isinstance(m.data, str) else repr(m.data)
        p(f"{nombre}: {m.type} len={len(d)} close_code={ws.close_code} {d[:200]}")
    except Exception as e:
        p(f"{nombre}: excepcion cliente {e!r} close_code={ws.close_code}")
    finally:
        await ws.close()


async def fuera_de_contrato(s):
    p("\n=== 3'. FUERA DE CONTRATO (una conexion por caso) ===")
    await caso(s, "seq_string_1MB", json.dumps(base_text("sala-x", "Z" * 900000)))
    await caso(s, "seq_string_60KB (entra en 64 KiB: eco truncado)", json.dumps(base_text("sala-x", "Z" * 60000)))
    await caso(s, "anidado_200k post-auth (400 KB)", "[" * 200000 + "]" * 200000)
    await caso(s, "anidado_20k post-auth (40 KB)", "[" * 20000 + "]" * 20000)
    await caso(s, "json_10MB", json.dumps(base_text("sala-x", 99, "b", relleno="c" * (10 * 1024 * 1024))))
    ws = await s.ws_connect(WS + "/ingest", max_msg_size=0)
    await ws.send_str("[" * 20000 + "]" * 20000)
    m = await ws.receive(timeout=5)
    p(f"primer_frame_anidado_20k (SIN token, 40 KB): {m.type} data={m.data} extra={m.extra} close_code={ws.close_code}")
    await ws.close()
    p(f"health: {await health_ok(s)}")


async def memoria(s):
    p("\n=== 4'. MEMORIA: 150 mensajes de ~1 MB y 150 de ~60 KB con campo extra ===")
    m0 = mem_kb()
    cortes = 0
    t0 = time.perf_counter()
    for i in range(150):
        ws = await productor(s)
        try:
            await ws.send_str(json.dumps(base_text("sala-mem", 10 + i, "x", relleno="r" * 1_000_000)))
            m = await ws.receive(timeout=5)
            cortes += m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR)
        except Exception:
            cortes += 1
        finally:
            await ws.close()
    ws = await productor(s)
    for i in range(150):  # entran (60 KB < 64 KiB) pero el campo extra no se guarda
        await ws.send_str(json.dumps(base_text("sala-mem2", 10 + i, "x", relleno="r" * 60_000)))
    await asyncio.sleep(2)
    await ws.close()
    m1 = mem_kb()
    st, h, b = raw_get("/api/metricas", {"Authorization": "Bearer " + TOKEN})
    ses = json.loads(b)["sesiones"]
    p(f"1 MB: 150 enviados, conexiones cortadas={cortes}, sala-mem en memoria={'sala-mem' in ses}; "
      f"60 KB: en_historial={ses.get('sala-mem2', {}).get('en_historial')}; memoria {m0} K -> {m1} K "
      f"(+{(m1 - m0) / 1024:.1f} MB) en {time.perf_counter() - t0:.1f} s")
    wa = await s.ws_connect(WS + "/ws/sala-mem2?lang=es", max_msg_size=0)
    m = await wa.receive(timeout=10)
    p(f"init a un espectador de sala-mem2: {len(m.data)} bytes")
    await wa.close()
    st, h, b = raw_get("/api/sesiones/sala-mem2/historial?tipos=todos")
    p(f"historial sala-mem2: {st} {len(b)} bytes, {len(json.loads(b))} mensajes, 'relleno' presente={b'relleno' in b}")
    p(f"health: {await health_ok(s)}")


async def flood(s, n, slugs_distintos):
    tipo = "slugs distintos" if slugs_distintos else "una sola sala"
    p(f"\n=== 5'. FLOOD {n + 1} conexiones sin auth ({tipo}) ===")
    m0, h0 = mem_kb(), await health_ok(s)
    conns, abiertas, r1013, motivos = [], 0, 0, set()
    t0 = time.perf_counter()
    for i in range(n + 1):
        sid = f"flood-{i}" if slugs_distintos else "flood-una"
        c = await s.ws_connect(WS + f"/ws/{sid}?lang=es", timeout=aiohttp.ClientWSTimeout(ws_receive=None))
        m = await c.receive(timeout=5)
        if m.type == aiohttp.WSMsgType.TEXT:
            abiertas += 1
            conns.append(c)
        else:
            r1013 += c.close_code == 1013
            motivos.add(m.extra)
            await c.close()
    dt = time.perf_counter() - t0
    await asyncio.sleep(2)
    m1, h1 = mem_kb(), await health_ok(s)
    st, hd, b = raw_get("/api/metricas", {"Authorization": "Bearer " + TOKEN})
    nses = len(json.loads(b)["sesiones"])
    p(f"abiertas={abiertas} cerradas_1013={r1013} motivos={sorted(map(str, motivos))} en {dt:.1f} s; "
      f"memoria {m0} K -> {m1} K (+{(m1 - m0) / 1024:.1f} MB); sesiones en memoria={nses}; "
      f"health antes={h0} despues={h1}")
    for c in conns:
        await c.close()
    await asyncio.sleep(2)
    p(f"tras cerrar: memoria {mem_kb()} K health={await health_ok(s)}")


async def main():
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0)) as s:
        p(f"hub pid={ns['PID']} memoria inicial {mem_kb()} K health={await health_ok(s)}")
        await ns["seccion_auth"](s)
        await ns["seccion_lecturas"](s)
        await fuera_de_contrato(s)
        ns["seccion_estaticos"]()
        await ns["seccion_origin"](s)
        await memoria(s)
        await flood(s, n, slugs_distintos=True)
        await flood(s, n, slugs_distintos=False)
        p(f"\nFIN health={await health_ok(s)} memoria {mem_kb()} K")


asyncio.run(main())
