"""[SIM] Capacidad del traductor con N salas y K keys en UNA maquina (FINAL 25/09; adaptado de
qa/out/adv-final/e4_traductor_sim.py). NO llama a Gemini: el "modelo" es un transporte FALSO rotulado
[SIM] que responde tras una demora sorteada de las demoras REALES (ms de los intentos ok guardados en
fixtures/casetes/). Todo lo demas es el codigo real: Traductor (lotes 2/4 s, hedge), Limitador (12 RPM
por modelo, 2 modelos, espera de cupo 15 s) y Reservas con lock en UN archivo compartido por todas las
salas del grupo; la sala i usa la key KEY_{i % K} (solo el NOMBRE: no hay keys reales).
Cadencia de textos: la de un casete real (fixtures/casetes/b3-ab-manual.jsonl) en bucle, desfasada.

    python -m worker.tests.sim_salas_traductor --salas 4 --keys 2 --duracion 90 --grupo g4k2 [--hedge-s 4]

Imprime una linea JSON (cobertura, motivos de ok:false, p50/p95 texto->traduccion, reservas max en
60 s por (key, modelo)). No es un test de pytest (tarda --duracion segundos reales).
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MODELOS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def _p(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 2)


def demoras_reales_ms() -> list[int]:
    out = []
    for p in sorted(glob.glob(str(RAIZ / "fixtures/casetes/**/*.jsonl"), recursive=True)):
        with open(p, encoding="utf-8") as f:
            for ln in f:
                if '"translation"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                src = ((r.get("payload") or {}).get("meta") or {}).get("source")
                if isinstance(src, dict) and src.get("vivo"):
                    for it in src.get("intentos") or []:
                        if it.get("estado") == "ok" and isinstance(it.get("ms"), int):
                            out.append(it["ms"])
    return out


def cadencia(p=RAIZ / "fixtures/casetes/b3-ab-manual.jsonl") -> list[tuple[float, str]]:
    tx = []
    for ln in p.read_text(encoding="utf-8").splitlines()[1:]:
        r = json.loads(ln)
        if r.get("dir") == "emit" and r.get("kind") == "text":
            tx.append((r["payload"]["t_emit"], r["payload"]["text"]))
    t0 = tx[0][0]
    return [(t - t0, x) for t, x in tx]


class ModeloSimulado:
    """[SIM] NO es Gemini."""

    def __init__(self, demoras_ms, rnd):
        self.d, self.rnd = demoras_ms, rnd

    async def generar(self, modelo, prompt):
        await asyncio.sleep(self.rnd.choice(self.d) / 1000.0)
        arr = json.loads(prompt.rsplit("Input:\n", 1)[1])
        return json.dumps(["[SIM] " + x for x in arr], ensure_ascii=False)


async def sala(tr, cad, duracion, desfase, reg):
    tr.iniciar()
    t_ini = time.time()
    total = cad[-1][0] + 2.4
    seq, k = 0, 0
    while True:
        vuelta, j = divmod(k, len(cad))
        t_obj = t_ini + desfase + vuelta * total + cad[j][0]
        if t_obj - t_ini > duracion:
            break
        esp = t_obj - time.time()
        if esp > 0:
            await asyncio.sleep(esp)
        seq += 1
        reg["t_texto"][seq] = time.time()
        tr.agregar(seq, cad[j][1], time.time())
        k += 1
    await tr.cerrar(timeout=25.0)


async def correr(a, d: Path) -> dict:
    from worker.traductor import Traductor
    log = d / "cuota-texto.log"
    dem, cad = demoras_reales_ms(), cadencia()
    regs, trs = [], []
    for i in range(a.salas):
        reg = {"t_texto": {}, "res": []}

        def on_res(res, reg=reg):
            t = time.time()
            for it in res.items:
                reg["res"].append({"seq": it["seq"], "ok": it["ok"], "t": t, "reason": res.reason})
        tr = Traductor("en", "es", transporte=ModeloSimulado(dem, random.Random(a.semilla * 100 + i)),
                       modelos=MODELOS, log=log, timeout_s=20.0, on_resultado=on_res,
                       logger=lambda s: None, hedge_s=a.hedge_s, key=f"KEY_{i % a.keys}")
        regs.append(reg)
        trs.append(tr)
    rnd = random.Random(a.semilla)
    t0 = time.time()
    await asyncio.gather(*(sala(trs[i], cad, a.duracion, rnd.uniform(0, 3), regs[i]) for i in range(a.salas)))
    t1 = time.time()
    salas = []
    for i, reg in enumerate(regs):
        primero = {}
        for r in reg["res"]:
            primero.setdefault(r["seq"], r)
        n = len(reg["t_texto"])
        ok = [s for s, r in primero.items() if r["ok"]]
        mot = {}
        for r in primero.values():
            if not r["ok"]:
                mot[r["reason"] or "?"] = mot.get(r["reason"] or "?", 0) + 1
        dem_ok = [primero[s]["t"] - reg["t_texto"][s] for s in ok]
        salas.append({"sala": i, "key": trs[i].key, "textos": n, "ok": len(ok),
                      "ok_frac": round(len(ok) / n, 3) if n else None, "ok_false": mot,
                      "demora_ok_p50": _p(dem_ok, .5), "demora_ok_p95": _p(dem_ok, .95),
                      "llamadas": trs[i].n_llamadas, "cubiertos": trs[i].n_cubiertos})
    res_f = d / "cuota-texto-reservas.jsonl"
    reservas = [json.loads(x) for x in res_f.read_text(encoding="utf-8").splitlines() if x.strip()]
    maxv = {}
    for key in sorted({r["key"] for r in reservas}):
        for m in MODELOS:
            ts = sorted(r["t"] for r in reservas if r["modelo"] == m and r["key"] == key)
            best, j = 0, 0
            for i2, t in enumerate(ts):
                while ts[j] < t - 60.0:
                    j += 1
                best = max(best, i2 - j + 1)
            maxv[f"{key}|{m}"] = best
    tn, tok = sum(s["textos"] for s in salas), sum(s["ok"] for s in salas)
    return {"SIMULACION": "[SIM] transporte falso; demoras sorteadas de casetes reales", "grupo": a.grupo,
            "salas": a.salas, "keys": a.keys, "hedge_s": a.hedge_s, "duracion_s": a.duracion,
            "reloj_s": round(t1 - t0, 1), "demoras_reales_n": len(dem), "textos": tn, "ok": tok,
            "cobertura": round(tok / tn, 3) if tn else None,
            "demora_ok_p95_por_sala": [s["demora_ok_p95"] for s in salas],
            "reservas_max_60s_por_key_modelo": maxv, "por_sala": salas}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salas", type=int, required=True)
    ap.add_argument("--keys", type=int, default=1)
    ap.add_argument("--duracion", type=float, default=90.0)
    ap.add_argument("--grupo", required=True)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--hedge-s", type=float, default=4.0)
    a = ap.parse_args(argv)
    d = Path(tempfile.gettempdir()) / f"sim-salas-{a.grupo}"
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*"):
        f.unlink()
    out = asyncio.run(correr(a, d))
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
