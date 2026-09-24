"""Umbral de atasco POR METRICA (B4): corre "reabrir con solape" con el TRANSPORTE DE CASETE (test,
sin API, rotulado) sobre los casetes largos REALES y mide, por perfil UMBRAL/SOSTENIDO:
- reaperturas (total, por motivo, y en 0-200 s de posicion de la fuente),
- "segundos con voz y sin texto" = audio de ventanas con voz no cubierto por ningun `text` emitido
  (qa/cobertura.py::cobertura_casete sobre el casete que graba la corrida: audio_voz_s - audio_voz_cubierto_s),
- tramo sin texto mas largo, y la suma de rotation.meta.audio_lost_s.

La fuente es una SENAL SINTETICA (tono con valles, no es voz) solo para que el Cortador arme ventanas;
lo que "dice el server" sale del casete real (worker/transporte_casete.py). Reloj virtual = posicion
de audio de la fuente.

    python -m worker.umbrales [--perfiles 22/8,15/6,12/5] [--mudo-es 60] [--salida-dir DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from qa.cobertura import _union, cobertura_casete  # noqa: E402  (herramienta de referencia de qa)
from worker.casete import Grabador  # noqa: E402
from worker.ingesta import SR, trocear  # noqa: E402
from worker.session import ConfigReabrir, SessionWorker  # noqa: E402
from worker.transporte_casete import FabricaCasete  # noqa: E402

CASETES = RAIZ / "fixtures" / "casetes"
EN = CASETES / "b1-nerdearla-en-intento2-quota.jsonl"
ES = CASETES / "b1-nerdearla-es-intento2-cancelled.jsonl"


def senal_sintetica(total_s: float) -> bytes:
    """Tono de 220 Hz 1,8 s + silencio 1,2 s (NO es voz): ventanas con has_voice=True."""
    rng = np.random.default_rng(7)
    partes, t = [], 0.0
    while t < total_s:
        for seg, amp in ((1.8, 4000), (1.2, 0)):
            n = int(round(seg * SR))
            x = amp * np.sin(2 * np.pi * 220 * np.arange(n) / SR) + rng.normal(0, 30, n)
            partes.append(np.clip(x, -32768, 32767).astype("<i2"))
            t += seg
    return np.concatenate(partes).tobytes()[: int(total_s * SR) * 2]


def correr(casete: Path, dur_s: float, cfg: ConfigReabrir, salida: Path, mudo=None, sid="umbral"):
    fab = FabricaCasete(str(casete), mudo)
    chs = list(trocear(senal_sintetica(dur_s), t0_epoch=0.0))
    caja = {}
    rec = Grabador(salida, {"session_id": sid, "lang": "xx", "generator": "worker.umbrales (transporte de casete)",
                            "replay_test": True, "casete_fuente": casete.name, "started_at": 0.0,
                            "cfg_reabrir": vars(cfg)})

    class _Bus:
        def __init__(self):
            self.msgs = []

        def publicar(self, m):
            self.msgs.append(m)

    bus = _Bus()

    async def fuente():
        for c in chs:
            yield c

    async def dormir(_s):
        await asyncio.sleep(0)

    cfg.drenaje_vieja_s, cfg.tick_s = 0.3, 0.05

    async def main():
        w = SessionWorker(sid, "en", fab(0.0), fuente(), grabador=rec, emisor=bus, heartbeat_s=10_000,
                          espera_final_s=0.3, reloj=lambda: caja["w"].pos_s, dormir=dormir,
                          log=lambda s: None, fabrica=fab, reabrir=cfg)
        caja["w"] = w
        return await w.correr()

    res = asyncio.run(main())
    rec.cerrar()
    return res, bus.msgs, fab


def fila(nombre: str, casete: Path, dur_s: float, u: float, s: float, dirsal: Path, mudo=None) -> dict:
    cfg = ConfigReabrir(atasco_umbral_s=u, atasco_sostenido_s=s)
    out = dirsal / f"umbral-{nombre}-{u:g}-{s:g}.jsonl"
    res, msgs, _ = correr(casete, dur_s, cfg, out, mudo=mudo)
    cob = cobertura_casete(str(out))
    rots = res.rotaciones
    sin_texto = round(cob["audio_voz_s"] - cob["audio_voz_cubierto_s"], 2)
    return {"escenario": nombre, "perfil": f"{u:g}/{s:g}", "dur_s": round(dur_s, 1),
            "reaperturas": len(rots),
            "reaperturas_0_200": sum(1 for r in rots if r["pos_s"] < 200.0),
            "por_motivo": {m: sum(1 for r in rots if r["reason"] == m) for m in sorted({r["reason"] for r in rots})},
            "posiciones": [(r["reason"], r.get("detalle"), r["pos_s"]) for r in rots],
            "voz_s": cob["audio_voz_s"], "voz_sin_texto_s": sin_texto,
            "voz_sin_texto_0_200_s": round(_union([(v["audio_start"], min(v["audio_end"], 200.0))
                                                    for v in cob["ventanas_sin_texto"]
                                                    if v["audio_start"] < 200.0]), 2),
            "tramo_max_s": cob["tramo_sin_texto_max_s"],
            "audio_lost_s_suma": round(sum(r.get("audio_lost_s") or 0.0 for r in rots), 2),
            "textos": res.textos, "dedup_descartados": res.dedup_descartados,
            "casete_salida": out.as_posix()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.umbrales")
    ap.add_argument("--perfiles", default="22/8,15/6,12/5")
    ap.add_argument("--mudo-es", type=float, default=None,
                    help="agrega el escenario ES con sesion muda desde S s de audio enviado")
    ap.add_argument("--salida-dir", default=None)
    a = ap.parse_args(argv)
    dirsal = Path(a.salida_dir or tempfile.mkdtemp(prefix="umbrales-"))
    dirsal.mkdir(parents=True, exist_ok=True)
    fin_en = FabricaCasete(str(EN)).guion.ventanas[-1][0] + 1.0 + 8.0
    fin_es = FabricaCasete(str(ES)).guion.ventanas[-1][0] + 1.0
    esc = [("en-quota", EN, fin_en, None), ("es-cancelled", ES, fin_es, None)]
    if a.mudo_es is not None:
        esc.append((f"es-mudo{a.mudo_es:g}", ES, 160.0, a.mudo_es))
    filas = []
    for p in a.perfiles.split(","):
        u, s = (float(x) for x in p.split("/"))
        for nombre, cas, dur, mudo in esc:
            f = fila(nombre, cas, dur, u, s, dirsal, mudo)
            filas.append(f)
            print(json.dumps(f, ensure_ascii=False), flush=True)
    print()
    print(f"{'escenario':14s} {'perfil':6s} {'reap':>4s} {'reap<200':>8s} {'voz_s':>7s} {'sin_texto_s':>11s} "
          f"{'sin_texto<200':>13s} {'tramo_max':>9s} {'lost_s':>7s}  posiciones")
    for f in filas:
        print(f"{f['escenario']:14s} {f['perfil']:6s} {f['reaperturas']:4d} {f['reaperturas_0_200']:8d} "
              f"{f['voz_s']:7.1f} {f['voz_sin_texto_s']:11.2f} {f['voz_sin_texto_0_200_s']:13.2f} "
              f"{f['tramo_max_s']:9.2f} {f['audio_lost_s_suma']:7.2f}  {f['posiciones']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
