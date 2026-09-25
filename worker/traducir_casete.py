"""Traduce OFFLINE los `text` de un casete con el traductor REAL y escribe `<nombre>-trad.jsonl`.

    python -m worker.traducir_casete fixtures/casetes/b1-en-60s-rederivado.jsonl --a es
    python -m worker.traducir_casete fixtures/casetes/b1-es-60s.jsonl --a en
    python -m worker.traducir_casete fixtures/casetes/b1-es-60s.jsonl --a pt --salida x-trad-pt.jsonl

- `--a` es GENERICO por idioma destino: cualquier codigo que matchee lang_destino de
  contracts/esquema.json (`^[a-z]{2}(-[A-Z]{2})?$`, ej. es, en, pt, pt-BR), no una lista cerrada.
  worker/traductor.py arma el prompt con el nombre del idioma si lo conoce (worker.traductor.IDIOMAS)
  o con el codigo tal cual si no (Gemini lo entiende igual: probado con pt).
- Lee las lineas `emit` de tipo `text`, arma los lotes con la MISMA regla que el worker en vivo
  (worker.traductor.Lotes: LOTE_MAX textos o LOTE_S s desde el primero pendiente; ver worker/traductor.py) y hace UNA llamada real
  por lote (worker.traductor.Traductor: limitador 12 RPM por modelo, rotacion, contador en
  reportes/cuota-texto.log).
- Casete nuevo = el original (server/client/emit sin cambios) + eventos `translation` intercalados
  con t = cierre del lote + duracion real de la llamada (despues del ultimo text del lote) +
  eventos `partial` derivados SIN API de los `interimInputTranscription` crudos (worker.rederivar).
- Los `translation` llevan meta.source = {"casete": ..., "offline": true}: la llamada al modelo de
  texto fue real, pero hecha despues de la grabacion (no en vivo).
Exit: 0 = escrito (aunque haya lotes ok:false, que se cuentan); 2 = casete sin textos.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

from worker.casete import leer
from worker.contrato import mensaje
from worker.rederivar import parciales_de
from worker.traductor import (TIMEOUT_S, Lotes, Traductor, TransporteTexto, meta_traduccion)

# Mismo patron que contracts/esquema.json #/$defs/lang_destino: CUALQUIER codigo ISO de 2 letras,
# con region opcional (es, en, pt, pt-BR...). El traductor es generico por idioma destino (R19
# obliga EN->ES; ES->EN y cualquier otro par salen del mismo camino, ver worker/traductor.py IDIOMAS).
LANG_DESTINO_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


def lang_destino(valor: str) -> str:
    """type= de argparse para --a: valida el FORMATO (no una lista cerrada de idiomas)."""
    if not LANG_DESTINO_RE.match(valor):
        raise argparse.ArgumentTypeError(
            f"{valor!r}: codigo de idioma destino invalido (formato esperado: es, en, pt, pt-BR...)")
    return valor


def lotes_de(textos: list[tuple[int, str, float]], t_fin: float, lotes: Optional[Lotes] = None):
    lt = lotes or Lotes()
    out = []
    for seq, tx, t in textos:
        out += lt.agregar(seq, tx, t)
    if lt.pend:
        t_ult = lt.pend[-1].t
        out.append(lt.vaciar(max(t_ult, min(lt.pend[0].t + lt.ventana_s, t_fin))))
    return out


async def traducir_casete(entrada: str, a: str, salida: Optional[str] = None,
                          transporte: Optional[TransporteTexto] = None,
                          timeout_s: float = TIMEOUT_S, log=None, con_parciales: bool = True,
                          traductor: Optional[Traductor] = None, reintentos: int = 1) -> dict:
    cab, eventos = leer(entrada)
    emits = [e for e in eventos if e.get("dir") == "emit"]
    textos = [(e["payload"]["seq"], e["payload"]["text"], float(e["t"]))
              for e in emits if e.get("kind") == "text"]
    if not textos:
        raise ValueError(f"{entrada}: no hay emit text")
    p0 = next(e["payload"] for e in emits if e.get("kind") == "text")
    sid, lang = p0["session_id"], p0["lang"]
    fines = [float(e["t"]) for e in emits if e.get("kind") == "session_end"]
    t_fin = fines[-1] if fines else float(eventos[-1]["t"])
    lotes = lotes_de(textos, t_fin)
    kw = {"log": log} if log is not None else {}
    tr = traductor or Traductor(lang, a, transporte=transporte, timeout_s=timeout_s,
                                espera_cupo_s=180.0, reintentos=reintentos, **kw)
    resultados = await asyncio.gather(*[tr.traducir([i.text for i in L.items], [i.seq for i in L.items])
                                        for L in lotes])
    nombre = Path(entrada).name
    nuevas = []
    for L, res in zip(lotes, resultados):
        t = max(L.t_cierre, max(i.t for i in L.items)) + res.ms / 1000.0   # nunca antes del ultimo text
        m = mensaje("translation", sid, None, lang, t_emit=t, items=res.items,
                    meta=meta_traduccion(res, a, {"casete": nombre, "offline": True,
                                                  "lote": L.motivo, "intentos": res.intentos}))
        nuevas.append({"t": t, "dir": "emit", "kind": "translation", "payload": m})
    parciales = parciales_de(eventos, sid, lang) if con_parciales else []
    nuevas += parciales
    todo = sorted(eventos + nuevas, key=lambda e: float(e["t"]))   # estable: originales en su orden
    cab2 = dict(cab)
    cab2["derived_from"] = nombre
    cab2["generator"] = "worker.traducir_casete"
    cab2["traduccion"] = {"lang_to": a, "modelos": tr.modelos, "llamadas": tr.n_llamadas,
                          "por_modelo": tr.por_modelo, "lotes": len(lotes),
                          "lotes_ok_false": sum(1 for r in resultados if not r.ok)}
    cab2["nota"] = ("server/client/emit originales sin cambios; + emit translation (llamada real al "
                    "modelo de texto, offline) + emit partial derivados de interimInputTranscription")
    salida = salida or str(Path(entrada).with_name(Path(entrada).stem + "-trad.jsonl"))
    with open(salida, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cab2, ensure_ascii=False) + "\n")
        for e in todo:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    pares = []
    por_seq = {s: tx for s, tx, _ in textos}
    for r in resultados:
        for it in r.items:
            pares.append({"seq": it["seq"], "original": por_seq.get(it["seq"]), "traduccion": it["text"],
                          "ok": it["ok"]})
    return {"entrada": entrada, "salida": salida, "lang": lang, "a": a, "textos": len(textos),
            "eventos_translation": len(lotes), "items_ok": sum(p["ok"] for p in pares),
            "items_ok_false": sum(not p["ok"] for p in pares), "llamadas": tr.n_llamadas,
            "por_modelo": tr.por_modelo, "lotes_ok_false": cab2["traduccion"]["lotes_ok_false"],
            "parciales": len(parciales),
            "batch_ms": [r.ms for r in resultados], "pares": pares}


def pares(path: str) -> dict:
    """SIN API: lee un casete -trad y devuelve los pares (original -> traduccion) y los chequeos."""
    cab, ev = leer(path)
    textos = {e["payload"]["seq"]: e for e in ev if e.get("dir") == "emit" and e.get("kind") == "text"}
    trads = [e for e in ev if e.get("dir") == "emit" and e.get("kind") == "translation"]
    out = []
    for e in trads:
        m = e["payload"]
        for it in m["items"]:
            out.append({"seq": it["seq"], "ok": it["ok"], "original": textos[it["seq"]]["payload"]["text"],
                        "traduccion": it["text"], "model": m["meta"].get("model"),
                        "batch_ms": m["meta"].get("batch_ms")})
    ts = [float(e["t"]) for e in ev]
    return {"casete": path, "translation": len(trads), "items": len(out),
            "ok": sum(p["ok"] for p in out), "ok_false": sum(not p["ok"] for p in out),
            "t_despues_del_ultimo_text": all(
                float(e["t"]) >= max(float(textos[i["seq"]]["t"]) for i in e["payload"]["items"])
                for e in trads),
            "ordenado_por_t": ts == sorted(ts),
            "parciales": sum(1 for e in ev if e.get("dir") == "emit" and e.get("kind") == "partial"),
            "cabecera_traduccion": cab.get("traduccion"), "pares": out}


def construir_parser() -> argparse.ArgumentParser:
    """Parser del CLI, separado de main() para poder probarlo (--a) SIN llamar a la API."""
    ap = argparse.ArgumentParser(prog="python -m worker.traducir_casete")
    ap.add_argument("casete")
    ap.add_argument("--a", required=True, type=lang_destino,
                    help="codigo ISO del idioma destino (es, en, pt, pt-BR...; ver lang_destino)")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--timeout-s", type=float, default=TIMEOUT_S)
    ap.add_argument("--sin-parciales", action="store_true")
    ap.add_argument("--reintentos", type=int, default=1)
    return ap


def main(argv=None) -> int:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--pares":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        for c in sys.argv[2:]:
            print(json.dumps(pares(c), ensure_ascii=False))
        return 0
    ap = construir_parser()
    x = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        r = asyncio.run(traducir_casete(x.casete, x.a, x.salida, timeout_s=x.timeout_s,
                                        con_parciales=not x.sin_parciales, reintentos=x.reintentos))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
