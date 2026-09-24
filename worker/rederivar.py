"""Recalcula OFFLINE la capa `emit` de texto de un casete con worker/mapeo.py (sin API, sin cuota).

    python -m worker.rederivar fixtures/casetes/b1-en-60s.jsonl fixtures/casetes/b1-en-60s-rederivado.jsonl

Por que existe: el casete b1-en-60s se grabo con la primera version del mapeo texto->ventana
(esperaba turnComplete, que el server no manda) y todos sus `text` quedaron con el rango de la
ventana 0. Las capas server y client son crudas y correctas; de ahi se recalcula el rango.

- server y client: se copian SIN CAMBIOS.
- emit no-texto: se copian sin cambios.
- emit text: mismo texto, seq y t_emit; se recalculan audio_start, audio_end y t_captured, y se
  marca meta.rederivado = true. La cabecera lleva derived_from y generator.
El original no se toca.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from worker.casete import leer
from worker.contrato import mensaje
from worker.mapeo import InfoVentana, Mapeador, segundos


def parciales_de(eventos: list[dict], session_id: str, lang: str) -> list[dict]:
    """Eventos emit `partial` (contrato B2) derivados SIN API de los interimInputTranscription crudos.

    Misma regla que el SessionWorker en vivo: audio_start = inicio de la ventana mas vieja sin
    ACTIVITY_END del server (el turno en curso) o, si no hay, la ventana abierta; t_captured =
    t_captured de la ultima ventana cerrada (<= t_emit); t_emit = t de llegada del frame.
    """
    pend: list[tuple[float, float]] = []      # (audio_start, fin_acum)
    acum = 0.0
    abierta = None
    ult_tc = None
    out = []
    for e in eventos:
        d, k, p = e.get("dir"), e.get("kind"), e.get("payload") or {}
        if d == "client" and k == "activity_start":
            abierta = p.get("audio_start")
        elif d == "client" and k == "ventana":
            acum += p.get("n_chunks", 0) * 0.1
            pend.append((p["audio_start"], round(acum, 3)))
            ult_tc = p.get("t_captured") or e["t"]
            abierta = None
        elif d == "server":
            va = p.get("voiceActivity") or {}
            if va.get("type") == "ACTIVITY_END":
                off = segundos(va.get("audioOffset"))
                if off is not None:
                    while pend and pend[0][1] <= off + 0.05:
                        pend.pop(0)
            sc = p.get("serverContent") or {}
            it = (sc.get("interimInputTranscription") or {}).get("text")
            if it:
                a0 = pend[0][0] if pend else abierta
                tc = ult_tc if (ult_tc is not None and ult_tc <= e["t"]) else None
                m = mensaje("partial", session_id, None, lang, text=it, audio_start=a0,
                            t_captured=tc, t_emit=e["t"], meta={"rederivado": True})
                out.append({"t": e["t"], "dir": "emit", "kind": "partial", "payload": m})
    return out


def asignaciones_de(eventos: list[dict]):
    mapa = Mapeador()
    acum = 0.0
    out = []
    for e in eventos:
        d, k, p = e.get("dir"), e.get("kind"), e.get("payload") or {}
        if d == "client" and k == "ventana":
            acum += p.get("n_chunks", 0) * 0.1
            tc = p.get("t_captured") or e["t"]
            mapa.ventana_cerrada(InfoVentana(p["ventana"], p["audio_start"], p["audio_end"], tc,
                                             round(acum, 3)))
        elif d == "server":
            sc = p.get("serverContent") or {}
            tx = (sc.get("inputTranscription") or {}).get("text")
            if tx:
                mapa.texto(e["t"], tx)
            va = p.get("voiceActivity") or {}
            if va.get("type") == "ACTIVITY_END":
                out += mapa.activity_end(segundos(va.get("audioOffset")))
    out += mapa.vaciar()
    return out


def rederivar(entrada: str, salida: str) -> dict:
    cab, eventos = leer(entrada)
    asig = asignaciones_de(eventos)
    textos_emit = [e for e in eventos if e.get("dir") == "emit" and e.get("kind") == "text"]
    if [a.text for a in asig] != [e["payload"]["text"] for e in textos_emit]:
        raise ValueError("los textos del server no coinciden 1:1 con los emit de texto")
    cab2 = dict(cab)
    cab2["derived_from"] = Path(entrada).name
    cab2["generator"] = "worker.rederivar"
    cab2["nota"] = ("server y client copiados sin cambios; emit text con audio_start/audio_end/"
                    "t_captured recalculados por worker/mapeo.py")
    it = iter(asig)
    cambiados = 0
    with open(salida, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cab2, ensure_ascii=False) + "\n")
        for e in eventos:
            if e.get("dir") == "emit" and e.get("kind") == "text":
                a = next(it)
                e2 = copy.deepcopy(e)
                m = e2["payload"]
                antes = (m.get("audio_start"), m.get("audio_end"), m.get("t_captured"))
                m["audio_start"], m["audio_end"] = a.audio_start, a.audio_end
                if a.t_captured is not None and a.t_captured <= m["t_emit"]:
                    m["t_captured"] = a.t_captured
                m.setdefault("meta", {})["rederivado"] = True
                e2["ventanas"] = a.ventanas
                e2.pop("ventana", None)
                cambiados += antes != (m.get("audio_start"), m.get("audio_end"), m.get("t_captured"))
                f.write(json.dumps(e2, ensure_ascii=False) + "\n")
            else:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return {"entrada": entrada, "salida": salida, "textos": len(asig), "cambiados": cambiados}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.rederivar")
    ap.add_argument("entrada")
    ap.add_argument("salida")
    a = ap.parse_args(argv)
    print(json.dumps(rederivar(a.entrada, a.salida), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
