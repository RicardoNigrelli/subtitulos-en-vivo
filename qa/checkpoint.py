"""Checkpoint MVP (B5) desde archivos en disco: una fila por sesión de un smoke REAL (qa/smoke.py).

    .venv/Scripts/python qa/checkpoint.py --tag b5r --sids qa-b5r-en,qa-b5r-es [--umbral-tramo 10]

Lee qa/out/smoke-<tag>-<sid>.bus.jsonl (cliente /ws, t_receive), .casete.jsonl (lo que grabó worker.run) y
.productor.log (resumen JSON final de worker.run). No corre nada. Por sesión:
textos; tramos con voz enviada y sin texto > umbral (todos, con duración y rango de audio); seq continuo desde
init.last_seq; rotaciones (motivo, segundo de audio, audio_lost_s, reenvío); cobertura (qa/cobertura.py);
latencia percibida t_receive - t_captured p50/p95 nearest-rank; R19: traducciones ok/total y atraso p50/p95
(worker.medir_traduccion.medir sobre el casete, sólo lectura) + lo que vio el cliente; errores (resumen del
productor, tipos de frame, fallas de analizar_bus). Segundos de audio desde reportes/cuota-audio.log.
Salida qa/out/checkpoint-<tag>.json y .log. Exit 0 = calculado; 1 = falta algún archivo o no hay textos.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.bus import analizar_bus  # noqa: E402
from qa.cobertura import cobertura_casete  # noqa: E402
from qa.comun import OUT, RAIZ, ahora_ar, leer_jsonl  # noqa: E402
from worker.medir_traduccion import medir  # noqa: E402  (sólo lectura del casete)


def resumen_productor(path: Path) -> dict:
    for ln in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        ln = ln.strip()
        if ln.startswith("{") and '"segundos_enviados"' in ln:
            try:
                return json.loads(ln)
            except json.JSONDecodeError:
                return {}
    return {}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sids", required=True)
    ap.add_argument("--umbral-tramo", type=float, default=10.0)
    a = ap.parse_args(argv)
    lineas: list[str] = []
    filas: list[dict] = []
    rc = 0

    def log(s: str) -> None:
        print(s, flush=True)
        lineas.append(s)

    log(f"checkpoint {ahora_ar()} tag={a.tag} sids={a.sids} umbral_tramo={a.umbral_tramo}")
    cuota = RAIZ / "reportes" / "cuota-audio.log"
    cuota_l = cuota.read_text(encoding="utf-8").splitlines() if cuota.is_file() else []
    for sid in [s for s in a.sids.split(",") if s]:
        bus = OUT / f"smoke-{a.tag}-{sid}.bus.jsonl"
        cas = OUT / f"smoke-{a.tag}-{sid}.casete.jsonl"
        plog = OUT / f"smoke-{a.tag}-{sid}.productor.log"
        falta = [str(p) for p in (bus, cas, plog) if not p.is_file()]
        if falta:
            log(f"FALTA {sid}: {falta}")
            rc = 1
            continue
        r = analizar_bus(bus, a.umbral_tramo, esperar_replay=False)
        cob = cobertura_casete(cas, a.umbral_tramo)
        tr = medir(str(cas))
        prod = resumen_productor(plog)
        frames = [e["frame"] for e in leer_jsonl(bus) if e.get("ev") == "frame"]
        tipos = Counter(f.get("type") for f in frames)
        rot = []
        for x in r["rotaciones"]:
            m = x["meta"] or {}
            rot.append({"seq": x["seq"], "motivo": x["reason"], "detalle": m.get("detalle"),
                        "audio_s_conexion_vieja": m.get("audio_s"), "pos_s": m.get("pos_s"), "corte_s": m.get("corte_s"),
                        "audio_lost_s": m.get("audio_lost_s"), "reenvio_s": m.get("reenvio_s"),
                        "conectar_s": m.get("conectar_s"), "old_id": m.get("old_id"), "new_id": m.get("new_id")})
        seg = [float(ln.split("|")[2]) for ln in cuota_l
               if len(ln.split("|")) >= 4 and ln.split("|")[1].strip() == sid]
        lp = r["latencia_s"]["percibida"]
        tramos = cob["tramos_sin_texto_mayores_al_umbral"]
        fila = {"sid": sid, "textos": r["textos"], "mensajes": r["mensajes"], "seq": r["seq"],
                "init_last_seq": r["init_last_seq"], "huecos_seq": r["huecos_seq"],
                "tramos_mudos_gt_umbral": len(tramos), "tramos_mudos_detalle": tramos,
                "tramo_sin_texto_max_s": cob["tramo_sin_texto_max_s"],
                "rotaciones": rot, "watchdogs": r["watchdogs"],
                "cobertura": {k: cob[k] for k in ("ventanas_con_voz", "ventanas_voz_con_texto", "fraccion_ventanas")},
                "latencia_percibida_s": lp,
                "traduccion_casete": tr, "traduccion_cliente": r["traduccion"],
                "tipos_de_frame": dict(tipos), "errores_productor": prod.get("errores"),
                "resumen_productor": {k: prod.get(k) for k in (
                    "segundos_enviados", "ventanas", "textos", "motivo_fin", "goaway", "cierre", "seq_final",
                    "duracion_s", "rotaciones", "reenviados_s", "traduccion", "dedup")},
                "exit_productor": r["exit_productor"], "fallas_smoke": r["fallas"],
                "audio_registrado_s": round(sum(seg), 1)}
        filas.append(fila)
        if not r["textos"]:
            rc = 1
        log(f"{sid}: textos={r['textos']} seq={r['seq']} init.last_seq={r['init_last_seq']} "
            f"huecos_seq={r['huecos_seq'] or 0} exit_productor={r['exit_productor']} motivo_fin={prod.get('motivo_fin')}")
        log(f"   tramos con voz enviada y sin texto > {a.umbral_tramo} s: {len(tramos)} "
            + ", ".join(f"{t.get('dur_audio_s')} s (audio {t.get('audio_start')}-{t.get('audio_end')})" for t in tramos)
            + f" | máximo {cob['tramo_sin_texto_max_s']} s")
        log(f"   rotaciones={len(rot)}: " + "; ".join(
            f"seq {x['seq']} {x['motivo']}/{x['detalle']} pos_s={x['pos_s']} corte_s={x['corte_s']} "
            f"audio_lost_s={x['audio_lost_s']} reenvio_s={x['reenvio_s']} conectar_s={x['conectar_s']}" for x in rot))
        log(f"   cobertura: {cob['ventanas_voz_con_texto']}/{cob['ventanas_con_voz']} = {cob['fraccion_ventanas']}")
        log(f"   latencia percibida (t_receive - t_captured) p50={lp['p50']} p95={lp['p95']} max={lp.get('max')} n={lp['n']}")
        log(f"   R19 casete (worker.medir_traduccion): ok {tr['ok']}/{tr['textos']} = {tr['ok_frac']} "
            f"atraso p50={tr['atraso_ok_p50_s']} p95={tr['atraso_ok_p95_s']} max={tr['atraso_ok_max_s']} "
            f"sin_item={tr['sin_item']} intentos={tr['intentos_por_estado']}")
        tc = r["traduccion"]
        log(f"   R19 cliente /ws: eventos={tc['eventos']} items ok={tc['items_ok']} ok:false={tc['items_ok_false']} "
            f"percibida p50={tc['percibida_traduccion_s']['p50']} p95={tc['percibida_traduccion_s']['p95']} "
            f"n={tc['percibida_traduccion_s']['n']}")
        log(f"   errores productor={prod.get('errores')} | tipos de frame={dict(tipos)}")
        log(f"   traductor={prod.get('traduccion')}")
        log(f"   audio registrado (reportes/cuota-audio.log)={sum(seg):.1f} s | fallas_smoke={r['fallas']}")
    tot = sum(f["audio_registrado_s"] for f in filas)
    log(f"AUDIO REGISTRADO total {tot:.1f} s = {tot / 60:.2f} min | exit={rc}")
    (OUT / f"checkpoint-{a.tag}.json").write_text(
        json.dumps({"generado": ahora_ar(), "tag": a.tag, "exit": rc, "sesiones": filas}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (OUT / f"checkpoint-{a.tag}.log").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
