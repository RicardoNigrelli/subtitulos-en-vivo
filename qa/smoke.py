"""Smoke de N sesiones en simultáneo contra el hub, visto desde un cliente /ws por sesión.

  Replay (sin API, rotulado):  .venv/Scripts/python qa/smoke.py --sesiones 2 --replay fixtures/casetes/a.jsonl,fixtures/casetes/b.jsonl --tag b2-replay
  Real (ASR, GASTA CUOTA):      .venv/Scripts/python qa/smoke.py --sesiones 2 --duracion 60 --clips a.wav,b.wav --tag b3 --api
  Recalcular desde disco:       .venv/Scripts/python qa/smoke.py --tag b2-replay --recalcular [--replay ...]

Productores: `worker.replay` (modo --replay) o `worker.run` (real; exige --api para no gastar cuota por
accidente). session_id distinto por sesión (qa-<tag>-<i>). Hub: por defecto levanta uno PROPIO en --puerto
(8104) si está libre; con --hub-externo usa el que ya contesta en ese puerto.
Chequea por sesión: init; textos > 0; seq continuo desde init.last_seq; nada de otra sesión; session_end;
exit 0 del productor; replay=true/false según modo; NINGÚN tramo de ventanas con voz enviadas y sin texto
> --umbral-tramo s (capa client del casete: el de origen en replay, el que graba worker.run en real);
p50/p95 de latencia percibida (t_receive - t_captured) por sesión, nearest-rank, nunca promedio.
Salida: qa/out/smoke-<tag>.log y .json. Exit 0 sólo si TODO pasa; 1 si algo falla; 2 uso/hub.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.bus import analizar_bus, cmd_real, cmd_replay, correr  # noqa: E402
from qa.comun import OUT, ahora_ar, health, levantar_hub, matar, puerto_ocupado  # noqa: E402


def _lang(path: str, cab_lang: str | None = None) -> str:
    if cab_lang:
        return cab_lang
    m = re.search(r"-(en|es)-", Path(path).name)
    return m.group(1) if m else "en"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--sesiones", type=int, default=2)
    ap.add_argument("--duracion", type=float, default=60.0)
    ap.add_argument("--clips", default="")
    ap.add_argument("--langs", default="", help="en,es (default: se infiere del nombre del clip)")
    ap.add_argument("--replay", default="", help="casete1,casete2 (modo replay, sin API)")
    ap.add_argument("--velocidad", type=float, default=1.0, help="sólo replay")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--puerto", type=int, default=8104)
    ap.add_argument("--hub-externo", action="store_true")
    ap.add_argument("--umbral-tramo", type=float, default=10.0)
    ap.add_argument("--api", action="store_true", help="obligatorio para el modo real (gasta cuota de Gemini)")
    ap.add_argument("--tope-envio-s", type=float, default=None,
                    help="sólo real: tope duro de audio enviado por sesión (se pasa a worker.run --tope-envio-s)")
    ap.add_argument("--recalcular", action="store_true")
    ap.add_argument("--sids", default="", help="session_id por sesión, separados por coma (default qa-<tag>-<i>)")
    ap.add_argument("--inicio", type=float, default=0.0, help="sólo real: segundo de inicio en la fuente (worker.run --inicio)")
    a = ap.parse_args(argv)
    modo = "replay" if a.replay else "real"
    tag = a.tag or ("b2-replay" if modo == "replay" else "real")
    tag = re.sub(r"[^a-z0-9_-]", "-", tag.lower())
    suf = ".recalculo" if a.recalcular else ""  # el recálculo no pisa el log de la corrida
    log_path, json_path = OUT / f"smoke-{tag}{suf}.log", OUT / f"smoke-{tag}{suf}.json"
    sids = [x for x in a.sids.split(",") if x]

    def sid_de(i):
        return sids[i] if i < len(sids) else f"qa-{tag}-{i + 1}"[:64]

    def buses_de_disco():
        if sids:
            return [str(OUT / f"smoke-{tag}-{s_}.bus.jsonl") for s_ in sids if (OUT / f"smoke-{tag}-{s_}.bus.jsonl").is_file()]
        return sorted(glob.glob(str(OUT / f"smoke-{tag}-qa-{tag}-[0-9]*.bus.jsonl")))
    OUT.mkdir(parents=True, exist_ok=True)
    lineas: list[str] = []

    def log(s):
        print(s, flush=True)
        lineas.append(s)

    def cerrar(rc):
        Path(log_path).write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return rc

    log(f"smoke {ahora_ar()} modo={modo} tag={tag} argv={' '.join(sys.argv[1:] if argv is None else argv)}")
    hub = None
    if not a.recalcular:
        prods = []
        if modo == "replay":
            fuentes = [x for x in a.replay.split(",") if x]
            for i in range(a.sesiones):
                c = fuentes[i % len(fuentes)]
                sid = sid_de(i)
                cab = json.loads(Path(c).read_text(encoding="utf-8").splitlines()[0])
                prods.append({"sid": sid, "lang": _lang(c, cab.get("lang")), "casete_cobertura": c,
                              "cmd": cmd_replay(c, sid, a.puerto, a.velocidad)})
            timeout = 900
        else:
            if not a.api:
                log("MODO REAL sin --api: no se lanza nada (protege la cuota). exit 2")
                return cerrar(2)
            clips = [x for x in a.clips.split(",") if x]
            langs = [x for x in a.langs.split(",") if x]
            if not clips:
                log("faltan --clips. exit 2")
                return cerrar(2)
            for i in range(a.sesiones):
                c = clips[i % len(clips)]
                sid = sid_de(i)
                lang = langs[i % len(langs)] if langs else _lang(c)
                cas = OUT / f"smoke-{tag}-{sid}.casete.jsonl"
                prods.append({"sid": sid, "lang": lang, "casete_cobertura": str(cas),
                              "cmd": cmd_real(c, sid, lang, a.duracion, cas, a.puerto, a.tope_envio_s, a.inicio)})
            timeout = a.duracion + 120
            log(f"AUDIO A GASTAR (aprox): {a.sesiones} x {a.duracion:.0f} s = {a.sesiones * a.duracion / 60:.2f} min "
                f"(el contador oficial es `python -m worker.cuota`)")
        if a.hub_externo:
            if not health(a.puerto):
                log(f"--hub-externo pero nada contesta /health en {a.puerto}. exit 2")
                return cerrar(2)
            log(f"hub externo en {a.puerto}")
        else:
            oc = puerto_ocupado(a.puerto)
            if oc:
                log(f"puerto {a.puerto} OCUPADO ({oc}): no levanto hub. exit 2")
                return cerrar(2)
            hub = levantar_hub(a.puerto, OUT / f"smoke-{tag}.hub.log")
            log(f"hub PROPIO pid={hub.pid} en {a.puerto} (log qa/out/smoke-{tag}.hub.log)")
        try:
            for p in buses_de_disco():
                Path(p).unlink()
            asyncio.run(correr(prods, a.puerto, f"smoke-{tag}", timeout, log=log))
        finally:
            if hub is not None:
                matar(hub)
                log(f"hub propio detenido (pid={hub.pid})")
    buses = buses_de_disco()
    if not buses:
        log("no hay archivos .bus.jsonl para este tag. exit 1")
        return cerrar(1)
    res = [analizar_bus(b, a.umbral_tramo, esperar_replay=(modo == "replay")) for b in buses]
    for r in res:
        lp = r["latencia_s"]["percibida"]
        hr = r["latencia_s"]["hub_mas_red"]
        tmax = None if not r["cobertura"] else r["cobertura"]["tramo_sin_texto_max_s"]
        log(f"{'OK   ' if r['ok'] else 'FALLA'} {r['sid']}: textos={r['textos']} mensajes={r['mensajes']} seq={r['seq']} "
            f"init.last_seq={r['init_last_seq']} exit_productor={r['exit_productor']} "
            f"percibida p50={lp['p50']} p95={lp['p95']} (n={lp['n']}) hub+red p50={hr['p50']} p95={hr['p95']} "
            f"tramo_sin_texto_max={tmax} s")
        pv = r["latencia_s"].get("percibida_primera_ventana")
        if pv:
            log(f"      percibida desde la PRIMERA ventana del bloque p50={pv['p50']} p95={pv['p95']} max={pv['max']} (n={pv['n']})")
        cob = r["cobertura"] or {}
        log(f"      cobertura (qa/cobertura.py sobre {cob.get('casete')}): ventanas con voz={cob.get('ventanas_con_voz')} "
            f"con texto={cob.get('ventanas_voz_con_texto')} fraccion={cob.get('fraccion_ventanas')}")
        log(f"      rotaciones={len(r['rotaciones'])} {[(x['seq'], x['reason']) for x in r['rotaciones']]} "
            f"watchdog={len(r['watchdogs'])}")
        tr = r["traduccion"]
        log(f"      traduccion: eventos={tr['eventos']} items ok={tr['items_ok']} ok:false={tr['items_ok_false']} "
            f"textos sin item={tr['textos_sin_ningun_item']} | percibida traducción p50={tr['percibida_traduccion_s']['p50']} "
            f"p95={tr['percibida_traduccion_s']['p95']} (n={tr['percibida_traduccion_s']['n']}) | text->translation "
            f"p50={tr['text_a_translation_s']['p50']} p95={tr['text_a_translation_s']['p95']}")
        for f in r["fallas"]:
            log(f"      - {f}")
    if modo == "real":  # minutos de audio que el worker registró para ESTAS sesiones (reportes/cuota-audio.log)
        sids = {r["sid"] for r in res}
        cl = Path(__file__).resolve().parent.parent / "reportes" / "cuota-audio.log"
        filas = [ln for ln in (cl.read_text(encoding="utf-8").splitlines() if cl.is_file() else [])
                 if len(ln.split("|")) >= 4 and ln.split("|")[1].strip() in sids]
        tot = sum(float(ln.split("|")[2]) for ln in filas)
        for ln in filas:
            log(f"      cuota-audio.log: {ln}")
        log(f"AUDIO REGISTRADO para {sorted(sids)}: {tot:.1f} s = {tot / 60:.2f} min ({len(filas)} líneas)")
    ok = bool(res) and all(r["ok"] for r in res) and (a.recalcular or len(res) >= a.sesiones)
    rc = 0 if ok else 1
    if modo == "replay":
        log("NOTA: en replay, 'percibida' = latencia del worker grabada en el casete (t_emit - t_captured, que el "
            "replay conserva) + sobrecosto hub+red medido ahora. NO es ASR real; la percibida real sale de B3/B5.")
    log(f"RESULTADO: {'OK' if ok else 'FALLA'} sesiones={len(res)} exit={rc}")
    json_path.write_text(json.dumps({"generado": ahora_ar(), "modo": modo, "tag": tag, "exit": rc,
                                     "sesiones": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    return cerrar(rc)


if __name__ == "__main__":
    sys.exit(main())
