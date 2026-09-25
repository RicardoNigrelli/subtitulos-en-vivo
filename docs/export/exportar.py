"""Exporta el historial canonico de una sesion a SRT, VTT o TXT (R8d).

Dos fuentes posibles:

    --casete <archivo.jsonl> [--lang es]
        Lee un casete de tres capas (o un .jsonl plano del contrato) con
        `contracts.leer_archivo`: el mismo lector que usa `contracts.validate` (congelado por
        backend). De un casete sólo toma las lineas `"dir": "emit"`; de un .jsonl plano toma los
        mensajes tal cual.

    --hub http://host:puerto --sesion <id> [--desde 0] [--lang es]
        Pide `GET /api/sesiones/<id>/historial?desde=<N>` (default 0) al hub en vivo. Esa ruta ya
        devuelve sólo mensajes `type=text`, ordenados por `seq`, con las traducciones YA MERGEADAS
        (contracts/README.md, hub/app.py). No hace falta re-mergear nada.

En ambos casos sólo se usan mensajes `type=text`: son los únicos con `audio_start`/`audio_end`
(contracts/README.md: "segundos desde el inicio del audio de la sesion (SRT/VTT, R8d)"). Esos dos
campos del contrato se usan TAL CUAL para los timestamps: este script no inventa duración ni corrige
huecos.

--lang elige el idioma de salida:
    - sin --lang, o --lang == idioma original de la sesion -> se usa `text` (original).
    - --lang distinto -> se usa `translations[lang].text` si `ok:true`.
    - si no hay traduccion ok para esa linea (no llegó o `ok:false`, contracts/README.md) -> se
      usa el texto ORIGINAL con el prefijo "[sin traducir]", el mismo criterio de `web/app.js`
      (ver reportes/frontend-b2.md, funcion resolverPendiente: nunca se inventa texto, se declara
      la ausencia).

Uso:
    .venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/x.jsonl --formato srt
    .venv/Scripts/python docs/export/exportar.py --casete fixtures/casetes/x.jsonl --formato vtt --lang es
    .venv/Scripts/python docs/export/exportar.py --hub http://localhost:8100 --sesion qa-b3-1 --formato srt

Sin --salida, escribe en docs/export/ejemplos/<base-del-casete-o-sesion>[-<lang>].<formato>.

Validacion (no la hace este script, la pide el bloque B4):
    ffmpeg -i salida.srt -f null -   # exit 0
    ffmpeg -i salida.vtt -f null -   # exit 0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))  # para "import contracts" sin depender del cwd desde el que se llame

from contracts import leer_archivo  # noqa: E402


def _mergear_traducciones(emitidos: list[dict]) -> list[dict]:
    """Aplica sobre los `type=text` los items de los `type=translation` que aparezcan despues en
    el archivo, con la MISMA regla de merge que hace el hub (contracts/README.md, "Traduccion
    diferida... y merge en el hub"): un item `ok:true` siempre reemplaza lo que hubiera; un item
    `ok:false` NO pisa un `ok:true` ya guardado. Hace falta porque un casete guarda los mensajes
    CRUDOS tal como los emitio el worker, ANTES del merge (a diferencia de `GET .../historial` del
    hub, que ya devuelve todo mergeado). Devuelve sólo los mensajes `type=text`, con sus
    `translations` ya completas."""
    textos: dict[int, dict] = {}          # seq -> mensaje type=text (copia, se muta in place)
    pendientes: dict[int, dict] = {}      # seq -> {lang_to: {text, ok}} de items sin su text todavia
    orden: list[int] = []

    def _set(m: dict, lang_to: str, item: dict) -> None:
        actual = m["translations"].get(lang_to)
        nuevo_ok = bool(item.get("ok"))
        if actual is not None and actual.get("ok") and not nuevo_ok:
            return  # un ok:false no pisa un ok:true ya guardado
        m["translations"][lang_to] = {"text": item.get("text"), "ok": nuevo_ok}

    for m in emitidos:
        if m.get("type") == "text":
            seq = m["seq"]
            copia = dict(m)
            copia["translations"] = dict(copia.get("translations") or {})
            textos[seq] = copia
            orden.append(seq)
            for lang_to, item in pendientes.pop(seq, {}).items():
                _set(copia, lang_to, item)
        elif m.get("type") == "translation":
            lang_to = (m.get("meta") or {}).get("lang_to")
            if not lang_to:
                continue
            for item in m.get("items") or []:
                seq = item.get("seq")
                if seq is None:
                    continue
                if seq in textos:
                    _set(textos[seq], lang_to, item)
                else:
                    pendientes.setdefault(seq, {})[lang_to] = item

    return [textos[s] for s in orden]


def _leer_casete(path: Path) -> list[dict]:
    """Mensajes type=text de un casete (con sus traducciones mergeadas) o de un .jsonl plano del
    contrato, ordenados por seq."""
    emitidos = []
    for linea, m in leer_archivo(path):
        if isinstance(m, Exception):
            print(f"AVISO {path}:{linea}: JSON invalido, se saltea: {m}", file=sys.stderr)
            continue
        if isinstance(m, dict):
            emitidos.append(m)
    msgs = _mergear_traducciones(emitidos)
    msgs.sort(key=lambda m: m["seq"])
    return msgs


SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LIMITE_HISTORIAL = 1000   # tope del hub por pedido (hub/app.py api_historial, `limit`); se pagina con `desde`


def _leer_hub(hub: str, sesion: str, desde: int) -> list[dict]:
    """GET /api/sesiones/<sesion>/historial?desde=<desde>&limit=1000, paginado con `desde` hasta que
    el hub devuelva menos que el limite: ya son solo type=text, ya mergeadas las traducciones, ya
    ordenadas por seq (contracts/README.md; hub/app.py api_historial)."""
    if not SLUG.match(sesion):
        raise SystemExit(f"ERROR --sesion invalido: {sesion!r} (esperado ^[a-z0-9][a-z0-9_-]{{0,63}}$)")
    textos: list[dict] = []
    while True:
        url = f"{hub.rstrip('/')}/api/sesiones/{sesion}/historial?desde={desde}&limit={LIMITE_HISTORIAL}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                cuerpo = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", errors="replace")
            raise SystemExit(f"ERROR {url} -> HTTP {e.code}: {detalle}")
        except urllib.error.URLError as e:
            raise SystemExit(f"ERROR no se pudo conectar a {url}: {e}")
        datos = json.loads(cuerpo)
        textos.extend(m for m in datos if m.get("type") == "text")
        seqs = [m.get("seq") for m in datos if isinstance(m.get("seq"), int)]
        if len(datos) < LIMITE_HISTORIAL or not seqs or max(seqs) + 1 <= desde:
            break
        desde = max(seqs) + 1
    return textos


def _texto_de(m: dict, lang: str | None) -> str:
    """Texto a mostrar para el idioma pedido (ver docstring del modulo)."""
    original = m.get("text") or ""
    if not lang or lang == m.get("lang"):
        return original
    trad = (m.get("translations") or {}).get(lang)
    if isinstance(trad, dict) and trad.get("ok") and trad.get("text"):
        return trad["text"]
    return f"[sin traducir] {original}"


def _ts_srt(s: float) -> str:
    total_ms = max(0, round(s * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    seg = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{seg:02d},{ms:03d}"


def _ts_vtt(s: float) -> str:
    return _ts_srt(s).replace(",", ".")


def _cargar_offset_map(path: Path) -> list[dict]:
    """[{"desde":.., "hasta":.., "offset":..}, ...] en la linea de tiempo del audio FUENTE
    (`audio_start`/`audio_end` del casete/hub tal cual). Pensado para alinear un SRT generado a
    partir de un audio "de trabajo" (p.ej. narracion concatenada) con la posicion real de cada
    tramo en un video final editado (docs/video/componer.py escribe este mapa a partir de su
    propio timeline: ver `corte-v2-timeline.json` -> `narracion_offsets`)."""
    datos = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted(datos, key=lambda r: r["desde"])


def _offset_para(t: float, mapa: list[dict]) -> float:
    for r in mapa:
        if r["desde"] <= t < r["hasta"]:
            return r["offset"]
    if not mapa:
        return 0.0
    return mapa[0]["offset"] if t < mapa[0]["desde"] else mapa[-1]["offset"]


def _aplicar_offset_map(msgs: list[dict], mapa: list[dict]) -> list[dict]:
    salida = []
    for m in msgs:
        ini, fin = _rango(m)
        centro = (ini + fin) / 2
        off = _offset_para(centro, mapa)
        m2 = dict(m)
        m2["audio_start"] = ini + off
        m2["audio_end"] = fin + off
        salida.append(m2)
    return salida


def _rango(m: dict) -> tuple[float, float]:
    ini, fin = float(m["audio_start"]), float(m["audio_end"])
    if fin <= ini:
        fin = ini + 0.001  # el contrato exige audio_end >= audio_start; evita un cue de largo 0
    return ini, fin


def generar_srt(msgs: list[dict], lang: str | None) -> str:
    bloques = []
    for i, m in enumerate(msgs, 1):
        ini, fin = _rango(m)
        bloques.append(f"{i}\n{_ts_srt(ini)} --> {_ts_srt(fin)}\n{_texto_de(m, lang)}\n")
    return "\n".join(bloques) + ("\n" if bloques else "")


def generar_vtt(msgs: list[dict], lang: str | None) -> str:
    bloques = ["WEBVTT\n"]
    for i, m in enumerate(msgs, 1):
        ini, fin = _rango(m)
        bloques.append(f"{i}\n{_ts_vtt(ini)} --> {_ts_vtt(fin)}\n{_texto_de(m, lang)}\n")
    return "\n".join(bloques) + ("\n" if len(bloques) > 1 else "")


def generar_txt(msgs: list[dict], lang: str | None) -> str:
    lineas = [f"[{_ts_srt(float(m['audio_start']))}] {_texto_de(m, lang)}" for m in msgs]
    return "\n".join(lineas) + ("\n" if lineas else "")


GENERADORES = {"srt": generar_srt, "vtt": generar_vtt, "txt": generar_txt}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python docs/export/exportar.py",
                                  description=__doc__.splitlines()[0])
    fuente = ap.add_mutually_exclusive_group(required=True)
    fuente.add_argument("--casete", help="archivo .jsonl (casete de tres capas o .jsonl plano del contrato)")
    fuente.add_argument("--hub", help="base URL del hub, ej. http://localhost:8100")
    ap.add_argument("--sesion", help="session_id (obligatorio con --hub)")
    ap.add_argument("--desde", type=int, default=0, help="historial?desde=N (default 0; solo con --hub)")
    ap.add_argument("--formato", required=True, choices=sorted(GENERADORES))
    ap.add_argument("--lang", default=None,
                     help="es|en: idioma de salida pedido. Default: idioma original de la sesion")
    ap.add_argument("--salida", default=None,
                     help="ruta de salida. Default: docs/export/ejemplos/<base>[-<lang>].<formato>")
    ap.add_argument("--offset-map", default=None,
                     help="JSON [{desde,hasta,offset}] para correr audio_start/audio_end "
                          "(alinear un audio de trabajo con su posicion real en un video editado)")
    args = ap.parse_args(argv)

    if args.hub and not args.sesion:
        ap.error("--hub requiere --sesion")
    if args.casete and args.sesion:
        ap.error("--sesion es solo para --hub (con --casete la sesion sale del propio archivo)")

    if args.casete:
        msgs = _leer_casete(Path(args.casete))
        base = Path(args.casete).stem
    else:
        msgs = _leer_hub(args.hub, args.sesion, args.desde)
        base = args.sesion

    if not msgs:
        print("ERROR: no hay mensajes type=text en la fuente indicada", file=sys.stderr)
        return 1

    if args.offset_map:
        msgs = _aplicar_offset_map(msgs, _cargar_offset_map(Path(args.offset_map)))

    salida_texto = GENERADORES[args.formato](msgs, args.lang)

    if args.salida:
        destino = Path(args.salida)
    else:
        sufijo = f"-{args.lang}" if args.lang else ""
        destino = RAIZ / "docs" / "export" / "ejemplos" / f"{base}{sufijo}.{args.formato}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(salida_texto, encoding="utf-8")
    print(f"OK: {len(msgs)} lineas type=text -> {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
