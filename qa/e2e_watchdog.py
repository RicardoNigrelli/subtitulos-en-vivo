"""e2e del WATCHDOG sin API: worker.run con transporte de casete rotulado que se queda MUDO desde S s.

    .venv/Scripts/python qa/e2e_watchdog.py [--puerto 8100] [--tag b3] [--mudo 60] [--tope-s 15]
    .venv/Scripts/python qa/e2e_watchdog.py --tag b3 --recalcular      # desde qa/out/, no corre nada

Qué hace: un cliente /ws sobre el hub (se conecta ANTES que el productor) y un `python -m worker.run
--transporte casete:<casete>:mudo=<S> ...` hablando con el hub por /ingest (bus real, sin mocks de qa).
Sin API por construcción: `--traducir-a none`, GEMINI_API_KEY y GEMINI_API_KEY_RESERVA vacías en el
entorno del worker, y CUOTA_LOG apuntando a qa/out/ (así una corrida de casete no ensucia
reportes/cuota-audio.log; si el worker igual registra segundos, queda a la vista en ese archivo).

Chequeos (todos desde los archivos en disco: .bus.jsonl del cliente y el casete que graba el worker):
  1. llega un `rotation` con meta.reason == "atasco" con t_receive en [T_mudo, T_mudo + tope] s, donde
     T_mudo = reloj en que se capturó el audio del segundo S (ventanas del casete grabado; si no hay,
     t_captured - audio_end de los textos previos);
  2. después de ese rotation vuelve a llegar al menos un `text` (seq mayor);
  3. seq continuo desde init.last_seq (tipos con seq); nada de otra sesión; session_end;
  4. el rotation valida contra el contrato (contracts.errores);
  5. exit 0 del worker.
Salida: qa/out/watchdog-<tag>.log y .json (+ .bus.jsonl, .productor.log, .casete.jsonl, .cuota.log).
Exit: 0 todo pasa · 1 algún chequeo falla · 2 precondición (no existe --transporte, hub caído, uso).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qa.comun import OUT, PY, RAIZ, ahora_ar, escuchar, guardar_jsonl, health, leer_jsonl  # noqa: E402

CASETE_DEF = "fixtures/casetes/b1-nerdearla-es-intento2-cancelled.jsonl"
AUDIO_DEF = "fixtures/audio/full/nerdearla-es-paez.m4a"


def tiene_transporte() -> tuple[bool, str]:
    r = subprocess.run([PY, "-m", "worker.run", "--help"], cwd=str(RAIZ), capture_output=True, text=True,
                       timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return ("--transporte" in r.stdout), r.stdout


def t_mudo_desde_casete(casete: Path, mudo: float) -> tuple[float | None, str]:
    """Reloj en que entró la muestra del segundo `mudo` del audio: ventanas del casete grabado."""
    if not casete.is_file():
        return None, "sin casete grabado"
    try:
        L = leer_jsonl(casete)
    except Exception as e:  # casete a medio escribir
        return None, f"casete ilegible: {e}"
    vs = [r["payload"] for r in L[1:] if r.get("dir") == "client" and r.get("kind") == "ventana"
          and isinstance(r.get("payload", {}).get("t_captured"), (int, float))]
    for v in vs:
        if v.get("audio_end", 0) >= mudo:
            return v["t_captured"] - (v["audio_end"] - mudo), f"ventana {v.get('ventana')} (audio {v.get('audio_start')}-{v.get('audio_end')})"
    return None, f"ninguna ventana llega a {mudo} s ({len(vs)} ventanas con t_captured)"


def pos_fuente_de_enviado(casete_fuente: str, s_enviado: float) -> tuple[float | None, str]:
    """El transporte de casete mide `mudo=S` en audio ENVIADO acumulado (suma de `dur` de las ventanas del
    casete ORIGINAL, incluye los reenvíos de solape), no en posición de la fuente. Devuelve la posición de
    la fuente P en que el cliente original llevaba S enviados (interpolado dentro de la ventana)."""
    from qa.comun import leer_casete
    _, R = leer_casete(RAIZ / casete_fuente if not Path(casete_fuente).is_absolute() else casete_fuente)
    env = 0.0
    for r in R:
        if r.get("dir") == "client" and r.get("kind") == "ventana":
            p = r["payload"]
            env += float(p.get("dur") or 0.0)
            if env >= s_enviado:
                return round(p["audio_end"] - (env - s_enviado), 3), f"ventana {p['ventana']} del casete fuente (acumulado {env:.2f} s)"
    return None, f"el casete fuente no llega a {s_enviado} s enviados ({env:.1f})"


def analizar(bus_path: Path, casete: Path, mudo: float, tope_s: float, casete_fuente: str | None = None) -> dict:
    from contracts import errores
    # posición de la fuente en que arranca el mudo (escala del transporte: audio enviado); sin casete fuente, = mudo
    pos_mudo, pos_fuente_det = (pos_fuente_de_enviado(casete_fuente, mudo) if casete_fuente else (mudo, "sin casete fuente"))
    if pos_mudo is None:
        pos_mudo = mudo
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
    rots = [e for e in msgs if e["frame"].get("type") == "rotation"]
    wds = [e for e in msgs if e["frame"].get("type") == "watchdog"]
    otras = sorted({str(e["frame"].get("session_id")) for e in msgs if e["frame"].get("session_id") != sid})
    if otras:
        fallas.append(f"mensajes de OTRA sesión: {otras}")
    # seq continuo
    seqs = [e["frame"].get("seq") for e in msgs if e["frame"].get("seq") is not None]
    prev, huecos = init.get("last_seq"), []
    for s in seqs:
        if prev is not None and s != prev + 1:
            huecos.append(f"{prev}->{s}")
        prev = s
    if huecos:
        fallas.append(f"seq no continuo: {huecos[:10]}")
    if not any(e["frame"].get("type") == "session_end" for e in msgs):
        fallas.append("no llegó session_end")
    if meta.get("exit_productor") != 0:
        fallas.append(f"worker exit={meta.get('exit_productor')}")
    # T_mudo = reloj en que la corrida capturó la posición de la fuente pos_mudo (arranque real del mudo)
    t_mudo, fuente_t = t_mudo_desde_casete(casete, pos_mudo)
    t_fuente_mudo, _ = t_mudo_desde_casete(casete, mudo)  # comparación: "S s de la FUENTE" (más laxo)
    t0s = [e["frame"]["t_captured"] - e["frame"]["audio_end"] for e in textos
           if isinstance(e["frame"].get("t_captured"), (int, float)) and isinstance(e["frame"].get("audio_end"), (int, float))
           and e["frame"]["audio_end"] <= pos_mudo]
    t_mudo_textos = (min(t0s) + pos_mudo) if t0s else None
    if t_mudo is None and t_mudo_textos is not None:
        t_mudo, fuente_t = t_mudo_textos, f"textos previos (min t_captured - audio_end, n={len(t0s)})"
    if t_mudo is None:
        fallas.append("no se pudo fijar T_mudo (ni ventanas en el casete ni textos previos)")
    rel = lambda t: None if (t is None or t_mudo is None) else round(t - t_mudo, 3)
    detalle_rots = [{"seq": e["frame"].get("seq"), "reason": (e["frame"].get("meta") or {}).get("reason"),
                     "meta": e["frame"].get("meta"), "t_receive": e["t_receive"], "t_emit": e["frame"].get("t_emit"),
                     "t_receive_menos_T_mudo_s": rel(e["t_receive"]), "errores_contrato": errores(e["frame"])}
                    for e in rots]
    for d in detalle_rots:
        if d["errores_contrato"]:
            fallas.append(f"rotation seq {d['seq']} no valida contra el contrato: {d['errores_contrato'][:3]}")
    elegida = None
    if t_mudo is not None:
        cand = [d for d in detalle_rots if d["reason"] == "atasco" and d["t_receive"] >= t_mudo]
        elegida = cand[0] if cand else None
        if elegida is None:
            fallas.append(f"no llegó ningún rotation reason=atasco después de T_mudo (rotations: "
                          f"{[(d['seq'], d['reason'], d['t_receive_menos_T_mudo_s']) for d in detalle_rots]})")
        elif elegida["t_receive_menos_T_mudo_s"] > tope_s:
            fallas.append(f"rotation atasco llegó {elegida['t_receive_menos_T_mudo_s']} s después de T_mudo (> {tope_s} s)")
    textos_despues = []
    if elegida is not None:
        textos_despues = [e for e in textos if e["t_receive"] > elegida["t_receive"]
                          and (elegida["seq"] is None or e["frame"].get("seq", -1) > elegida["seq"])]
        if not textos_despues:
            fallas.append("después del rotation atasco no volvió a llegar ningún text")
    fmt_t = lambda e: {"seq": e["frame"].get("seq"), "audio": [e["frame"].get("audio_start"), e["frame"].get("audio_end")],
                       "t_receive_menos_T_mudo_s": rel(e["t_receive"]), "text": (e["frame"].get("text") or "")[:80]}
    return {"sid": sid, "bus": bus_path.relative_to(RAIZ).as_posix(), "casete_grabado": casete.relative_to(RAIZ).as_posix(),
            "exit_worker": meta.get("exit_productor"), "init_last_seq": init.get("last_seq"),
            "mensajes": len(msgs), "textos": len(textos), "seq": [seqs[0], seqs[-1]] if seqs else None,
            "huecos_seq": huecos, "mudo_s_enviado": mudo, "tope_s": tope_s,
            "pos_fuente_del_mudo_s": pos_mudo, "pos_fuente_del_mudo_detalle": pos_fuente_det,
            "T_mudo": t_mudo, "T_mudo_fuente": fuente_t, "T_mudo_segun_textos": t_mudo_textos,
            "T_fuente_S": t_fuente_mudo,
            "rotation_elegida_menos_T_fuente_S_s": (round(elegida["t_receive"] - t_fuente_mudo, 3)
                                                    if elegida is not None and t_fuente_mudo is not None else None),
            "rotations": detalle_rots, "watchdogs": [e["frame"] for e in wds],
            # rótulo: los textos salen de un casete (no es ASR real); el contrato pide replay=true en ese caso
            "replay_en_textos": {str(k): sum(1 for e in textos if e["frame"].get("replay") is k) for k in (True, False, None)},
            "rotation_elegida": elegida,
            "textos_antes_de_T_mudo": [fmt_t(e) for e in textos if t_mudo is not None and e["t_receive"] <= t_mudo],
            "textos_despues_de_la_rotation": [fmt_t(e) for e in textos_despues],
            "fallas": fallas, "ok": not fallas}


async def correr(sid, lang, cmd, port, log_prod, env, timeout_s, log) -> tuple[list, int | None]:
    reg: list = []
    parar = asyncio.Event()
    tarea = asyncio.create_task(escuchar(f"ws://localhost:{port}/ws/{sid}?lang={lang}", reg, parar, reconectar=False))
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10 and not any(e.get("ev") == "frame" for e in reg):
        await asyncio.sleep(0.05)
    with open(log_prod, "w", encoding="utf-8") as f:
        f.write("CMD: " + " ".join(cmd) + "\n")
        f.flush()
        pr = subprocess.Popen(cmd, cwd=str(RAIZ), stdout=f, stderr=subprocess.STDOUT, env=env)
        log(f"worker pid={pr.pid} {ahora_ar()}")
        t0 = time.monotonic()
        while pr.poll() is None and time.monotonic() - t0 < timeout_s:
            await asyncio.sleep(0.2)
        if pr.poll() is None:
            log(f"TIMEOUT {timeout_s} s: mato el worker pid={pr.pid}")
            pr.kill()
            pr.wait(10)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5 and not any(e.get("ev") == "frame" and e["frame"].get("type") == "session_end" for e in reg):
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    parar.set()
    await asyncio.gather(tarea, return_exceptions=True)
    return reg, pr.returncode


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=8100)
    ap.add_argument("--tag", default="b3")
    ap.add_argument("--sesion", default=None, help="default qa-wd-<tag>-<HHMMSS> (nuevo en cada corrida)")
    ap.add_argument("--casete-fuente", default=CASETE_DEF)
    ap.add_argument("--mudo", type=float, default=60.0)
    ap.add_argument("--archivo", default=AUDIO_DEF)
    ap.add_argument("--inicio", type=float, default=300.0)
    ap.add_argument("--duracion", type=float, default=120.0)
    ap.add_argument("--lang", default="es")
    ap.add_argument("--tope-s", type=float, default=15.0, help="máximo entre T_mudo y la llegada del rotation")
    ap.add_argument("--transporte", default=None, help="override del valor de --transporte (default casete:<casete>:mudo=<S>)")
    ap.add_argument("--extra", default="", help="flags extra para worker.run, separados por espacios")
    ap.add_argument("--recalcular", action="store_true")
    a = ap.parse_args(argv)
    base = OUT / f"watchdog-{a.tag}"
    suf = ".recalculo" if a.recalcular else ""
    log_path, json_path = Path(f"{base}{suf}.log"), Path(f"{base}{suf}.json")
    bus, casete, log_prod, cuota_log = Path(f"{base}.bus.jsonl"), Path(f"{base}.casete.jsonl"), Path(f"{base}.productor.log"), Path(f"{base}.cuota.log")
    OUT.mkdir(parents=True, exist_ok=True)
    lineas: list[str] = []

    def log(s):
        print(s, flush=True)
        lineas.append(s)

    def cerrar(rc):
        log_path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return rc

    log(f"e2e_watchdog {ahora_ar()} argv={' '.join(sys.argv[1:] if argv is None else argv)}")
    if not a.recalcular:
        ok, ayuda = tiene_transporte()
        if not ok:
            log("worker.run NO tiene --transporte (todavía). No se corre nada. exit 2")
            log("worker.run --help:\n" + ayuda)
            return cerrar(2)
        if not health(a.puerto):
            log(f"el hub no contesta /health en {a.puerto}. exit 2")
            return cerrar(2)
        sid = a.sesion or f"qa-wd-{a.tag}-{time.strftime('%H%M%S')}"
        transporte = a.transporte or f"casete:{a.casete_fuente}:mudo={a.mudo:g}"
        cmd = [PY, "-m", "worker.run", "--transporte", transporte, "--archivo", a.archivo,
               "--inicio", f"{a.inicio:g}", "--duracion", f"{a.duracion:g}", "--sesion", sid, "--lang", a.lang,
               "--hub", f"ws://localhost:{a.puerto}/ingest", "--casete", str(casete.relative_to(RAIZ)),
               "--traducir-a", "none"] + [x for x in a.extra.split() if x]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "GEMINI_API_KEY": "", "GEMINI_API_KEY_RESERVA": "",
               "CUOTA_LOG": str(cuota_log)}
        for p in (bus, casete, cuota_log):
            if p.exists():
                p.unlink()
        log(f"sesion={sid} hub={a.puerto} transporte={transporte}")
        log("CMD: " + " ".join(cmd[1:]))
        log("entorno del worker: GEMINI_API_KEY='' GEMINI_API_KEY_RESERVA='' CUOTA_LOG=" + str(cuota_log.relative_to(RAIZ)))
        reg, rc_w = asyncio.run(correr(sid, a.lang, cmd, a.puerto, log_prod, env, a.duracion + 120, log))
        guardar_jsonl(bus, [{"ev": "meta", "sid": sid, "lang": a.lang, "exit_productor": rc_w, "cmd": cmd,
                             "mudo_s": a.mudo}] + reg)
        log(f"worker exit={rc_w} {ahora_ar()}; cliente: {sum(1 for e in reg if e.get('ev') == 'frame')} frames -> {bus.relative_to(RAIZ)}")
    if not bus.is_file():
        log(f"no existe {bus}. exit 1")
        return cerrar(1)
    r = analizar(bus, casete, a.mudo, a.tope_s, a.casete_fuente)
    r["cuota_log_qa"] = cuota_log.read_text(encoding="utf-8").strip() if cuota_log.is_file() else None
    log(f"mudo={a.mudo:g} s de audio ENVIADO (escala del transporte) = posición de la fuente {r['pos_fuente_del_mudo_s']} s "
        f"[{r['pos_fuente_del_mudo_detalle']}]")
    log(f"T_mudo = {r['T_mudo']} [{r['T_mudo_fuente']}]; según textos previos = {r['T_mudo_segun_textos']}; "
        f"T de {a.mudo:g} s de la FUENTE = {r['T_fuente_S']}")
    if r["rotation_elegida"]:
        log(f"rotation elegida: seq={r['rotation_elegida']['seq']} llegó {r['rotation_elegida']['t_receive_menos_T_mudo_s']} s "
            f"después de T_mudo (tope {a.tope_s:g} s); {r['rotation_elegida_menos_T_fuente_S_s']} s después de los {a.mudo:g} s de la fuente")
    log(f"textos={r['textos']} mensajes={r['mensajes']} seq={r['seq']} init.last_seq={r['init_last_seq']} exit_worker={r['exit_worker']}")
    for d in r["rotations"]:
        log(f"rotation seq={d['seq']} reason={d['reason']} meta={json.dumps(d['meta'], ensure_ascii=False)} "
            f"t_receive-T_mudo={d['t_receive_menos_T_mudo_s']} s contrato={'OK' if not d['errores_contrato'] else d['errores_contrato']}")
    for w in r["watchdogs"]:
        log(f"watchdog seq={w.get('seq')} meta={json.dumps(w.get('meta'), ensure_ascii=False)}")
    for t in r["textos_despues_de_la_rotation"][:5]:
        log(f"text después: seq={t['seq']} audio={t['audio']} t-T_mudo={t['t_receive_menos_T_mudo_s']} s «{t['text']}»")
    log(f"textos después de la rotation elegida: {len(r['textos_despues_de_la_rotation'])}")
    log(f"rótulo replay en los text recibidos (informativo, no cambia el exit): {r['replay_en_textos']}")
    log(f"cuota registrada por el worker en {cuota_log.name}: {r['cuota_log_qa']!r}")
    for f in r["fallas"]:
        log(f"  - FALLA: {f}")
    rc = 0 if r["ok"] else 1
    log(f"RESULTADO: {'OK' if rc == 0 else 'FALLA'} exit={rc}")
    json_path.write_text(json.dumps({"generado": ahora_ar(), "exit": rc, **r}, ensure_ascii=False, indent=1), encoding="utf-8")
    return cerrar(rc)


if __name__ == "__main__":
    sys.exit(main())
