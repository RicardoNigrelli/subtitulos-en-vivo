"""CLI de una corrida REAL (ASR contra Gemini Live) desde archivo / URL (R17a, R18).

    python -m worker.run --archivo fixtures/audio/clips/x.wav --sesion demo-en --lang en \
        [--inicio 0] [--duracion 60] [--hub ws://localhost:8100/ingest] \
        [--casete fixtures/casetes/x.jsonl] [--titulo ...] [--vocab "Nerdearla,Nombre"] \
        [--url https://...] [--tope-envio-s N]

Gasta cuota: antes de arrancar mira reportes/cuota-audio.log y no pasa del presupuesto del bloque.
Al terminar apende la linea de cuota (fecha-hora | sesion | segundos_enviados | archivo_fuente).
Exit: 0 = hubo texto; 3 = sin texto; 4 = sin presupuesto; 2 = error de uso.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from worker import cuota
from worker.casete import Grabador
from worker.emisor import Emisor
from worker.gemini import MODELO_LIVE_DEFAULT
from worker.ingesta import fuente_ffmpeg
from worker.session import SessionWorker
from worker.transporte import TransporteGemini


def _args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m worker.run")
    ap.add_argument("--archivo", required=True, help="archivo de audio o URL que ffmpeg entienda")
    ap.add_argument("--sesion", required=True)
    ap.add_argument("--lang", required=True, choices=["en", "es"])
    ap.add_argument("--inicio", type=float, default=0.0)
    ap.add_argument("--duracion", type=float, default=None)
    ap.add_argument("--hub", default=None, help="ws://localhost:8100/ingest")
    ap.add_argument("--casete", default=None)
    ap.add_argument("--titulo", default=None)
    ap.add_argument("--url", default=None, help="URL de origen (se anota en el casete)")
    ap.add_argument("--vocab", default="Nerdearla", help="custom_vocabulary, separado por comas")
    ap.add_argument("--modelo", default=MODELO_LIVE_DEFAULT)
    ap.add_argument("--tope-envio-s", type=float, default=None,
                    help="maximo de segundos de audio a enviar en esta corrida")
    ap.add_argument("--key", default="GEMINI_API_KEY", help="NOMBRE de la variable de la key")
    return ap.parse_args(argv)


async def correr(a) -> int:
    restante = cuota.restante_s()
    if restante <= 0:
        print(f"[run] SIN PRESUPUESTO: gastado {cuota.gastado_s():.1f} s de "
              f"{cuota.PRESUPUESTO_S:.0f} s en el bloque. No se corre.", file=sys.stderr)
        return 4
    tope = restante if a.tope_envio_s is None else min(a.tope_envio_s, restante)
    vocab = [v.strip() for v in a.vocab.split(",") if v.strip()]
    tr = TransporteGemini(a.modelo, a.lang, vocab, nombre_key=a.key)
    casete = a.casete or f"fixtures/casetes/{a.sesion}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    source = {"file": a.archivo, "url": a.url, "start_s": a.inicio, "dur_s": a.duracion}
    titulo = a.titulo or Path(a.archivo).stem
    from google.genai import __version__ as genai_version
    from worker import ventanas as V
    cortador = {"ventana_s": V.VENTANA_S, "tolerancia_s": V.TOLERANCIA_S, "solape_s": V.SOLAPE_S,
                "gap_s": V.GAP_S, "percentil": V.PERCENTIL, "calibracion_s": V.CALIBRACION_S,
                "chunk_s": 0.1, "chunk_bytes": 3200}
    rec = Grabador(casete, {"session_id": a.sesion, "lang": a.lang, "model": a.modelo,
                            "config": tr.config_enviada()["config"], "source": source,
                            "title": titulo, "generator": "worker.run", "cortador": cortador,
                            "sdk": f"google-genai {genai_version}", "started_at": time.time()})
    emisor = Emisor(a.hub) if a.hub else None
    if emisor:
        emisor.iniciar()
    fuente = fuente_ffmpeg(a.archivo, a.inicio, a.duracion, tiempo_real=True)
    w = SessionWorker(a.sesion, a.lang, tr, fuente, grabador=rec, emisor=emisor, titulo=titulo,
                      source=source, tope_envio_s=tope,
                      log=lambda s: print(s, file=sys.stderr, flush=True))
    res = None
    try:
        res = await w.correr()
    finally:
        seg = w.res.segundos_enviados
        linea = cuota.registrar(a.sesion, seg, a.archivo)
        print(f"[run] cuota: {linea}", file=sys.stderr)
        if emisor:
            ok = await emisor.vaciar(3.0)
            print(f"[run] hub: enviados={emisor.n_enviados} backlog_pendiente={emisor.pendientes()} "
                  f"vaciado={ok}", file=sys.stderr)
            await emisor.detener()
        rec.cerrar()
    resumen = {"sesion": res.session_id, "casete": casete, "segundos_enviados": res.segundos_enviados,
               "ventanas": res.ventanas, "textos": res.textos, "mensajes_server": res.mensajes_server,
               "goaway": res.goaway, "cierre": res.cierre, "motivo_fin": res.motivo_fin,
               "seq_final": res.seq_final, "duracion_s": round(res.t_fin - res.t_inicio, 1),
               "errores": res.errores}
    print(json.dumps(resumen, ensure_ascii=False))
    return 0 if res.textos > 0 else 3


def main(argv=None) -> int:
    a = _args(argv)
    return asyncio.run(correr(a))


if __name__ == "__main__":
    sys.exit(main())
