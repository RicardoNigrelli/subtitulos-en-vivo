"""RECONEXIÓN e2e: se mata el hub a mitad de un replay y se lo vuelve a levantar.

    .venv/Scripts/python qa/reconexion.py [--casete fixtures/casetes/b1-nerdearla-es-intento2-cancelled.jsonl]
                                          [--puerto 8194] [--velocidad 10] [--matar-tras 25] [--caido-s 3]

Hub PROPIO en --puerto (se verifica libre con netstat antes). Productor: `worker.replay` del casete. Cliente
/ws con reconexión (0,3 s). Cuando el cliente recibió --matar-tras textos: kill DURO del hub (TerminateProcess),
--caido-s segundos abajo, se relevanta en el mismo puerto (memoria vacía: auth_ok.last_seq = {}).
Verifica (exit 0 sólo si TODO):
  1. el replay reconecta (>= 2 '[emisor] conectado' en su log) y termina con exit 0 (backlog vaciado)
  2. el hub nuevo tiene TODOS los `text` del casete (historial?desde=0 == seqs esperados): backlog reenviado
  3. el cliente recibe `init` en cada conexión y no pierde ningún `text`: lo visto antes de la caída
     ∪ historial?desde=<último visto> pedido al reconectar ∪ lo visto después == todos los esperados
  4. nada de otra sesión y llega session_end
Informa (no falla): hueco visto por el cliente, init.last_seq tras reconectar, seq repetidos recibidos en vivo.
Salida: qa/out/reconexion-b2.log, .json y .bus.jsonl (todos los frames con t_receive).
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
from qa.bus import cmd_replay  # noqa: E402
from qa.comun import (OUT, RAIZ, ahora_ar, escuchar, get_json, guardar_jsonl, leer_casete,  # noqa: E402
                      levantar_hub, matar, puerto_ocupado)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--casete", default="fixtures/casetes/b1-nerdearla-es-intento2-cancelled.jsonl")
    ap.add_argument("--puerto", type=int, default=8194)
    ap.add_argument("--velocidad", type=float, default=10.0)
    ap.add_argument("--matar-tras", type=int, default=25, help="textos recibidos por el cliente antes del kill")
    ap.add_argument("--caido-s", type=float, default=3.0)
    ap.add_argument("--sesion", default="qa-reconexion-b2")
    ap.add_argument("--tag", default="reconexion-b2")
    a = ap.parse_args(argv)
    lineas: list[str] = []

    def log(s):
        s = f"{time.strftime('%H:%M:%S')} {s}"
        print(s, flush=True)
        lineas.append(s)

    log_path, json_path, bus_path = OUT / f"{a.tag}.log", OUT / f"{a.tag}.json", OUT / f"{a.tag}.bus.jsonl"
    hub_log = OUT / f"{a.tag}.hub.log"
    if hub_log.exists():
        hub_log.unlink()
    _, R = leer_casete(a.casete)
    esperados = sorted(r["payload"]["seq"] for r in R if r.get("dir") == "emit" and r.get("kind") == "text")
    log(f"reconexion {ahora_ar()} casete={a.casete} textos esperados={len(esperados)} "
        f"seq {esperados[0]}..{esperados[-1]} velocidad x{a.velocidad} puerto={a.puerto}")
    oc = puerto_ocupado(a.puerto)
    if oc:
        log(f"puerto {a.puerto} OCUPADO ({oc}). exit 2")
        Path(log_path).write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return 2
    ev: dict = {"eventos": []}
    reg: list = []

    async def correr() -> dict:
        hub = levantar_hub(a.puerto, hub_log)
        log(f"hub A pid={hub.pid} arriba en {a.puerto}")
        parar = asyncio.Event()
        cli = asyncio.create_task(escuchar(f"ws://localhost:{a.puerto}/ws/{a.sesion}", reg, parar, reconectar=True))
        while not any(e.get("ev") == "frame" for e in reg):
            await asyncio.sleep(0.05)
        plog = OUT / f"{a.tag}.productor.log"
        fp = open(plog, "w", encoding="utf-8")
        cmd = cmd_replay(a.casete, a.sesion, a.puerto, a.velocidad)
        fp.write("CMD: " + " ".join(cmd) + "\n")
        fp.flush()
        rep = subprocess.Popen(cmd, cwd=str(RAIZ), stdout=fp, stderr=subprocess.STDOUT,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        log(f"replay pid={rep.pid}")

        def textos_vistos():
            return [e for e in reg if e.get("ev") == "frame" and e["frame"].get("type") == "text"]

        t0 = time.monotonic()
        while len(textos_vistos()) < a.matar_tras and rep.poll() is None and time.monotonic() - t0 < 300:
            await asyncio.sleep(0.05)
        antes = [e["frame"]["seq"] for e in textos_vistos()]
        ultimo_visto = max(antes) if antes else 0
        n_reg_kill = len(reg)
        rc_kill = matar(hub)
        t_kill = time.time()
        log(f"KILL duro del hub A (exit {rc_kill}); el cliente había visto {len(antes)} textos, último seq={ultimo_visto}")
        ev["eventos"].append({"kill": t_kill, "ultimo_visto": ultimo_visto, "textos_antes": len(antes)})
        await asyncio.sleep(a.caido_s)
        libre = puerto_ocupado(a.puerto)
        log(f"tras {a.caido_s} s: puerto {a.puerto} {'OCUPADO ' + libre if libre else 'libre'}; replay vivo={rep.poll() is None}")
        hub2 = levantar_hub(a.puerto, hub_log)
        t_up = time.time()
        log(f"hub B pid={hub2.pid} arriba en {a.puerto} ({t_up - t_kill:.1f} s después del kill)")
        ev["eventos"].append({"up": t_up})
        # esperar el init de la reconexión del cliente y pedir el historial desde el último visto
        hist_reconexion = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            nuevos = [e for e in reg[n_reg_kill:] if e.get("ev") == "frame" and e["frame"].get("type") == "init"]
            if nuevos:
                init2 = nuevos[0]
                try:
                    hist_reconexion = get_json(f"http://127.0.0.1:{a.puerto}/api/sesiones/{a.sesion}/historial?desde={ultimo_visto}")
                except Exception as e:  # 404 si la sesión todavía no existe en el hub nuevo
                    hist_reconexion = {"error": f"{type(e).__name__}: {e}"}
                log(f"cliente reconectado: init.last_seq={init2['frame'].get('last_seq')} state={init2['frame'].get('state')} "
                    f"lines={len(init2['frame'].get('lines') or [])} ({init2['t_receive'] - t_up:+.2f} s vs hub B arriba); "
                    f"historial?desde={ultimo_visto} -> "
                    f"{('%d textos' % len(hist_reconexion)) if isinstance(hist_reconexion, list) else hist_reconexion}")
                ev["init2"] = {"t_receive": init2["t_receive"], "last_seq": init2["frame"].get("last_seq"),
                               "state": init2["frame"].get("state")}
                break
            await asyncio.sleep(0.05)
        t0 = time.monotonic()
        while rep.poll() is None and time.monotonic() - t0 < 600:
            await asyncio.sleep(0.2)
        rc_rep = rep.poll()
        fp.close()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10 and not any(e.get("ev") == "frame" and e["frame"].get("type") == "session_end" for e in reg):
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        # si el historial al reconectar dio 404 (sesión aún no re-aprendida), se repite ahora
        hist_final = get_json(f"http://127.0.0.1:{a.puerto}/api/sesiones/{a.sesion}/historial?desde=0")
        if not isinstance(hist_reconexion, list):
            hist_reconexion_tarde = get_json(f"http://127.0.0.1:{a.puerto}/api/sesiones/{a.sesion}/historial?desde={ultimo_visto}")
        else:
            hist_reconexion_tarde = None
        parar.set()
        await asyncio.gather(cli, return_exceptions=True)
        matar(hub2)
        log(f"hub B detenido; replay exit={rc_rep}")
        return {"rc_rep": rc_rep, "ultimo_visto": ultimo_visto, "antes": antes, "n_reg_kill": n_reg_kill,
                "hist_reconexion": hist_reconexion, "hist_reconexion_tarde": hist_reconexion_tarde,
                "hist_final": hist_final, "plog": plog}

    r = asyncio.run(correr())
    guardar_jsonl(bus_path, reg)
    plog_txt = Path(r["plog"]).read_text(encoding="utf-8", errors="replace")
    n_conect = plog_txt.count("[emisor] conectado")
    frames = [e for e in reg if e.get("ev") == "frame"]
    inits = [e for e in frames if e["frame"].get("type") == "init"]
    conexiones = [e for e in reg if e.get("ev") == "conectado"]
    caidas = [e for e in reg if e.get("ev") == "desconectado"]
    despues = [e["frame"]["seq"] for e in reg[r["n_reg_kill"]:] if e.get("ev") == "frame" and e["frame"].get("type") == "text"]
    hr = r["hist_reconexion"] if isinstance(r["hist_reconexion"], list) else (r["hist_reconexion_tarde"] or [])
    recuperados = [m["seq"] for m in hr]
    union = sorted(set(r["antes"]) | set(recuperados) | set(despues))
    hist_final = sorted(m["seq"] for m in r["hist_final"])
    otras = [e["frame"].get("session_id") for e in frames if e["frame"].get("type") not in ("init", "heartbeat")
             and e["frame"].get("session_id") != a.sesion]
    repetidos = [s for s in despues if s <= r["ultimo_visto"]]
    primer_despues = min((s for s in despues if s > r["ultimo_visto"]), default=None)
    checks = {
        "1_replay_reconecta_y_exit0": {"ok": n_conect >= 2 and r["rc_rep"] == 0,
                                       "conexiones_del_emisor": n_conect, "exit_replay": r["rc_rep"]},
        "2_backlog_completo_en_hub_nuevo": {"ok": hist_final == esperados, "hub_nuevo_textos": len(hist_final),
                                            "esperados": len(esperados),
                                            "faltan": sorted(set(esperados) - set(hist_final))[:20]},
        "3_cliente_sin_perder_text": {"ok": len(inits) >= 2 and union == esperados, "inits": len(inits),
                                      "conexiones_cliente": len(conexiones), "cierres_vistos": len(caidas),
                                      "textos_antes": len(r["antes"]), "ultimo_visto": r["ultimo_visto"],
                                      "recuperados_por_historial": len(recuperados), "textos_despues_en_vivo": len(despues),
                                      "faltan": sorted(set(esperados) - set(union))[:20]},
        "4_sin_mezcla_y_session_end": {"ok": not otras and any(e["frame"].get("type") == "session_end" for e in frames),
                                       "otras_sesiones": len(otras)},
    }
    info = {"hueco_visto_en_vivo": None if primer_despues is None else f"{r['ultimo_visto']} -> {primer_despues}",
            "init_tras_reconectar": ev.get("init2"), "seq_repetidos_recibidos_en_vivo_tras_reconectar": len(repetidos),
            "historial_al_reconectar": ("%d textos" % len(r["hist_reconexion"])) if isinstance(r["hist_reconexion"], list) else r["hist_reconexion"]}
    for k, v in checks.items():
        log(f"{'OK   ' if v['ok'] else 'FALLA'} {k}: {json.dumps({x: y for x, y in v.items() if x != 'ok'}, ensure_ascii=False)}")
    log(f"INFO {json.dumps(info, ensure_ascii=False)}")
    rc = 0 if all(v["ok"] for v in checks.values()) else 1
    log(f"RESULTADO: {'OK' if rc == 0 else 'FALLA'} exit={rc}")
    json_path.write_text(json.dumps({"generado": ahora_ar(), "args": vars(a), "checks": checks, "info": info,
                                     "eventos": ev, "exit": rc}, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(log_path).write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
