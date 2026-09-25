"""Cadencia de envio y comportamiento del server en un casete (diagnostico corrida doble, 25/09).

    python -m worker.cadencia casete1.jsonl [casete2.jsonl ...]      # una linea JSON por casete

La fuente se pauta EN EL MISMO LOOP de asyncio que envia el audio (ingesta.py: espera = t0 +
(idx+1)*0,1 - loop.time(); t_captured = time.time() al entregar el chunk). Si algo bloquea el loop
N s, los chunks salen tarde y en rafaga: `tarde` de una ventana = t_captured del ultimo chunk -
audio_end - min(t_captured - audio_end) del casete. Un loop sano da tarde ~0; una rotacion da tarde
porque el envio espera a la conexion nueva (por diseno). Por eso se separa ANTES del primer pedido de
reapertura (causa posible del atasco) de DESPUES (consecuencia).

Campos: tarde_max_antes_s / n_tarde_antes (>0,3 s) · tarde_max_despues_s · lag_envio_max_s
(activity_end enviado - t_captured) · primer_pedido (t relativo, motivo, detalle, audio_s, atraso_s)
· c1 (conexion 1 hasta el primer pedido: mensajes server, con texto, ultimo audioOffset y cuando)
· server_hueco_max_s · rotaciones · textos · primer_texto_s. Sin API.
"""
from __future__ import annotations

import json
import sys


def _off(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).rstrip("s"))
    except ValueError:
        return None


def analizar(ruta: str) -> dict:
    t0 = None
    vs, srv, textos, rot = [], [], [], []
    pedido = None
    c1 = {"mensajes": 0, "con_texto": 0, "ultimo_offset_s": None, "t_ultimo_offset": None}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            d = json.loads(linea)
            if "dir" not in d:
                t0 = d.get("started_at")
                continue
            p = d.get("payload") or {}
            di, k = d["dir"], d.get("kind")
            if di == "client" and k == "ventana" and not p.get("reenvio") and p.get("t_captured"):
                vs.append((p["t_captured"], p["audio_end"], d["t"]))
            elif di == "client" and k == "reabrir_pedido" and pedido is None:
                pedido = {"t": d["t"], "motivo": p.get("reason"), "detalle": p.get("detalle"),
                          "audio_s": p.get("audio_s"), "atraso_s": p.get("atraso_s")}
            elif di == "server":
                srv.append(d["t"])
                if d.get("conexion") == "c1" and pedido is None:
                    c1["mensajes"] += 1
                    sc = p.get("serverContent") or {}
                    if (sc.get("inputTranscription") or {}).get("text"):
                        c1["con_texto"] += 1
                    off = _off((p.get("voiceActivity") or {}).get("audioOffset"))
                    if off is not None:
                        c1["ultimo_offset_s"] = off
                        c1["t_ultimo_offset"] = d["t"]
            elif di == "emit" and k == "text":
                textos.append(d["t"])
            elif di == "emit" and k == "rotation":
                m = p.get("meta") or {}
                rot.append({"detalle": m.get("detalle"), "audio_lost_s": m.get("audio_lost_s")})
    t0 = t0 or (vs[0][0] if vs else 0.0)
    base = min((tc - ae for tc, ae, _ in vs), default=0.0)
    tp = pedido["t"] if pedido else float("inf")
    antes = [tc - ae - base for tc, ae, _ in vs if tc < tp]
    despues = [tc - ae - base for tc, ae, _ in vs if tc >= tp]
    rel = lambda t: None if t is None else round(t - t0, 1)
    if pedido:
        pedido = {**pedido, "t": rel(pedido["t"])}
    c1["t_ultimo_offset"] = rel(c1["t_ultimo_offset"])
    gaps = [b - a for a, b in zip(srv, srv[1:])]
    return {
        "casete": ruta.replace("\\", "/").split("/")[-1],
        "ventanas": len(vs),
        "tarde_max_antes_s": round(max(antes), 3) if antes else None,
        "n_tarde_antes": sum(1 for x in antes if x > 0.3),
        "tarde_max_despues_s": round(max(despues), 3) if despues else None,
        "lag_envio_max_s": round(max((t - tc for tc, _, t in vs), default=0.0), 3),
        "primer_pedido": pedido,
        "c1": c1,
        "server_hueco_max_s": round(max(gaps), 2) if gaps else None,
        "rotaciones": rot,
        "textos": len(textos),
        "primer_texto_s": rel(textos[0]) if textos else None,
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for r in argv:
        try:
            print(json.dumps(analizar(r), ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"casete": r, "error": f"{type(e).__name__}: {e}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
