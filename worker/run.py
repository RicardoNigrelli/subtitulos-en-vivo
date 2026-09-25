"""CLI de una corrida REAL (ASR contra Gemini Live) desde archivo / URL (R17a, R18).

    python -m worker.run --archivo fixtures/audio/clips/x.wav --sesion demo-en --lang en \
        [--inicio 0] [--duracion 60] [--hub ws://localhost:8100/ingest] \
        [--casete fixtures/casetes/x.jsonl] [--titulo ...] [--vocab "Nerdearla,Nombre"] \
        [--url https://...] [--tope-envio-s N] [--vad-auto] [--sin-reabrir]
        [--transporte gemini | casete:<archivo.jsonl>[:mudo=S]]
    python -m worker.run --fuente url --url udp://127.0.0.1:9000 --sesion x --lang en ...   # stream
    python -m worker.run --fuente mic --dispositivo "Microphone (X)" --sesion x --lang es ... # dshow
    python -m worker.run --listar-dispositivos
    ... --agenda fixtures/agenda.json [--charla booch-en]    # glosario R8c -> custom_vocabulary

B8: --fuente archivo|url|mic (worker/ingesta.py: opciones_fuente). url = cualquier entrada que ffmpeg
abra (HTTP/HLS/RTMP/SRT/UDP); mic = dshow en Windows. --agenda: el vocabulario de la charla
(worker/glosario.py) se SUMA a --vocab.

B3: reabrir con solape ACTIVO por defecto (worker/session.py: cierre/atasco/preventiva/goaway).
--vad-auto: VAD automatico del server (sin activity_start/end nuestros), NO_INTERRUPTION igual.
--transporte casete:...: TRANSPORTE DE TEST rotulado (worker/transporte_casete.py): NO habla con
Gemini, reproduce las lineas server de un casete real sincronizadas con el audio enviado; no gasta
cuota ni la registra. Lo usa qa para el e2e del watchdog sobre el bus real.

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
from worker.ingesta import comando_ffmpeg, fuente_ffmpeg
from worker.session import SessionWorker
from worker.transporte import TransporteGemini


def _args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m worker.run")
    ap.add_argument("--archivo", default=None, help="archivo de audio o URL que ffmpeg entienda")
    ap.add_argument("--fuente", default="archivo", choices=["archivo", "url", "mic"],
                    help="archivo (default) | url (stream: --url o --archivo) | mic (--dispositivo)")
    ap.add_argument("--dispositivo", default=None,
                    help='nombre del dispositivo de captura (dshow), p.ej. "Microphone (Realtek)"')
    ap.add_argument("--agenda", default=None, help="agenda JSON (R8c): glosario -> custom_vocabulary")
    ap.add_argument("--charla", default=None, help="id de la charla en la agenda (default: --sesion)")
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
    ap.add_argument("--ventana-s", type=float, default=None, help="ventana objetivo (default 3,0)")
    ap.add_argument("--gap-s", type=float, default=None, help="gap end->start (default 0,7)")
    ap.add_argument("--drenaje-s", type=float, default=30.0, help="espera de turnos al fin de la fuente")
    ap.add_argument("--traducir-a", default="auto", help="es|en|none (auto: en->es, es->en)")
    ap.add_argument("--timeout-trad-s", type=float, default=20.0,
                    help="timeout por llamada del traductor (B4: 20 s, sin reintento por timeout)")
    ap.add_argument("--vad-auto", action="store_true",
                    help="VAD automatico del server ACTIVADO (sin turnos manuales)")
    ap.add_argument("--sin-reabrir", action="store_true", help="desactiva reabrir con solape")
    ap.add_argument("--transporte", default="gemini",
                    help="gemini | casete:<archivo.jsonl>[:mudo=S] (test, rotulado, sin API)")
    a = ap.parse_args(argv)
    if a.fuente == "archivo" and not a.archivo:
        ap.error("--fuente archivo necesita --archivo")
    if a.fuente == "url" and not (a.url or a.archivo):
        ap.error("--fuente url necesita --url (o --archivo con la URL)")
    if a.fuente == "mic" and not a.dispositivo:
        ap.error('--fuente mic necesita --dispositivo "<nombre>" (listar: --listar-dispositivos)')
    return a


def entrada_de(a) -> tuple[str, str | None, list[str], str]:
    """(entrada, formato, opciones de entrada, nombre legible) segun --fuente."""
    from worker.ingesta import opciones_fuente
    valor = {"archivo": a.archivo, "url": a.url or a.archivo, "mic": a.dispositivo}[a.fuente]
    ent, fmt, extra = opciones_fuente(a.fuente, valor)
    return ent, fmt, extra, valor


async def correr(a) -> int:
    es_casete = a.transporte.startswith("casete:")
    restante = float("inf") if es_casete else cuota.restante_s()
    if restante <= 0:
        print(f"[run] SIN PRESUPUESTO: gastado {cuota.gastado_s():.1f} s de "
              f"{cuota.PRESUPUESTO_S:.0f} s en el bloque. No se corre.", file=sys.stderr)
        return 4
    tope = (None if a.tope_envio_s is None else a.tope_envio_s) if es_casete else (
        restante if a.tope_envio_s is None else min(a.tope_envio_s, restante))
    vocab = [v.strip() for v in a.vocab.split(",") if v.strip()]
    if a.agenda:
        from worker.glosario import vocabulario
        for t in vocabulario(a.agenda, a.charla or a.sesion):
            if t.lower() not in {v.lower() for v in vocab}:
                vocab.append(t)
        print(f"[run] glosario de {a.agenda} ({a.charla or a.sesion}): {len(vocab)} terminos "
              f"-> custom_vocabulary", file=sys.stderr)
    entrada, formato, opciones, valor = entrada_de(a)
    if es_casete:
        from worker.transporte_casete import FabricaCasete
        fab_casete = FabricaCasete.desde_spec(a.transporte)
        fab_casete.turnos = not a.vad_auto
        tr = fab_casete(0.0)

        def fabrica(corte_s: float):
            return fab_casete(corte_s)
    else:
        tr = TransporteGemini(a.modelo, a.lang, vocab, nombre_key=a.key, auto_vad=a.vad_auto)

        def fabrica(corte_s: float):
            return TransporteGemini(a.modelo, a.lang, vocab, nombre_key=a.key, auto_vad=a.vad_auto)
    casete = a.casete or f"fixtures/casetes/{a.sesion}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    source = {"file": a.archivo, "url": a.url, "start_s": a.inicio, "dur_s": a.duracion,
              "kind": a.fuente, "device": a.dispositivo,
              "agenda": {"file": a.agenda, "charla": a.charla or a.sesion} if a.agenda else None}
    titulo = a.titulo or (Path(a.archivo).stem if a.fuente == "archivo" else f"{a.fuente}: {valor}")
    from google.genai import __version__ as genai_version
    from worker import ventanas as V
    ventana_s = a.ventana_s if a.ventana_s is not None else V.VENTANA_S
    gap_s = a.gap_s if a.gap_s is not None else V.GAP_S
    cortador = {"ventana_s": ventana_s, "tolerancia_s": V.TOLERANCIA_S, "solape_s": V.SOLAPE_S,
                "gap_s": gap_s, "percentil": V.PERCENTIL, "calibracion_s": V.CALIBRACION_S,
                "chunk_s": 0.1, "chunk_bytes": 3200, "drenaje_s": a.drenaje_s,
                "turnos": "vad_auto" if a.vad_auto else "manuales",
                "reabrir": not a.sin_reabrir}
    from worker.session import ConfigReabrir
    cfg_reabrir = ConfigReabrir.desde_env()
    cortador["cfg_reabrir"] = vars(cfg_reabrir)
    destino = a.traducir_a
    if destino == "auto":
        destino = "es" if a.lang == "en" else "en"
    traductor = None
    if destino and destino != "none":
        from worker.traductor import Traductor
        traductor = Traductor(a.lang, destino, timeout_s=a.timeout_trad_s)
    rec = Grabador(casete, {"session_id": a.sesion, "lang": a.lang, "model": a.modelo,
                            "config": tr.config_enviada()["config"], "source": source,
                            "title": titulo, "cortador": cortador,
                            "generator": "worker.run" + (" transporte=" + a.transporte if es_casete else ""),
                            "replay_test": es_casete,
                            "sdk": f"google-genai {genai_version}", "started_at": time.time()})
    emisor = Emisor(a.hub) if a.hub else None
    seq0 = 0
    if emisor:
        emisor.iniciar()
        # seq tras reinicio: seguir desde auth_ok.last_seq[session_id] + 1 (el hub no rebobina)
        if await emisor.esperar_conexion(5.0):
            seq0 = int(emisor.last_seq_hub.get(a.sesion, 0) or 0)
        print(f"[run] seq inicial = {seq0} (auth_ok.last_seq; conectado={emisor.conectado})",
              file=sys.stderr)
    rec.client("seq_inicial", {"last_seq_hub": seq0, "conectado": bool(emisor and emisor.conectado)})
    fuente = fuente_ffmpeg(entrada, a.inicio, a.duracion, tiempo_real=True,
                           formato_entrada=formato, opciones_entrada=opciones)
    print(f"[run] fuente {a.fuente}: {' '.join(comando_ffmpeg(entrada, a.inicio, a.duracion, formato, opciones))}",
          file=sys.stderr)
    w = SessionWorker(a.sesion, a.lang, tr, fuente, grabador=rec, emisor=emisor, titulo=titulo,
                      source=source, tope_envio_s=tope, espera_final_s=a.drenaje_s, gap_s=gap_s,
                      cortador=V.Cortador(ventana_s=ventana_s, gap_s=gap_s),
                      traductor=traductor, seq_inicial=seq0,
                      log=lambda s: print(s, file=sys.stderr, flush=True),
                      fabrica=None if a.sin_reabrir else fabrica, reabrir=cfg_reabrir,
                      turnos_manuales=not a.vad_auto)
    res = None
    try:
        res = await w.correr()
    finally:
        seg = w.res.segundos_enviados
        if es_casete:
            print(f"[run] transporte de casete (test): {seg} s NO van a Gemini, no se registran",
                  file=sys.stderr)
        else:
            linea = cuota.registrar(a.sesion, seg, valor)
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
               "errores": res.errores, "seq_inicial": seq0, "parciales": w.n_parciales,
               "drenaje": w.drenaje, "rotaciones": res.rotaciones,
               "descartados_vieja": res.descartados_vieja, "reenviados_s": round(res.reenviados_s, 1),
               "turnos": "vad_auto" if a.vad_auto else "manuales", "transporte": a.transporte,
               "traduccion": None if traductor is None else {
                   "a": traductor.a, "llamadas": traductor.n_llamadas, "lotes": traductor.n_lotes,
                   "lotes_ok_false": traductor.n_ok_false, "por_modelo": traductor.por_modelo,
                   "max_en_vuelo": traductor.max_en_vuelo, "timeouts": traductor.n_timeout,
                   "reintentos": traductor.n_reintentos},
               "dedup": {"recortados": res.dedup_recortados, "descartados": res.dedup_descartados},
               "rotulo": w.rotulo}
    print(json.dumps(resumen, ensure_ascii=False))
    return 0 if res.textos > 0 else 3


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--listar-dispositivos" in argv:
        from worker.ingesta import listar_dispositivos
        nombres, crudo = listar_dispositivos()
        print(crudo.strip(), file=sys.stderr)
        print(json.dumps({"dshow_audio": nombres}, ensure_ascii=False))
        return 0 if nombres else 3
    a = _args(argv)
    return asyncio.run(correr(a))


if __name__ == "__main__":
    sys.exit(main())
