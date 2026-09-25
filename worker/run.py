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
OPERACION EN SALA (--fuente mic|url): sin --duracion (o --duracion 0) corre SIN LIMITE (ni -t ni tope
de cuota del bloque; los segundos se registran igual) hasta Ctrl+C / Ctrl+Break / SIGTERM, que hacen
una parada limpia: session_end con meta.reason "stop", conexion cerrada en orden, casete cerrado,
exit 0. Si ffmpeg muere o no entrega bytes durante FUENTE_TIMEOUT_S (10 s), la fuente se reabre con
backoff 1, 2, 4... 30 s (worker/ingesta.py: FuenteReabrible) sin cerrar la sesion publica: sale un
`error` con meta.code "fuente" y la sesion sigue. --fuente archivo termina al acabar el archivo.

Exit: 0 = hubo texto o parada pedida; 3 = sin texto; 4 = sin presupuesto; 2 = error de uso.
"""
from __future__ import annotations

import os

# Adversario final (escala 3): OpenBLAS reserva memoria por hilo al importar numpy (16 hilos: ~504 MB
# de commit por worker en Windows; con 1 hilo, 22,5 MB). El worker no hace algebra pesada: 1 hilo
# alcanza. Va ANTES de cualquier import que traiga numpy; si el operador lo define, se respeta.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

from worker import cuota  # noqa: E402
from worker.seguridad import SLUG, sin_credenciales, validar_sesion  # noqa: E402
from worker.casete import Grabador
from worker.emisor import Emisor
from worker.gemini import MODELO_LIVE_DEFAULT
from worker.ingesta import FuenteReabrible, comando_ffmpeg, fuente_ffmpeg
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
    ap.add_argument("--lang", required=True, choices=LANGS_ORIGEN,
                    help="idioma de la charla; probados en vivo: en, es")
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
    ap.add_argument("--ventana-s", type=float, default=None, help="ventana objetivo (default env VENTANA_S o 3,0; tolerancia proporcional)")
    ap.add_argument("--gap-s", type=float, default=None, help="gap end->start (default 0,7)")
    ap.add_argument("--drenaje-s", type=float, default=30.0, help="espera de turnos al fin de la fuente")
    ap.add_argument("--traducir-a", default="auto",
                    help="lista de idiomas destino separados por coma (es,pt,fr) | auto (en->es, "
                         "es->en) | none. Un Traductor por idioma, misma key: cada idioma suma llamadas")
    ap.add_argument("--timeout-trad-s", type=float, default=20.0,
                    help="timeout por llamada del traductor (B4: 20 s, sin reintento por timeout)")
    ap.add_argument("--vad-auto", action="store_true",
                    help="VAD automatico del server ACTIVADO (sin turnos manuales)")
    ap.add_argument("--sin-reabrir", action="store_true", help="desactiva reabrir con solape")
    ap.add_argument("--transporte", default="gemini",
                    help="gemini | casete:<archivo.jsonl>[:mudo=S] (test, rotulado, sin API)")
    a = ap.parse_args(argv)
    if not validar_sesion(a.sesion):
        # M5: el id va al nombre del casete (path traversal) y el hub rechaza todo lo que no cumpla
        # el patron (se gastaria cuota contra una sala vacia)
        ap.error(f"--sesion {a.sesion!r} invalida: tiene que cumplir {SLUG.pattern} "
                 f"(minusculas, digitos, '-' y '_'; hasta 64; empieza con letra o digito)")
    try:
        destinos(a.traducir_a, a.lang)
    except ValueError as e:
        ap.error(f"--traducir-a: {e}")
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


def armar_transportes(a, vocab: list[str]):
    """(transporte inicial, fabrica(corte_s)) de la sala. Live: la conexion inicial y CADA rotacion
    usan --key (a.key). Casete: transporte de test rotulado, sin API."""
    if a.transporte.startswith("casete:"):
        from worker.transporte_casete import FabricaCasete
        fab_casete = FabricaCasete.desde_spec(a.transporte)
        fab_casete.turnos = not a.vad_auto
        tr = fab_casete(0.0)

        def fabrica(corte_s: float):
            return fab_casete(corte_s)
        return tr, fabrica

    def fabrica(corte_s: float):
        return TransporteGemini(a.modelo, a.lang, vocab, nombre_key=a.key, auto_vad=a.vad_auto)
    return fabrica(0.0), fabrica


# idioma de la charla (--lang). PROBADOS EN VIVO: en, es. El contrato (contracts/esquema.json#lang_origen)
# hoy acepta solo en/es: con otro, el hub rechaza los mensajes hasta que backend amplie ese enum.
LANGS_ORIGEN = ["en", "es", "pt", "fr", "de", "it"]
LANGS_PROBADOS = ("en", "es")
_LANG_DESTINO = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")      # = contracts/esquema.json#lang_destino


def destinos(valor: Optional[str], lang: str) -> list[str]:
    """--traducir-a -> lista de idiomas destino, sin repetir y sin el de origen. `auto` = el opuesto
    en/es (en->es, es->en; otro origen -> es, R19); `none`/vacio = []. ValueError si un codigo no
    cumple el patron del contrato."""
    out: list[str] = []
    for x in (valor or "").split(","):
        x = x.strip()
        if not x or x.lower() == "none":
            continue
        if x.lower() == "auto":
            x = "en" if lang == "es" else "es"
        if not _LANG_DESTINO.match(x):
            raise ValueError(f"idioma destino invalido: {x!r} (codigo ISO: es, pt, pt-BR)")
        if x != lang and x not in out:
            out.append(x)
    return out


def armar_traductores(a) -> list:
    """Un Traductor por idioma destino. Todos comparten el MISMO transporte (key --key) y el MISMO
    Limitador: cortacircuito, token bucket y reservas por (key, modelo) son uno solo para la sala;
    cada idioma suma sus llamadas a esa cuenta."""
    langs = destinos(a.traducir_a, a.lang)
    if not langs:
        return []
    from worker.traductor import Limitador, Traductor, TransporteGenAI, modelos_por_defecto
    tr = TransporteGenAI(nombre_key=a.key)
    modelos = modelos_por_defecto()
    lim = Limitador(modelos, key=a.key) if len(langs) > 1 else None
    return [Traductor(a.lang, x, timeout_s=a.timeout_trad_s, transporte=tr, modelos=modelos,
                      limitador=lim) for x in langs]


def armar_traductor(a):
    """Traductor de la sala o None (--traducir-a none). Usa la MISMA key que la Live API (--key): "una
    key/proyecto por sala" cubre las dos llamadas (antes el de texto usaba siempre GEMINI_API_KEY).
    FINAL 25/09: las reservas de texto entre procesos (reportes/cuota-texto-reservas.jsonl) se cuentan
    por (key, modelo): la key del traductor es la del transporte (--key)."""
    t = armar_traductores(a)
    return t[0] if t else None


def _resumen_trad(t) -> dict:
    return {"a": t.a, "llamadas": t.n_llamadas, "lotes": t.n_lotes, "lotes_ok_false": t.n_ok_false,
            "timeouts": t.n_timeout, "por_modelo": t.por_modelo}


async def correr(a) -> int:
    es_casete = a.transporte.startswith("casete:")
    # sala: mic/url sin --duracion corre sin limite; la cuota del bloque (guarda de desarrollo) no la
    # corta ni le impide arrancar: los segundos se registran igual al terminar
    sin_limite = a.fuente in ("mic", "url") and not a.duracion
    # CUOTA_GUARDA=0: sin la guarda de presupuesto de DESARROLLO (la usa ops/control.py: una sala creada
    # por el staff desde el panel no puede negarse a arrancar por el tope de 30 min de la vibeathon)
    guarda = os.environ.get("CUOTA_GUARDA", "1").strip() != "0"
    restante = float("inf") if (es_casete or sin_limite or not guarda) else cuota.restante_s()
    if sin_limite and not es_casete:
        print(f"[run] sala sin limite ({a.fuente}): gastado del bloque {cuota.gastado_s():.1f} s de "
              f"{cuota.PRESUPUESTO_S:.0f} s; no corta. Parar: Ctrl+C", file=sys.stderr)
    if restante <= 0:
        print(f"[run] SIN PRESUPUESTO: gastado {cuota.gastado_s():.1f} s de "
              f"{cuota.PRESUPUESTO_S:.0f} s en el bloque. No se corre.", file=sys.stderr)
        return 4
    tope = (None if a.tope_envio_s is None else a.tope_envio_s) if (es_casete or sin_limite or not guarda) else (
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
    tr, fabrica = armar_transportes(a, vocab)
    # sin --casete: el camino real graba en fixtures/casetes/ (casete de evidencia); el transporte de
    # casete (test) graba en reportes/, nunca en fixtures/
    casete = a.casete or (f"reportes/casete-test-{a.sesion}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl" if es_casete
                          else f"fixtures/casetes/{a.sesion}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    # A2: ni userinfo ni query ni fragment (stream keys, passphrase, firmas) en session_start, casete,
    # log ni cuota: el hub publica source y title sin auth y fixtures/casetes/ se versiona
    valor_publico = sin_credenciales(valor) if a.fuente == "url" else valor
    source = {"file": sin_credenciales(a.archivo), "url": sin_credenciales(a.url), "start_s": a.inicio, "dur_s": a.duracion,
              "kind": a.fuente, "device": a.dispositivo,
              "agenda": {"file": a.agenda, "charla": a.charla or a.sesion} if a.agenda else None}
    titulo = a.titulo or (Path(a.archivo).stem if a.fuente == "archivo" else f"{a.fuente}: {valor_publico}")
    from google.genai import __version__ as genai_version
    from worker import ventanas as V
    ventana_s = a.ventana_s if a.ventana_s is not None else V.VENTANA_S
    gap_s = a.gap_s if a.gap_s is not None else V.GAP_S
    tolerancia_s = V.tolerancia_para(ventana_s)
    cortador = {"ventana_s": ventana_s, "tolerancia_s": tolerancia_s, "solape_s": V.SOLAPE_S,
                "gap_s": gap_s, "percentil": V.PERCENTIL, "calibracion_s": V.CALIBRACION_S,
                "chunk_s": 0.1, "chunk_bytes": 3200, "drenaje_s": a.drenaje_s,
                "turnos": "vad_auto" if a.vad_auto else "manuales",
                "reabrir": not a.sin_reabrir}
    from worker.session import ConfigReabrir
    cfg_reabrir = ConfigReabrir.desde_env()
    cortador["cfg_reabrir"] = vars(cfg_reabrir)
    traductores = armar_traductores(a)
    traductor = traductores[0] if traductores else None
    if a.lang not in LANGS_PROBADOS:
        print(f"[run] AVISO: --lang {a.lang} NO probado en vivo (probados en vivo: en, es).", file=sys.stderr)
    if len(traductores) > 1:
        print(f"[run] traduccion a {','.join(t.a for t in traductores)}: {len(traductores)} llamadas "
              f"por texto a la misma key", file=sys.stderr)
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
    if a.fuente == "archivo":
        fuente = fuente_ffmpeg(entrada, a.inicio, a.duracion, tiempo_real=True,
                               formato_entrada=formato, opciones_entrada=opciones)
    else:
        # mic/url: se reabre sola si se cae (FUENTE_TIMEOUT_S, backoff hasta 30 s); --duracion N la
        # corta a los N s de audio, sin --duracion no tiene fin propio (Ctrl+C / SIGTERM)
        fuente = FuenteReabrible(entrada, formato, opciones, inicio=a.inicio, max_s=a.duracion or None,
                                 log=lambda s: print(s, file=sys.stderr, flush=True))
    cmd_log = [sin_credenciales(x) for x in comando_ffmpeg(entrada, a.inicio, a.duracion, formato, opciones)]
    print(f"[run] fuente {a.fuente}: {' '.join(cmd_log)}",
          file=sys.stderr)
    w = SessionWorker(a.sesion, a.lang, tr, fuente, grabador=rec, emisor=emisor, titulo=titulo,
                      source=source, tope_envio_s=tope, espera_final_s=a.drenaje_s, gap_s=gap_s,
                      cortador=V.Cortador(ventana_s=ventana_s, tolerancia_s=tolerancia_s, gap_s=gap_s),
                      traductor=traductores or None, seq_inicial=seq0,
                      log=lambda s: print(s, file=sys.stderr, flush=True),
                      fabrica=None if a.sin_reabrir else fabrica, reabrir=cfg_reabrir,
                      turnos_manuales=not a.vad_auto, vocab=vocab)
    loop = asyncio.get_running_loop()
    senales = [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])
    previos = {}

    def _senal(signum, _frame):
        # 1a senal: parada limpia; 2a: se restauran los handlers (la siguiente corta en seco)
        for sg, h in previos.items():
            signal.signal(sg, h)
        print(f"[run] senal {signal.Signals(signum).name}: parada limpia (otra vez = corte en seco)",
              file=sys.stderr, flush=True)
        loop.call_soon_threadsafe(w.parar, "stop")

    for sg in senales:
        try:
            previos[sg] = signal.signal(sg, _senal)
        except (ValueError, OSError):
            pass
    res = None
    try:
        res = await w.correr()
    finally:
        seg = w.res.segundos_enviados
        if es_casete:
            print(f"[run] transporte de casete (test): {seg} s NO van a Gemini, no se registran",
                  file=sys.stderr)
        else:
            linea = cuota.registrar(a.sesion, seg, valor_publico)
            print(f"[run] cuota: {linea}", file=sys.stderr)
        if emisor:
            ok = await emisor.vaciar(3.0)
            print(f"[run] hub: enviados={emisor.n_enviados} backlog_pendiente={emisor.pendientes()} "
                  f"vaciado={ok}", file=sys.stderr)
            await emisor.detener()
        rec.cerrar()
        for sg, h in previos.items():
            try:
                signal.signal(sg, h)
            except (ValueError, OSError):
                pass
    resumen = {"sesion": res.session_id, "casete": casete, "segundos_enviados": res.segundos_enviados,
               "ventanas": res.ventanas, "textos": res.textos, "mensajes_server": res.mensajes_server,
               "goaway": res.goaway, "cierre": res.cierre, "motivo_fin": res.motivo_fin,
               "seq_final": res.seq_final, "duracion_s": round(res.t_fin - res.t_inicio, 1),
               "errores": res.errores, "seq_inicial": seq0, "parciales": w.n_parciales,
               "drenaje": w.drenaje, "rotaciones": res.rotaciones,
               "descartados_vieja": res.descartados_vieja, "reenviados_s": round(res.reenviados_s, 1),
               "caidas_fuente": res.caidas_fuente,
               "turnos": "vad_auto" if a.vad_auto else "manuales", "transporte": a.transporte,
               "traducciones": [_resumen_trad(t) for t in traductores],
               "traduccion": None if traductor is None else {
                   "a": traductor.a, "llamadas": traductor.n_llamadas, "lotes": traductor.n_lotes,
                   "lotes_ok_false": traductor.n_ok_false, "por_modelo": traductor.por_modelo,
                   "max_en_vuelo": traductor.max_en_vuelo, "timeouts": traductor.n_timeout,
                   "reintentos": traductor.n_reintentos, "hedge_s": traductor.hedge_s,
                   "cubiertos": traductor.n_cubiertos, "cubiertos_ganados": traductor.n_cubiertos_ganados,
                   "cancelados": traductor.n_cancelados, "key": traductor.key,
                   "modo": traductor.modo, "textos_por_nivel": traductor.textos_por_nivel},
               "dedup": {"recortados": res.dedup_recortados, "descartados": res.dedup_descartados},
               "rotulo": w.rotulo}
    print(json.dumps(resumen, ensure_ascii=False))
    return 0 if (res.textos > 0 or res.motivo_fin == "stop") else 3


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
