"""Motor común de smoke y latencia: N productores (procesos) contra un hub + un cliente /ws por sesión.

Registra cada frame que llega al cliente con `t_receive` en qa/out/<tag>-<sid>.bus.jsonl y el log del
productor en qa/out/<tag>-<sid>.productor.log. El análisis se hace SIEMPRE desde esos archivos en disco.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.comun import OUT, PY, RAIZ, escuchar, guardar_jsonl, leer_jsonl, resumen  # noqa: E402
from qa.cobertura import cobertura_casete  # noqa: E402


def cmd_replay(casete, sid, port, velocidad=1.0):
    return [PY, "-m", "worker.replay", str(casete), "--hub", f"ws://localhost:{port}/ingest",
            "--sesion", sid, "--velocidad", str(velocidad)]


def cmd_real(clip, sid, lang, dur, casete_salida, port, tope_envio_s=None):
    c = [PY, "-m", "worker.run", "--archivo", str(clip), "--sesion", sid, "--lang", lang,
         "--duracion", str(dur), "--casete", str(casete_salida), "--hub", f"ws://localhost:{port}/ingest"]
    if tope_envio_s:  # tope DURO de audio enviado a la API por sesión (flag de worker.run)
        c += ["--tope-envio-s", str(tope_envio_s)]
    return c


async def correr(productores: list[dict], port: int, tag: str, timeout_s: float, log=print) -> list[dict]:
    """productor: {sid, lang, cmd, casete_cobertura}. Devuelve [{sid, exit, bus, productor_log, ...}]."""
    parar = {p["sid"]: asyncio.Event() for p in productores}
    regs = {p["sid"]: [] for p in productores}
    tareas = [asyncio.create_task(escuchar(f"ws://localhost:{port}/ws/{p['sid']}?lang={p['lang']}",
                                           regs[p["sid"]], parar[p["sid"]], reconectar=False))
              for p in productores]
    # el cliente se conecta ANTES que el productor: recibe init (state waiting) y todo en vivo
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10 and not all(any(e.get("ev") == "frame" for e in regs[p["sid"]]) for p in productores):
        await asyncio.sleep(0.05)
    procs = []
    for p in productores:
        lp = OUT / f"{tag}-{p['sid']}.productor.log"
        f = open(lp, "w", encoding="utf-8")
        f.write("CMD: " + " ".join(p["cmd"]) + "\n")
        f.flush()
        pr = subprocess.Popen(p["cmd"], cwd=str(RAIZ), stdout=f, stderr=subprocess.STDOUT,
                              env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        procs.append((p, pr, lp, f))
        log(f"[{tag}] productor {p['sid']} pid={pr.pid}: {' '.join(p['cmd'][1:])}")
    t0 = time.monotonic()
    while any(pr.poll() is None for _, pr, _, _ in procs) and time.monotonic() - t0 < timeout_s:
        await asyncio.sleep(0.2)
    for _, pr, _, _ in procs:
        if pr.poll() is None:
            log(f"[{tag}] TIMEOUT {timeout_s} s: mato pid={pr.pid}")
            pr.kill()
            pr.wait(10)
    # esperar el session_end en cada cliente (hasta 5 s)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5:
        if all(any(e.get("ev") == "frame" and e["frame"].get("type") == "session_end" for e in regs[p["sid"]])
               for p in productores):
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    for e in parar.values():
        e.set()
    await asyncio.gather(*tareas, return_exceptions=True)
    res = []
    for p, pr, lp, f in procs:
        f.close()
        bus = OUT / f"{tag}-{p['sid']}.bus.jsonl"
        guardar_jsonl(bus, [{"ev": "meta", "sid": p["sid"], "lang": p["lang"], "exit_productor": pr.returncode,
                             "cmd": p["cmd"], "casete_cobertura": p.get("casete_cobertura")}] + regs[p["sid"]])
        res.append({"sid": p["sid"], "exit": pr.returncode, "bus": str(bus), "productor_log": str(lp)})
    return res


def analizar_bus(bus_path, umbral_tramo: float = 10.0, esperar_replay: bool | None = None) -> dict:
    """Todo desde el archivo en disco. Devuelve métricas + lista de fallas."""
    reg = leer_jsonl(bus_path)
    meta = reg[0] if reg and reg[0].get("ev") == "meta" else {}
    sid = meta.get("sid")
    frames = [e for e in reg if e.get("ev") == "frame"]
    fallas = []
    if not frames or frames[0]["frame"].get("type") != "init":
        fallas.append("el primer frame no es init (o no llegó nada)")
    init = frames[0]["frame"] if frames else {}
    msgs = [e for e in frames[1:] if e["frame"].get("type") not in ("heartbeat", "init")]
    textos = [e for e in msgs if e["frame"].get("type") == "text"]
    if not textos:
        fallas.append("0 textos recibidos")
    otras = [e["frame"].get("session_id") for e in msgs if e["frame"].get("session_id") != sid]
    if otras:
        fallas.append(f"{len(otras)} mensajes de OTRA sesión: {sorted(set(map(str, otras)))}")
    seqs = [e["frame"].get("seq") for e in msgs if e["frame"].get("seq") is not None]
    base = init.get("last_seq")
    huecos = []
    prev = base
    for s in seqs:
        if prev is not None and s != prev + 1:
            huecos.append(f"{prev}->{s}")
        prev = s
    if huecos:
        fallas.append(f"seq no continuo: {huecos[:10]}")
    if not any(e["frame"].get("type") == "session_end" for e in msgs):
        fallas.append("no llegó session_end")
    if meta.get("exit_productor") != 0:
        fallas.append(f"productor exit={meta.get('exit_productor')}")
    if esperar_replay is not None:
        mal = [e["frame"].get("seq") for e in msgs if bool(e["frame"].get("replay")) != esperar_replay]
        if mal:
            fallas.append(f"{len(mal)} mensajes con replay != {esperar_replay}")
    lat = {"hub_mas_red": [], "hub_interno": [], "entrega_hub_cliente": [], "percibida": []}
    for e in textos:
        f, tr = e["frame"], e["t_receive"]
        if isinstance(f.get("t_emit"), (int, float)):
            lat["hub_mas_red"].append(tr - f["t_emit"])
            if isinstance(f.get("t_hub"), (int, float)):
                lat["hub_interno"].append(f["t_hub"] - f["t_emit"])
        if isinstance(f.get("t_hub"), (int, float)):
            lat["entrega_hub_cliente"].append(tr - f["t_hub"])
        if isinstance(f.get("t_captured"), (int, float)):
            lat["percibida"].append(tr - f["t_captured"])
    # percibida desde la PRIMERA ventana del bloque (lo que esperó la primera palabra): sólo en corridas REALES,
    # con el casete que grabó ESTA corrida (en replay el casete es de otra hora y no aplica)
    cc0 = meta.get("casete_cobertura")
    if "worker.run" in " ".join(meta.get("cmd") or []) and cc0 and Path(cc0).is_file():
        from qa.comun import leer_casete
        _, RR = leer_casete(cc0)
        venc = {r["payload"]["ventana"]: r["payload"].get("t_captured") for r in RR
                if r.get("dir") == "client" and r.get("kind") == "ventana"}
        prim = {}
        for r in RR:
            if r.get("dir") == "emit" and r.get("kind") == "text" and isinstance(r.get("ventanas"), list):
                ts = [venc.get(v) for v in r["ventanas"] if isinstance(venc.get(v), (int, float))]
                if ts:
                    prim[r["payload"].get("seq")] = min(ts)
        lat["percibida_primera_ventana"] = [e["t_receive"] - prim[e["frame"].get("seq")] for e in textos
                                            if e["frame"].get("seq") in prim]
    # rotaciones (B3: un mecanismo "reabrir con solape", meta.reason = motivo) y watchdog, tal como llegan al cliente
    rotaciones = [{"seq": e["frame"].get("seq"), "reason": (e["frame"].get("meta") or {}).get("reason"),
                   "meta": e["frame"].get("meta"), "t_receive": e["t_receive"]}
                  for e in msgs if e["frame"].get("type") == "rotation"]
    watchdogs = [{"seq": e["frame"].get("seq"), "meta": e["frame"].get("meta"), "t_receive": e["t_receive"]}
                 for e in msgs if e["frame"].get("type") == "watchdog"]
    # traducción EN VIVO (R19): evento `translation` con items {seq, text, ok}. Demora percibida de la traducción
    # = t_receive(translation) - t_captured(text del mismo seq). Sólo items ok:true entran al percentil.
    tcap = {e["frame"].get("seq"): e["frame"].get("t_captured") for e in textos}
    trecv_text = {e["frame"].get("seq"): e["t_receive"] for e in textos}
    n_ev = n_ok = n_nok = 0
    con_item, perc_tr, desde_text = set(), [], []
    for e in msgs:
        f = e["frame"]
        if f.get("type") != "translation":
            continue
        n_ev += 1
        for it in f.get("items") or []:
            s_ = it.get("seq")
            con_item.add(s_)
            if it.get("ok"):
                n_ok += 1
                if isinstance(tcap.get(s_), (int, float)):
                    perc_tr.append(e["t_receive"] - tcap[s_])
                if s_ in trecv_text:
                    desde_text.append(e["t_receive"] - trecv_text[s_])
            else:
                n_nok += 1
    traduccion = {"eventos": n_ev, "items_ok": n_ok, "items_ok_false": n_nok,
                  "textos_sin_ningun_item": sorted(s_ for s_ in tcap if s_ not in con_item),
                  "percibida_traduccion_s": resumen(perc_tr), "text_a_translation_s": resumen(desde_text)}
    # hueco en el eje de audio entre textos recibidos (informativo)
    rng = sorted((e["frame"]["audio_start"], e["frame"]["audio_end"]) for e in textos
                 if isinstance(e["frame"].get("audio_start"), (int, float)))
    gap_audio = max((b[0] - a[1] for a, b in zip(rng, rng[1:])), default=0.0)
    cob = None
    cc = meta.get("casete_cobertura")
    if cc and Path(cc).is_file():
        cob = cobertura_casete(cc, umbral_tramo)
        if cob["tramos_sin_texto_mayores_al_umbral"]:
            t = cob["tramos_sin_texto_mayores_al_umbral"]
            fallas.append(f"tramo con voz enviada y sin texto > {umbral_tramo} s: " +
                          ", ".join(f"{x['dur_audio_s']} s (audio {x['audio_start']}-{x['audio_end']})" for x in t))
    elif cc:
        fallas.append(f"no existe el casete para medir tramos sin texto: {cc}")
    return {"sid": sid, "bus": str(bus_path), "exit_productor": meta.get("exit_productor"),
            "init_last_seq": base, "init_state": init.get("state"), "mensajes": len(msgs), "textos": len(textos),
            "seq": [seqs[0], seqs[-1]] if seqs else None, "huecos_seq": huecos,
            "latencia_s": {k: resumen(v) for k, v in lat.items()},
            "rotaciones": rotaciones, "watchdogs": watchdogs, "traduccion": traduccion,
            "max_hueco_audio_entre_textos_s": round(gap_audio, 2),
            "cobertura": None if cob is None else {k: cob[k] for k in ("casete", "ventanas_con_voz", "ventanas_voz_con_texto",
                                                                         "fraccion_ventanas", "tramo_sin_texto_max_s")},
            "fallas": fallas, "ok": not fallas}
