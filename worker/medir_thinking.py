"""Medicion B3: latencia del modelo de texto con thinking 'minimal' (config B2) vs thinking APAGADO.

    python -m worker.medir_thinking --casete fixtures/casetes/b1-en-60s-rederivado.jsonl \
        --n 10 --modelo gemini-3.5-flash-lite --salida reportes/audio-pipeline-b3-thinking.log

Gasta 2*n llamadas REALES al modelo de texto (una linea por llamada en reportes/cuota-texto.log).
Las dos configuraciones se INTERCALAN (A B A B ...) para que la carga del server afecte igual a
ambas; mismos textos (lotes de 1-3 textos del casete, como en vivo) en el mismo orden para las dos.
Ritmo: una llamada cada --paso-s (5,5 s => ~11 RPM < 15 RPM del free tier); tope por llamada
--tope-s (30 s) para MEDIR la cola larga, no para cortarla a 15 s.
Config (google-genai 2.25, types.ThinkingConfig.model_fields = include_thoughts, thinking_budget,
thinking_level; ThinkingLevel = MINIMAL/LOW/MEDIUM/HIGH):
  A "minimal": ThinkingConfig(thinking_level="minimal")  (lo que usaba B2)
  B "off":     ThinkingConfig(thinking_budget=0)          (thinking desactivado)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime

from worker.casete import leer
from worker.traductor import FMT, LOG_TEXTO, parsear, prompt_lote

CONFIGS = {"minimal": {"thinking_level": "minimal"}, "off": {"thinking_budget": 0},
           "default": {}}


def lotes_del_casete(path: str, n: int) -> list[list[str]]:
    _, evs = leer(path)
    textos = [e["payload"]["text"] for e in evs if e.get("dir") == "emit" and e.get("kind") == "text"]
    out, i, tam = [], 0, [3, 2, 1]
    while len(out) < n and textos:
        k = tam[len(out) % 3]
        lote = textos[i:i + k] or textos[:k]
        out.append(lote)
        i = (i + k) % max(1, len(textos))
    return out


def pct(xs: list[float], p: float) -> float | None:
    """nearest-rank"""
    if not xs:
        return None
    s = sorted(xs)
    import math
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


async def una(cliente, modelo: str, cfg_nombre: str, lote: list[str], tope_s: float) -> dict:
    from google.genai import types
    kw = {}
    if CONFIGS[cfg_nombre]:
        kw["thinking_config"] = types.ThinkingConfig(**CONFIGS[cfg_nombre])
    cfg = types.GenerateContentConfig(temperature=0.2, response_mime_type="application/json",
                                      response_schema={"type": "ARRAY", "items": {"type": "STRING"}}, **kw)
    t0 = time.monotonic()
    estado, pensados, raw = "ok", None, ""
    try:
        r = await asyncio.wait_for(cliente.aio.models.generate_content(
            model=modelo, contents=prompt_lote(lote, "en", "es"), config=cfg), tope_s)
        raw = r.text or ""
        um = getattr(r, "usage_metadata", None)
        pensados = getattr(um, "thoughts_token_count", None) if um else None
        if parsear(raw, len(lote)) is None:
            estado = "cantidad"
    except asyncio.TimeoutError:
        estado = "timeout"
    except Exception as e:
        estado = f"error:{type(e).__name__}:{str(e)[:120]}"
    ms = int((time.monotonic() - t0) * 1000)
    with open(LOG_TEXTO, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime(FMT)} | {modelo} | {len(lote)} | "
                f"{estado.split(':')[0] if estado.startswith('error') else estado} | {ms}\n")
    return {"cfg": cfg_nombre, "items": len(lote), "estado": estado, "ms": ms,
            "thoughts_tokens": pensados, "hora": datetime.now().strftime(FMT)}


async def correr(a) -> int:
    from worker.gemini import cliente
    c = cliente()
    lotes = lotes_del_casete(a.casete, a.n)
    cfgs = a.configs.split(",")
    orden = [(cfg, lote) for lote in lotes for cfg in cfgs]       # intercalado A B A B
    tareas = []
    for i, (cfg, lote) in enumerate(orden):
        tareas.append(asyncio.create_task(una(c, a.modelo, cfg, lote, a.tope_s)))
        if i < len(orden) - 1:
            await asyncio.sleep(a.paso_s)
    res = await asyncio.gather(*tareas)
    with open(a.salida, "w", encoding="utf-8") as f:
        f.write(f"# worker.medir_thinking {datetime.now().strftime(FMT)} modelo={a.modelo} "
                f"casete={a.casete} n={a.n} paso_s={a.paso_s} tope_s={a.tope_s}\n")
        for r in res:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for cfg in cfgs:
            ms = [r["ms"] for r in res if r["cfg"] == cfg and r["estado"] == "ok"]
            tod = [r["ms"] for r in res if r["cfg"] == cfg]
            linea = (f"RESUMEN cfg={cfg} config={CONFIGS[cfg]} llamadas={len(tod)} ok={len(ms)} "
                     f"p50_ms(todas)={pct(tod, 50)} p95_ms(todas)={pct(tod, 95)} "
                     f"p50_ms(ok)={pct(ms, 50)} p95_ms(ok)={pct(ms, 95)} (nearest-rank)")
            f.write(linea + "\n")
            print(linea)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.medir_thinking")
    ap.add_argument("--casete", default="fixtures/casetes/b1-en-60s-rederivado.jsonl")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--modelo", default="gemini-3.5-flash-lite")
    ap.add_argument("--configs", default="minimal,off")
    ap.add_argument("--paso-s", type=float, default=5.5)
    ap.add_argument("--tope-s", type=float, default=30.0)
    ap.add_argument("--salida", default="reportes/audio-pipeline-b3-thinking.log")
    return asyncio.run(correr(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
