"""Cliente de audiencia de prueba: se conecta a /ws/<sesion>?lang=xx e imprime init + mensajes.

    python -m hub.cliente_ws ejemplo-en --lang es
    python -m hub.cliente_ws sala-1 --lang es --hub ws://localhost:8100 --mensajes 20 --inactividad 15

Chequea lo que recibe: cada mensaje contra el contrato, init contra $defs/frame_init, que todo sea
de la sesion pedida, y `seq` estrictamente creciente (mayor que init.last_seq). Termina al llegar a --mensajes, con session_end,
o tras --inactividad segundos sin mensajes del contrato (los heartbeats no cuentan).
Con --solo-init: recibe el init (estado inicial al conectar), lo valida y sale.
B2: imprime `translation` (cada item: seq, ok, texto; lo aplica a las lineas que ya tiene) y
`partial` (texto provisorio); ninguno lleva seq, asi que no entran en el chequeo de seq creciente.
Tras `session_end` sigue escuchando --tras-fin segundos (default 5): las traducciones por lotes
pueden llegar DESPUES del session_end (medido en b1-es-60s-trad.jsonl: 3 de 6). 0 = cortar ahi.
Exit 0: recibio init + >=1 mensaje (o solo init con --solo-init), todo valido y seq creciente.
Exit 1: algo de eso fallo.
Exit 2: no pudo conectar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import aiohttp

from contracts import errores, errores_frame

from .config import cargar_config


def _texto_en(m: dict, lang: str | None) -> str:
    if not lang or m.get("lang") == lang:
        return m.get("text", "")
    tr = (m.get("translations") or {}).get(lang)
    if tr is None:
        return f"[sin traduccion a {lang}] {m.get('text', '')}"
    if not tr.get("ok"):
        return f"[traduccion fallida] {m.get('text', '')}"
    return tr["text"]


async def escuchar(url: str, lang: str | None, max_msgs: int | None, inactividad: float,
                   out=print, solo_init: bool = False, tras_fin: float = 5.0) -> dict:
    res = {"init": None, "mensajes": [], "latidos": 0, "problemas": [], "traducciones": 0,
           "parciales": 0, "items_aplicados": 0}
    lineas: dict[int, dict] = {}  # seq -> text recibido (para aplicar los items de translation)
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, heartbeat=20.0) as ws:
            ultimo_util = time.monotonic()
            ultimo_seq = None
            espera = inactividad
            while True:
                restante = espera - (time.monotonic() - ultimo_util)
                if restante <= 0:
                    out(f"FIN: {espera:.0f} s sin mensajes del contrato")
                    break
                try:
                    r = await ws.receive(timeout=restante)
                except asyncio.TimeoutError:
                    continue
                if r.type != aiohttp.WSMsgType.TEXT:
                    out(f"FIN: el hub cerro la conexion (tipo={r.type.name}, codigo={ws.close_code})")
                    break
                f = json.loads(r.data)
                if res["init"] is None:
                    if f.get("type") != "init":
                        res["problemas"].append(f"el primer frame no es init: {f.get('type')}")
                    res["init"] = f
                    for e in errores_frame(f, "init"):
                        res["problemas"].append(f"init: {e}")
                    ultimo_seq = f.get("last_seq")
                    out(f"INIT session_id={f.get('session_id')} lang={f.get('lang')} "
                        f"session_lang={f.get('session_lang')} last_seq={f.get('last_seq')} "
                        f"state={f.get('state')} lines={len(f.get('lines', []))}")
                    for ln in f.get("lines", []):
                        if isinstance(ln.get("seq"), int):
                            lineas[ln["seq"]] = ln
                        out(f"  init seq={ln['seq']:>4} | {_texto_en(ln, lang)}")
                        for e in errores(ln):
                            res["problemas"].append(f"init seq={ln.get('seq')}: {e}")
                    if solo_init:
                        out("FIN: --solo-init")
                        break
                    continue
                if f.get("type") == "heartbeat":
                    res["latidos"] += 1
                    continue
                ultimo_util = time.monotonic()
                errs = errores(f)
                for e in errs:
                    res["problemas"].append(f"seq={f.get('seq')}: {e}")
                if f.get("session_id") != res["init"].get("session_id"):
                    res["problemas"].append(f"mensaje de OTRA sesion: {f.get('session_id')} seq={f.get('seq')}")
                seq = f.get("seq")
                if isinstance(seq, int) and ultimo_seq is not None and seq <= ultimo_seq:
                    res["problemas"].append(f"seq no creciente: {seq} despues de {ultimo_seq}")
                if isinstance(seq, int):
                    ultimo_seq = seq
                res["mensajes"].append(f)
                demora = ""
                if isinstance(f.get("t_hub"), (int, float)) and isinstance(f.get("t_emit"), (int, float)):
                    demora = f" t_hub-t_emit={f['t_hub'] - f['t_emit']:.1f}s"
                rotulo = " REPLAY" if f.get("replay") else ""
                if f.get("type") == "text":
                    lineas[seq] = f
                    out(f"seq={seq:>4} text{rotulo}{demora} | {_texto_en(f, lang)}")
                elif f.get("type") == "translation":
                    res["traducciones"] += 1
                    lang_to = (f.get("meta") or {}).get("lang_to")
                    out(f"     translation{rotulo}{demora} lang_to={lang_to} items={len(f.get('items') or [])} "
                        f"model={(f.get('meta') or {}).get('model')}")
                    for it in f.get("items") or []:
                        ln = lineas.get(it.get("seq"))
                        estado = "aplicado" if ln is not None else "sin linea (llega mergeado con el text)"
                        if ln is not None:
                            ln.setdefault("translations", {})[lang_to] = {"text": it.get("text"), "ok": it.get("ok")}
                            res["items_aplicados"] += 1
                        out(f"       item seq={it.get('seq')} ok={it.get('ok')} [{estado}] | {it.get('text')}")
                elif f.get("type") == "partial":
                    res["parciales"] += 1
                    out(f"     partial{rotulo}{demora} | {f.get('text')}")
                else:
                    out(f"seq={seq:>4} {f.get('type')}{rotulo}{demora} | meta={json.dumps(f.get('meta'), ensure_ascii=False)}")
                if f.get("type") == "session_end":
                    if tras_fin <= 0:
                        out("FIN: session_end")
                        break
                    out(f"session_end: sigo {tras_fin:.0f} s por traducciones rezagadas")
                    espera = tras_fin
                if max_msgs is not None and len(res["mensajes"]) >= max_msgs:
                    out(f"FIN: {max_msgs} mensajes recibidos")
                    break
    return res


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m hub.cliente_ws", description=__doc__.splitlines()[0])
    ap.add_argument("sesion")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--hub", default=None, help="base ws:// (default ws://localhost:<HUB_PORT>)")
    ap.add_argument("--mensajes", type=int, default=None, help="terminar al recibir N mensajes")
    ap.add_argument("--inactividad", type=float, default=15.0, help="terminar tras S segundos sin mensajes")
    ap.add_argument("--solo-init", action="store_true", help="recibir solo el init (estado inicial) y salir")
    ap.add_argument("--tras-fin", type=float, default=5.0,
                    help="segundos que sigue escuchando tras session_end (traducciones rezagadas); 0 = cortar")
    args = ap.parse_args(argv)
    base = args.hub or f"ws://localhost:{cargar_config().port}"
    url = f"{base.rstrip('/')}/ws/{args.sesion}" + (f"?lang={args.lang}" if args.lang else "")
    print(f"cliente_ws: conectando a {url}")
    try:
        res = asyncio.run(escuchar(url, args.lang, args.mensajes, args.inactividad, solo_init=args.solo_init,
                                   tras_fin=args.tras_fin))
    except (aiohttp.ClientError, OSError) as e:
        print(f"ERROR no se pudo conectar: {e}")
        return 2
    seqs = [m["seq"] for m in res["mensajes"] if isinstance(m.get("seq"), int)]
    print(f"RESUMEN: init={'si' if res['init'] else 'no'} mensajes={len(res['mensajes'])} "
          f"seq={seqs[0] if seqs else '-'}..{seqs[-1] if seqs else '-'} latidos={res['latidos']} "
          f"traducciones={res['traducciones']} (items aplicados={res['items_aplicados']}) "
          f"parciales={res['parciales']} problemas={len(res['problemas'])}")
    for p in res["problemas"]:
        print(f"PROBLEMA {p}")
    ok = res["init"] is not None and (res["mensajes"] or args.solo_init) and not res["problemas"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
