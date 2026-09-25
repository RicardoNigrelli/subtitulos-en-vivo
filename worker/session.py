"""SessionWorker: una sesion de ASR por sala, desacoplada del transporte (worker/transporte.py).

- Emite el `seq` (monotonico por sesion; heartbeat = null). Historial canonico: el Emisor.
- Todo queda en el casete: server crudo, client (turnos y ventanas), emit (mensajes del contrato).
- Solape y gap salen en rafaga SIN dormir; la unica espera del emisor de audio es la del gap
  (>= 0,7 s de reloj de pared entre activity_end y activity_start), y solo si hace falta.

B3 - REABRIR CON SOLAPE (watchdog + rotacion en UN mecanismo; ESTADO.md, Decisiones).
Disparadores (meta.reason del evento `rotation`):
  (i)   "cierre":     el server cerro el websocket (1000/1006/1011/cualquiera) -> reabrir YA.
  (ii)  "atasco":     dos senales, evaluadas con TIMER EXTERNO (cada 0,5 s) y en cada chunk enviado:
        - atraso: audio_enviado_a_la_conexion - audioOffset_del_server > ATASCO_UMBRAL_S sostenido
          durante ATASCO_SOSTENIDO_S (meta.detalle="atraso").
        - mudo:   ventanas con voz enviadas y NINGUN texto del server (ni final ni parcial) durante
          MUDO_S (meta.detalle="mudo"; skill gemini-live: sesiones que dejan de emitir sin error).
  (iii) "preventiva": ROTACION_PREVENTIVA_S de audio enviado a la conexion (tope observado ~283 s).
  (iv)  "goaway":     llego GoAway.
Criterio de los defaults (medido sobre casetes reales; reportes/audio-pipeline-b3.md):
  la larga ES (b1-nerdearla-es-intento2-cancelled) tuvo cobertura 1,0 en 0-210 s PERO con un
  episodio de atraso de hasta 25,9 s (offset trabado en 35,7 s entre t~35 y t~56, latencia 19 s) y
  ~20 s sin final. Con UMBRAL 6 / SOSTENIDO 4 (el pedido original) ese episodio dispara (~t 42).
  Defaults: UMBRAL 22 / SOSTENIDO 8. Medido en escala de audio enviado: en el episodio ES de t~37 el
  atraso supera 22 s durante 3,9 s seguidos como maximo (con 20 s son 9,5 s: disparaba). Asi la ES no
  dispara en 0-200 s, la preventiva (240 s de audio enviado) llega primero y el atasco de la muerte
  (offset trabado en 255,4 s) dispara en la conexion nueva antes de los 250 s (test_reabrir.py). Con
  VAD automatico del server el criterio de atraso NO sirve (turnos de 30+ s: dispara solo). Lo que se pierde con esta eleccion: el episodio EN de los
  ~5 s (offset trabado ~19 s, con perdida) NO dispara por atraso; en tiempo real es indistinguible
  del episodio ES de los ~37 s (que se recupero solo). "mudo" (sin ningun texto MUDO_S=10 s con voz
  enviada) no confunde a ninguno de los dos: los parciales siguieron llegando.
25/09: se probo 10 / 4 (ROJO 1 de reportes/verificacion-final2.md) y se VOLVIO a 22 / 8: en replay 10/4
  reabre mas (EN 14 vs 8, ES 11 vs 8), da un falso positivo en el episodio ES de t~37 (44,6 s) y no
  achica el tramo mas largo sin texto; 22/8 tiene evidencia en vivo (ESTADO.md, fila R18: cobertura
  1,0 en 2 x 11 min con rotaciones).
  Numeros (replay, no vivo): reportes/audio-pipeline-umbral.log (python -m worker.umbrales
  --perfiles 10/4,22/8 --mudo-es 60 [--vf2 fixtures/casetes/vf2-smoke-final2-en.jsonl --solo-vf2]).
Mecanica: la conexion nueva se abre ANTES de soltar la vieja, en el borde de ventana (o ya, si la
vieja murio). Mientras conecta, el audio queda en la fuente (tiempo real: sale en rafaga al
conectar; nada se pierde). En atasco/cierre se REENVIAN a la nueva las ventanas que la vieja no
cubrio (hasta REENVIO_MAX_S). La vieja recibe activity_end si tenia turno abierto y queda hasta
DRENAJE_VIEJA_S drenando; de ella se aceptan solo textos que empiezan antes del corte.     `seq`
sigue continuo y la session_id publica NO cambia (old_id/new_id son ids internos de conexion).
B4:
- DEDUP de costuras por caracteres (worker/dedup.py): sufijo del ultimo texto emitido vs prefijo del
  nuevo; el original crudo queda en el casete (`dedup.original`). Un duplicado entero no se emite.
- ROJO 4 (verificacion final): en la COSTURA de una rotacion (texto de la vieja drenando, o con
  audio_start en [corte, pos_s + solape)) se compara CONTENIDO compacto (sin mayusculas, puntuacion ni
  espacios) contra los ultimos 5 textos emitidos: si el nuevo ya esta dentro de uno, no se emite; si
  contiene a uno entero, se quita ese tramo y sale solo lo no repetido. De la vieja se descarta todo
  texto con audio_start >= corte (antes: solo si ademas pasaba el limite de audio_end).
- ROTULO: si el transporte es de test (atributo SOURCE_REPLAY, p.ej. worker/transporte_casete.py)
  TODO mensaje sale con replay:true y meta.source=<rotulo>; el session_start lleva "[TEST] " en el
  titulo (contracts/README.md: replay=true si viene de un casete).
- rotation.meta.audio_lost_s / audio_lost_rango: audio de ventanas que la conexion vieja no cubrio y
  que NO se reenvia (tope REENVIO_MAX_S): ESTIMACION del hueco que deja la reapertura (cota superior:
  si la vieja drena, se recupera).
- session_start.meta.translations_langs: idiomas destino del traductor desde el arranque.
B7 (intento corto de B5, fixtures/casetes/b5-qa-b5-en-cierre1006.jsonl; test_cierre_red.py):
- La reapertura solo se ejecuta en el lazo de envio. Si un envio a la conexion falla (1006 que aparece
  como ConnectionClosedError en send) o se TRABA mas de ENVIO_TIMEOUT_S (5 s), la conexion se da por
  muerta y se reabre con meta.reason "cierre" (meta.por="envio" si nadie la cerro): antes la excepcion
  salia de _enviar y la sala terminaba con server_cerro.
- Un pedido pendiente (atasco/preventiva/goaway) que no llego a ejecutarse cuando la conexion muere
  pasa a "cierre" (meta.pedido_previo guarda el motivo anterior).
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional

from worker.casete import Grabador, kind_server
from worker.contrato import SIN_SEQ, mensaje
from worker.dedup import contenido_repetido, dedup_costura, tiradas_repetidas
from worker.emisor import Emisor
from worker.ingesta import CHUNK_S, Chunk, EventoFuente
from worker.mapeo import InfoVentana, Mapeador, segundos as _segundos_mapeo
from worker.transporte import Transporte
from worker.ventanas import GAP_S, SOLAPE_S, Accion, Cortador, Ventana


PARADA_DRENAJE_S = 8.0   # parada en sala: espera corta del ultimo texto antes de cerrar


def _env_f(nombre: str, defecto: float) -> float:
    try:
        return float(os.environ.get(nombre, defecto))
    except ValueError:
        return defecto


@dataclass
class ConfigReabrir:
    """Todos configurables por variable de entorno (criterio de los defaults: docstring del modulo)."""
    atasco_umbral_s: float = 22.0
    atasco_sostenido_s: float = 8.0
    mudo_s: float = 10.0
    preventiva_s: float = 240.0
    drenaje_vieja_s: float = 20.0
    reenvio_max_s: float = 15.0
    intentos_conectar: int = 3
    tick_s: float = 0.5
    envio_timeout_s: float = 5.0      # B7: un envio trabado mas que esto = conexion muerta
    arranque_s: float = 0.0           # compuerta: reabrir si tras N s de audio NINGUN final (0 = apagado)

    @classmethod
    def desde_env(cls) -> "ConfigReabrir":
        return cls(atasco_umbral_s=_env_f("ATASCO_UMBRAL_S", 22.0),
                   atasco_sostenido_s=_env_f("ATASCO_SOSTENIDO_S", 8.0),
                   arranque_s=_env_f("ATASCO_ARRANQUE_S", 0.0),
                   mudo_s=_env_f("MUDO_S", 10.0),
                   preventiva_s=_env_f("ROTACION_PREVENTIVA_S", 240.0),
                   drenaje_vieja_s=_env_f("DRENAJE_VIEJA_S", 20.0),
                   reenvio_max_s=_env_f("REENVIO_MAX_S", 15.0),
                   envio_timeout_s=_env_f("ENVIO_TIMEOUT_S", 5.0))


class _SinReabrir(ConnectionError):
    """No se pudo abrir la conexion nueva y la vieja esta muerta: la sala termina."""


class _EnvioTrabado(TimeoutError):
    """Un envio a la conexion no volvio en ENVIO_TIMEOUT_S."""


@dataclass
class Resultado:
    session_id: str
    chunks_enviados: int = 0
    ventanas: int = 0
    textos: int = 0
    mensajes_server: int = 0
    goaway: Optional[dict] = None
    cierre: Optional[dict] = None
    motivo_fin: str = ""
    seq_final: int = 0
    t_inicio: float = 0.0
    t_fin: float = 0.0
    errores: list = field(default_factory=list)
    rotaciones: list = field(default_factory=list)
    descartados_vieja: int = 0
    caidas_fuente: int = 0           # sala: la fuente mic/url se cayo y se reabrio (misma sesion)
    reenviados_s: float = 0.0
    dedup_recortados: int = 0         # textos a los que se les quito el prefijo repetido
    dedup_descartados: int = 0        # textos que eran duplicado entero (no se emitieron)

    @property
    def segundos_enviados(self) -> float:
        return round(self.chunks_enviados * CHUNK_S, 1)


def _segundos(offset) -> Optional[float]:
    """'2.200s' -> 2.2 (formato Duration de protobuf en JSON)."""
    if offset is None:
        return None
    try:
        return float(str(offset).rstrip("s"))
    except ValueError:
        return None


def extraer_texto(msg: dict) -> Optional[str]:
    sc = msg.get("serverContent") or {}
    tx = (sc.get("inputTranscription") or {}).get("text")
    return tx if tx else None


class _Conexion:
    """Una sesion de Gemini (o de casete) dentro de la sala. La sala (session_id) no cambia."""

    def __init__(self, n: int, tr: Transporte, corte_s: float):
        self.n = n
        self.id = f"c{n}"
        self.tr = tr
        self.corte_s = corte_s           # posicion de audio de la fuente donde arranca
        self.mapa = Mapeador()
        self.chunks = 0                  # audio enviado A ESTA conexion (incluye solape/reenvio)
        self.offset_s = 0.0              # ultimo audioOffset del server
        self.cerrada = asyncio.Event()
        self.cierre: Optional[dict] = None
        self.rx: Optional[asyncio.Task] = None
        self.t_ultimo_end: Optional[float] = None
        self.turno_abierto = False
        self.limite_audio_end: Optional[float] = None   # vieja: solo textos con audio_end <= esto
        self.corte_nueva: Optional[float] = None        # vieja: desde aca cubre la conexion nueva
        self.atraso_desde: Optional[float] = None
        self.voz_sin_texto_desde: Optional[float] = None
        self.max_atraso = 0.0
        self.gracia_s = 0.0              # audio reenviado en rafaga al abrir: no cuenta como atraso
        self.finales = 0

    @property
    def acc_s(self) -> float:
        return round(self.chunks * CHUNK_S, 3)

    @property
    def atraso_s(self) -> float:
        # la rafaga de reenvio (gracia) no es atraso mientras el server no la termino de procesar
        g = self.gracia_s if self.offset_s < self.gracia_s else 0.0
        return round(max(0.0, self.acc_s - self.offset_s - g), 3)


def _union(intervalos) -> float:
    tot, fin = 0.0, None
    for a, b in sorted(intervalos):
        if fin is None or a > fin:
            tot += b - a
            fin = b
        elif b > fin:
            tot += b - fin
            fin = b
    return tot


def _audio_perdido(vieja: "_Conexion", reenviar: list, motivo: str) -> dict:
    """Estimacion del audio que queda SIN TEXTO por la reapertura: ventanas pendientes de la vieja
    (sin texto del server) que no entran en el reenvio. En preventiva/goaway la vieja sigue sana y
    drena: 0. Es cota superior (si la vieja drena dentro de DRENAJE_VIEJA_S, se recupera)."""
    if motivo not in ("atasco", "cierre"):
        return {"audio_lost_s": 0.0, "audio_lost_rango": None}
    reenv = {h["v"].idx for h in reenviar}
    perdidas = [i for i in vieja.mapa.pend if i.idx not in reenv]
    if not perdidas:
        return {"audio_lost_s": 0.0, "audio_lost_rango": None}
    iv = [(i.audio_start, i.audio_end) for i in perdidas]
    return {"audio_lost_s": round(_union(iv), 2),
            "audio_lost_rango": [round(min(a for a, _ in iv), 2), round(max(b for _, b in iv), 2)]}


class SessionWorker:
    def __init__(self, session_id: str, lang: str, transporte: Transporte,
                 fuente: AsyncIterator[Chunk], grabador: Optional[Grabador] = None,
                 emisor: Optional[Emisor] = None, titulo: str = "", source: Optional[dict] = None,
                 cortador: Optional[Cortador] = None, tope_envio_s: Optional[float] = None,
                 heartbeat_s: float = 5.0, espera_final_s: float = 30.0, gap_s: float = GAP_S,
                 traductor=None, seq_inicial: int = 0,
                 reloj: Callable[[], float] = time.time,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 log: Callable[[str], None] = print,
                 fabrica: Optional[Callable[[float], Transporte]] = None,
                 reabrir: Optional[ConfigReabrir] = None,
                 turnos_manuales: bool = True,
                 rotulo: Optional[str] = None,
                 dedup: bool = True):
        self.sid = session_id
        self.lang = lang
        self.tr = transporte
        self.fuente = fuente
        self.rec = grabador
        self.emisor = emisor
        self.titulo = titulo
        self.source = source or {}
        self.cortador = cortador or Cortador()
        self.tope_chunks = None if tope_envio_s is None else int(tope_envio_s / CHUNK_S)
        self.heartbeat_s = heartbeat_s
        self.espera_final_s = espera_final_s
        self.gap_s = gap_s
        self.reloj = reloj
        self.dormir = dormir
        self.log = log
        self.seq = int(seq_inicial or 0)     # sigue desde auth_ok.last_seq (el hub no rebobina)
        self.traductor = traductor           # worker/traductor.py (R19), o None
        self.fabrica = fabrica               # None => sin reabrir (una sola conexion, como B1/B2)
        self.cfg = reabrir or ConfigReabrir.desde_env()
        self.turnos_manuales = turnos_manuales   # False => VAD automatico del server (A/B B3)
        # rotulo de test/replay: explicito o el que declara el transporte (no se puede olvidar)
        self.rotulo = rotulo or getattr(transporte, "SOURCE_REPLAY", None)
        self.dedup = dedup
        self._ultimo_texto: Optional[str] = None
        self._ultimo_a1: Optional[float] = None
        self._recientes: deque = deque(maxlen=5)   # (texto, audio_start, audio_end) emitidos: costura
        self.n_parciales = 0
        self.drenaje: dict = {}
        self.res = Resultado(session_id)
        self.con: Optional[_Conexion] = None
        self.viejas: list[_Conexion] = []
        self._retiros: list[asyncio.Task] = []
        self._n_con = 0
        self._pedido: Optional[dict] = None
        self._atascos_seguidos = 0                   # backoff: atascos sin un final de la nueva
        self._historial: deque = deque(maxlen=16)   # ventanas cerradas con sus chunks (reenvio)
        self._en_curso: Optional[dict] = None        # ventana abierta: {"v": Ventana, "chunks": [...]}
        self.abierta: Optional[Ventana] = None
        self.ultima: Optional[Ventana] = None
        self.pos_s = 0.0                              # posicion de audio de la fuente (ultimo chunk)
        self._fin_sala = asyncio.Event()
        self._parada = asyncio.Event()                # sala: Ctrl+C / SIGTERM -> parar('stop')
        self._envio_terminado = asyncio.Event()
        self._vivo = False

    @property
    def mapa(self) -> Mapeador:
        return self.con.mapa if self.con else Mapeador()

    # ---- emision ----------------------------------------------------------
    def emitir(self, tipo: str, **campos) -> dict:
        if tipo not in SIN_SEQ:
            self.seq += 1
            seq = self.seq
        else:
            seq = None
        extra_casete = campos.pop("_casete", {})
        if self.rotulo:
            # transporte de test: TODO mensaje sale rotulado (no es ASR en vivo)
            meta = dict(campos.get("meta") or {})
            if "source" in meta and meta["source"] != self.rotulo:
                meta["source_original"] = meta["source"]
            meta["source"] = self.rotulo
            campos["meta"] = meta
            campos["replay"] = True
        msg = mensaje(tipo, self.sid, seq, self.lang, t_emit=self.reloj(), **campos)
        if self.rec:
            self.rec.emit(msg, t=msg["t_emit"], **extra_casete)
        if self.emisor:
            self.emisor.publicar(msg)
        self.res.seq_final = self.seq
        return msg

    # ---- conexiones ---------------------------------------------------------
    async def _abrir(self, tr: Transporte, corte_s: float) -> _Conexion:
        self._n_con += 1
        con = _Conexion(self._n_con, tr, corte_s)
        if self.rec:
            self.rec.client("connect", {"session_id": self.sid, "lang": self.lang, "conexion": con.id,
                                        "corte_s": corte_s})
        setup = await tr.conectar()
        if self.rec:
            self.rec.server(kind_server(setup), setup, fuente="sdk", conexion=con.id)
            cfg = getattr(tr, "config_enviada", None)
            if callable(cfg):
                self.rec.client("config", cfg(), conexion=con.id)
        con.rx = asyncio.create_task(self._recibir(con))
        return con

    def parar(self, motivo: str = "stop") -> None:
        """Parada limpia (operacion en sala: Ctrl+C / SIGTERM). Se deja de leer la fuente, se cierra la
        ventana abierta, se drena un rato corto (PARADA_DRENAJE_S), se cierra la conexion en orden y
        sale session_end con meta.reason = motivo. Idempotente."""
        if self._parada.is_set():
            return
        self._parada.set()
        self.res.motivo_fin = motivo
        self.log(f"[{self.sid}] parada pedida ({motivo}): cierre ordenado")
        if self.rec:
            self.rec.client("parada", {"motivo": motivo, "pos_s": round(self.pos_s, 3)})
        parar_fuente = getattr(self.fuente, "parar", None)
        if parar_fuente:
            parar_fuente()

    async def _evento_fuente(self, ev: EventoFuente) -> None:
        """La fuente mic/url se cayo o volvio (worker/ingesta.py: FuenteReabrible). La sesion publica
        sigue (misma session_id, seq continuo); la conexion con el modelo se reabre solo si hace falta
        (por su cuenta: cierre/atasco/goaway)."""
        if self.rec:
            self.rec.client("fuente_" + ev.tipo, ev.detalle)
        if ev.tipo != "caida":
            self.log(f"[{self.sid}] fuente reabierta (audio {ev.detalle.get('audio_s')} s)")
            return
        self.res.caidas_fuente += 1
        d = ev.detalle
        self.emitir("error", meta={"code": "fuente",
                                   "message": f"fuente caida ({d.get('motivo')}); reintento en "
                                              f"{d.get('reintento_en_s')} s, la sesion sigue"})
        # la ventana abierta se cierra ya: su texto llega aunque la fuente tarde en volver
        if not self.con.cerrada.is_set():
            for a in self.cortador.cerrar():
                await self._ejecutar(a)

    def pedir_reapertura(self, motivo: str, **detalle) -> None:
        """Lo llaman el timer externo, el receptor (cierre/goaway) y el chequeo por chunk."""
        if self.fabrica is None or self._fin_sala.is_set():
            return
        if self._pedido is not None:
            if motivo == "cierre" and self._pedido.get("reason") != "cierre":
                # la conexion murio antes de que el pedido anterior se ejecutara: el motivo real es
                # el cierre (B5: pedido atasco/mudo con el envio trabado, despues 1006)
                previo = self._pedido.get("reason")
                self._pedido = {**self._pedido, "reason": "cierre", "pedido_previo": previo, **detalle}
                if self.rec:
                    self.rec.client("reabrir_pedido", {**self._pedido, "actualizado": True})
            return
        con = self.con
        self._pedido = {"reason": motivo, "t": self.reloj(), "pos_s": round(self.pos_s, 2),
                        "old_id": con.id if con else None,
                        "audio_s": con.acc_s if con else None,
                        "atraso_s": con.atraso_s if con else None, **detalle}
        if self.rec:
            self.rec.client("reabrir_pedido", self._pedido)
        self.log(f"[{self.sid}] reabrir pedido: {self._pedido}")

    def _chequear(self) -> None:
        con = self.con
        if con is None or self.fabrica is None or self._pedido is not None:
            return
        ahora = self.reloj()
        if con.cerrada.is_set():
            self.pedir_reapertura("cierre", code=(con.cierre or {}).get("code"))
            return
        if con.acc_s >= self.cfg.preventiva_s:
            self.pedir_reapertura("preventiva")
            return
        # ATASCO_ARRANQUE_S (apagado por defecto): conexion que todavia no dio NINGUN final despues de N s
        # de audio propio (sin contar el reenvio en rafaga) con voz pendiente. Caso gate-es: el server no
        # cerro el primer turno (su ACTIVITY_END llego a +39,7 s) aunque le mandamos activity_end a +3 s.
        if self.cfg.arranque_s > 0 and con.finales == 0 and con.voz_sin_texto_desde is not None:
            arr = min(120.0, self.cfg.arranque_s * (2 ** self._atascos_seguidos))
            if con.acc_s - con.gracia_s >= arr:
                self.pedir_reapertura("atasco", detalle="arranque", sin_final_s=round(con.acc_s - con.gracia_s, 2))
                return
        at = con.atraso_s
        con.max_atraso = max(con.max_atraso, at)
        # backoff: si la conexion anterior se reabrio por atasco y esta todavia no dio un final,
        # el sostenido se duplica (8, 16, 32 ... hasta 120 s): evita tormentas de reaperturas
        sost = min(120.0, self.cfg.atasco_sostenido_s * (2 ** self._atascos_seguidos))
        if at > self.cfg.atasco_umbral_s:
            if con.atraso_desde is None:
                con.atraso_desde = ahora
            elif ahora - con.atraso_desde >= sost:
                self.pedir_reapertura("atasco", detalle="atraso",
                                      sostenido_s=round(ahora - con.atraso_desde, 2))
                return
        else:
            con.atraso_desde = None
        mudo = min(120.0, self.cfg.mudo_s * (2 ** self._atascos_seguidos))
        if (con.voz_sin_texto_desde is not None
                and ahora - con.voz_sin_texto_desde >= mudo):
            self.pedir_reapertura("atasco", detalle="mudo",
                                  sin_texto_s=round(ahora - con.voz_sin_texto_desde, 2))

    async def _vigia(self) -> None:
        """Timer EXTERNO (no depende de recibir mensajes ni de que el envio avance)."""
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            self._chequear()

    async def _conectar_nueva(self, corte_s: float) -> Optional[_Conexion]:
        ultimo = None
        for intento in range(self.cfg.intentos_conectar):
            try:
                return await self._abrir(self.fabrica(corte_s), corte_s)
            except Exception as e:
                ultimo = f"{type(e).__name__}: {e}"
                self.res.errores.append(f"reconnect: {ultimo}")
                if self.rec:
                    self.rec.client("connect_error", {"error": ultimo, "intento": intento + 1})
                await asyncio.sleep(1.0 * (intento + 1))
        self.emitir("error", meta={"code": "reconnect", "message": ultimo})
        return None

    async def _rotar(self, siguiente: Optional[Ventana]) -> bool:
        """Cambia de conexion en un borde de ventana (o con turno abierto si la vieja murio)."""
        p = self._pedido
        vieja = self.con
        motivo = p["reason"]
        reenviar: list[dict] = []
        if motivo in ("atasco", "cierre"):
            pend = {i.idx for i in vieja.mapa.pend}
            desde = self.pos_s - self.cfg.reenvio_max_s
            reenviar = [h for h in self._historial
                        if h["v"].idx in pend and h["v"].audio_start >= desde]
        abierta = self._en_curso
        if reenviar:
            corte = reenviar[0]["v"].audio_start
        elif abierta is not None:
            corte = abierta["v"].audio_start
        elif siguiente is not None:
            corte = siguiente.audio_start
        else:
            corte = self.pos_s
        # el filtro de la vieja rige desde YA (no despues de conectar: en vivo B3 la vieja emitio
        # un texto durante los 1,95 s de connect que la regla tenia que filtrar)
        vieja.limite_audio_end = round(corte + SOLAPE_S + 0.05, 3)
        vieja.corte_nueva = corte
        t0 = time.monotonic()
        nueva = await self._conectar_nueva(corte)
        conectar_s = round(time.monotonic() - t0, 2)
        if nueva is None:
            vieja.limite_audio_end = vieja.corte_nueva = None
            self._pedido = None
            return False
        # la vieja: cerrar su turno si quedo abierto y dejarla drenar
        if vieja.turno_abierto and not vieja.cerrada.is_set():
            try:
                if self.turnos_manuales:
                    await self._tx(vieja.tr.activity_end())
            except Exception:
                pass
            vieja.turno_abierto = False
        self.viejas.append(vieja)
        self._retiros.append(asyncio.create_task(self._retirar(vieja)))
        self.con = nueva
        info = {**p, "new_id": nueva.id, "corte_s": round(corte, 2), "conectar_s": conectar_s,
                "reenvio_ventanas": [h["v"].idx for h in reenviar],
                "reenvio_s": round(sum(len(h["chunks"]) for h in reenviar) * CHUNK_S, 1)}
        info.update(_audio_perdido(vieja, reenviar, motivo))
        info.pop("t", None)
        nueva.gracia_s = info["reenvio_s"]
        self._atascos_seguidos = self._atascos_seguidos + 1 if motivo == "atasco" else 0
        info["atascos_seguidos"] = self._atascos_seguidos
        self.res.rotaciones.append(info)
        self.res.reenviados_s += info["reenvio_s"]
        self.emitir("rotation", meta=info)
        self._pedido = None
        # reenvio en rafaga de lo que la vieja no cubrio (sin dormir salvo el gap entre turnos)
        for h in reenviar:
            await self._turno_start(h["v"])
            for data in h["chunks"]:
                await self._audio(data)
            await self._turno_end(h["v"], reenvio=True)
        if abierta is not None:
            # la vieja murio con turno abierto: se reabre el turno en la nueva con lo que ya habia
            await self._turno_start(abierta["v"])
            for data in abierta["chunks"]:
                await self._audio(data)
        return True

    async def _retirar(self, vieja: _Conexion) -> None:
        t0 = time.monotonic()
        while (vieja.mapa.pend and not vieja.cerrada.is_set()
               and time.monotonic() - t0 < self.cfg.drenaje_vieja_s):
            await asyncio.sleep(0.1)
        if self.rec:
            self.rec.client("retiro", {"conexion": vieja.id, "pendientes": len(vieja.mapa.pend),
                                       "espero_s": round(time.monotonic() - t0, 2)})
        if not vieja.cerrada.is_set() or (vieja.cierre or {}).get("by") == "envio":
            try:
                await asyncio.wait_for(vieja.tr.cerrar(), 5)
            except (asyncio.TimeoutError, Exception):
                pass
        if vieja.rx is not None:
            try:
                await asyncio.wait_for(vieja.rx, 5)
            except (asyncio.TimeoutError, Exception):
                vieja.rx.cancel()
        self._emitir_asignaciones(vieja.mapa.vaciar(), vieja)

    # ---- envio de audio ---------------------------------------------------
    async def _tx(self, aw) -> None:
        """Todo envio a una conexion: con reapertura habilitada, tope ENVIO_TIMEOUT_S (B7)."""
        t = self.cfg.envio_timeout_s
        if self.fabrica is None or not t or t <= 0:
            await aw
            return
        try:
            await asyncio.wait_for(aw, t)
        except asyncio.TimeoutError:
            raise _EnvioTrabado(f"envio trabado mas de {t} s") from None

    def _caida(self, con: _Conexion, e: BaseException) -> None:
        """Un envio fallo o se trabo: la conexion se da por muerta y se pide reabrir por cierre."""
        err = f"{type(e).__name__}: {e}"
        self.res.errores.append(f"envio: {err}")
        ya = con.cerrada.is_set()
        if self.rec:
            self.rec.client("send_error", {"error": err, "server_ya_cerro": ya, "conexion": con.id,
                                           "reabre": True})
        self.log(f"[{self.sid}] envio fallo en {con.id} ({err}): se reabre")
        if not ya:
            con.cierre = {"code": None, "reason": f"envio: {err}", "by": "envio"}
            con.cerrada.set()
        self.pedir_reapertura("cierre", code=(con.cierre or {}).get("code"),
                              **({} if ya else {"por": "envio"}))

    async def _turno_start(self, v: Ventana) -> None:
        con = self.con
        if self.turnos_manuales:
            if con.t_ultimo_end is not None:
                falta = con.t_ultimo_end + self.gap_s - self.reloj()
                if falta > 0:
                    await self.dormir(falta)   # la unica espera: el gap, no el audio
            await self._tx(con.tr.activity_start())
        con.turno_abierto = True
        if self.rec:
            self.rec.client("activity_start", {"ventana": v.idx, "audio_start": v.audio_start},
                            conexion=con.id)

    async def _audio(self, data: bytes) -> None:
        con = self.con
        await self._tx(con.tr.enviar_audio(data))
        con.chunks += 1
        self.res.chunks_enviados += 1

    async def _turno_end(self, v: Ventana, reenvio: bool = False) -> None:
        con = self.con
        if self.turnos_manuales:
            await self._tx(con.tr.activity_end())
        con.turno_abierto = False
        con.t_ultimo_end = self.reloj()
        con.mapa.ventana_cerrada(InfoVentana(v.idx, v.audio_start, v.audio_end, v.t_captured,
                                             con.acc_s))
        if v.has_voice and con.voz_sin_texto_desde is None:
            con.voz_sin_texto_desde = con.t_ultimo_end
        if self.rec:
            r = v.resumen()
            if reenvio:
                r["reenvio"] = True
            self.rec.client("ventana", r, t=con.t_ultimo_end, conexion=con.id)
            self.rec.client("activity_end", {"ventana": v.idx, "audio_end": v.audio_end},
                            t=con.t_ultimo_end, conexion=con.id)

    async def _ejecutar(self, a: Accion) -> None:
        # B7: si el envio falla o se traba, la conexion se da por muerta, se reabre (cierre) y la
        # MISMA accion se repite en la nueva (el chunk que fallo no se conto ni se guardo)
        for intento in range(self.cfg.intentos_conectar + 1):
            try:
                await self._ejecutar1(a)
                return
            except _SinReabrir:
                raise
            except Exception as e:
                if (self.fabrica is None or self._fin_sala.is_set()
                        or intento >= self.cfg.intentos_conectar):
                    raise
                self._caida(self.con, e)

    async def _ejecutar1(self, a: Accion) -> None:
        if self._pedido is not None and self.fabrica is not None:
            vieja_muerta = self.con.cerrada.is_set()
            if a.kind == "start" or (vieja_muerta and a.kind in ("audio", "end")):
                if not await self._rotar(a.ventana if a.kind == "start" else None):
                    if self.con.cerrada.is_set():
                        raise _SinReabrir("no se pudo reabrir y la conexion murio")
        if a.kind == "start":
            await self._turno_start(a.ventana)
            self.abierta = a.ventana
            self._en_curso = {"v": a.ventana, "chunks": []}
        elif a.kind == "audio":
            # VAD automatico: el audio va UNA vez (sin la rafaga de solape, que duplicaria voz)
            if self.turnos_manuales or not getattr(a, "burst", False):
                await self._audio(a.chunk.data)
                if self._en_curso is not None:
                    self._en_curso["chunks"].append(a.chunk.data)
            self.pos_s = max(self.pos_s, a.chunk.audio_end)
            self._chequear()
        elif a.kind == "end":
            v = a.ventana
            await self._turno_end(v)
            if self._en_curso is not None:
                self._historial.append(self._en_curso)
            self._en_curso = None
            self.ultima = v
            self.abierta = None
            self.res.ventanas += 1

    async def _enviar(self) -> None:
        try:
            async for c in self.fuente:
                if self._parada.is_set():
                    break
                if isinstance(c, EventoFuente):
                    await self._evento_fuente(c)
                    continue
                if self._fin_sala.is_set():
                    self.res.motivo_fin = self.res.motivo_fin or "server_cerro"
                    break
                if self.fabrica is None and self.con.cerrada.is_set():
                    self.res.motivo_fin = self.res.motivo_fin or "server_cerro"
                    break
                if self.tope_chunks is not None and self.res.chunks_enviados >= self.tope_chunks:
                    self.res.motivo_fin = "tope_cuota"
                    self.log(f"[{self.sid}] tope de envio alcanzado "
                             f"({self.res.segundos_enviados} s): se corta")
                    break
                for a in self.cortador.push(c):
                    await self._ejecutar(a)
            else:
                self.res.motivo_fin = self.res.motivo_fin or "fin_fuente"
            if not self.con.cerrada.is_set():
                for a in self.cortador.cerrar():
                    await self._ejecutar(a)
        except Exception as e:
            self.res.errores.append(f"envio: {type(e).__name__}: {e}")
            if self.rec:
                self.rec.client("send_error", {"error": f"{type(e).__name__}: {e}",
                                               "server_ya_cerro": self.con.cerrada.is_set()})
            if not self.con.cerrada.is_set():
                self.log(f"[{self.sid}] error enviando: {type(e).__name__}: {e}")
            self.res.motivo_fin = self.res.motivo_fin or "error_envio"
        finally:
            cerrar_fuente = getattr(self.fuente, "aclose", None)
            if cerrar_fuente:
                try:
                    await cerrar_fuente()
                except Exception:
                    pass
            self._envio_terminado.set()

    # ---- recepcion ------------------------------------------------------------
    def _en_costura(self, a, con: Optional[_Conexion]) -> bool:
        """Texto de la costura de una rotacion: de una conexion vieja drenando, o con audio_start en
        [corte, pos_s + solape) de alguna rotacion (el tramo que las dos conexiones pudieron transcribir).
        Fuera de la costura no se compara contenido (una frase legitima repetida no se pierde)."""
        if con is None:
            return False
        if con.corte_nueva is not None:
            return True
        if a.audio_start is None:
            return False
        for r in self.res.rotaciones[-3:]:
            c = r.get("corte_s")
            if c is not None and c - 0.05 <= a.audio_start < float(r.get("pos_s") or c) + SOLAPE_S + 0.05:
                return True
        return False

    def _emitir_asignaciones(self, asignaciones, con: Optional[_Conexion] = None) -> None:
        for a in asignaciones:
            lim = con.limite_audio_end if con is not None else None
            corte = con.corte_nueva if con is not None else None
            # de la vieja se descarta todo texto que EMPIEZA en/despues del corte (lo cubre la nueva);
            # uno FUSIONADO que empieza antes del corte trae audio que la nueva no recibio (reenvio con
            # tope REENVIO_MAX_S): se acepta y se marca; lo que repita se quita abajo (costura por
            # contenido, ROJO 4) y en la punta (sufijo/prefijo, B4).
            if ((corte is not None and a.audio_start is not None and a.audio_start >= corte - 0.05)
                    or (lim is not None and a.audio_end is not None and a.audio_end > lim
                        and a.audio_start is None)):
                self.res.descartados_vieja += 1
                if self.rec:
                    self.rec.client("descartado_vieja", {"conexion": con.id, "text": a.text,
                                                         "audio_start": a.audio_start,
                                                         "audio_end": a.audio_end, "limite": lim})
                continue
            if a.audio_start is None or a.audio_end is None:
                # texto sin ventana asignable (no deberia pasar en vivo): se ancla a la posicion actual
                # para que el mensaje cumpla el contrato (audio_* numericos)
                a.audio_start = self.abierta.audio_start if self.abierta else round(self.pos_s, 3)
                a.audio_end = round(max(self.pos_s, a.audio_start), 3)
                a.t_captured = a.t_captured if a.t_captured is not None else self.reloj()
            # el original sale YA con translations {}; la traduccion llega despues como `translation`
            extra = {"ventanas": a.ventanas, "conexion": con.id if con is not None else None}
            if lim is not None and a.audio_end is not None and a.audio_end > lim:
                extra["cruza_corte"] = {"corte_s": corte, "limite": lim}
            partes = [(a.text, a.audio_start, a.audio_end, True)]
            if self.dedup and self._recientes and self._en_costura(a, con):
                rec = list(self._recientes)
                tipo, pedazos = contenido_repetido(a.text, [r[0] for r in rec])
                if tipo is None:
                    # compuerta (gate-es): tirada comun de >= 6 palabras EN EL MEDIO de dos textos distintos
                    tir = tiradas_repetidas(a.text, [r[0] for r in rec])
                    if tir is not None:
                        tipo, pedazos = ("tirada", tir) if tir else ("contenido", [])
                if tipo == "contenido":
                    self.res.dedup_descartados += 1
                    if self.rec:
                        self.rec.client("dedup_descartado", {"conexion": extra["conexion"], "text": a.text,
                                                             "ventanas": a.ventanas, "motivo": "contenido"})
                    continue
                if tipo in ("contiene", "tirada"):
                    # se quitan los tramos que repiten textos ya emitidos; lo que queda (audio que la otra
                    # conexion no transcribio) sale ubicado entre los tramos repetidos
                    partes = []
                    for t, i_ant, i_sig in pedazos:
                        a0 = rec[i_ant][2] if i_ant is not None else a.audio_start
                        a1 = rec[i_sig][1] if i_sig is not None else a.audio_end
                        a0 = round(min(max(a0, a.audio_start), a.audio_end), 3)
                        a1 = round(min(max(a1, a0), a.audio_end), 3)
                        partes.append((t, a0, a1, i_sig is None))
                    # un pedazo ANTERIOR al tramo repetido se cose con su vecino en el audio, no con el
                    # ultimo emitido (casete EN: "... of a as an" | "as an abstraction ...")
                    if partes and pedazos[0][1] is None:
                        t, a0, a1, s0 = partes[0]
                        vecino = [r for r in rec if r[2] <= a0 + SOLAPE_S + 0.05 and r[1] < a0]
                        if vecino:
                            t2, _q = dedup_costura(max(vecino, key=lambda r: r[2])[0], t)
                            if _q and t2.strip():
                                partes[0] = (t2, a0, a1, s0)
                    extra = {**extra, "costura": {"original": a.text, "pedazos": len(partes), "tipo": tipo}}
                    if not partes:
                        self.res.dedup_descartados += 1
                        continue
            for texto, a0, a1, sigue in partes:
                self._emitir_texto(texto, a0, a1, a, extra, sigue)

    def _emitir_texto(self, texto: str, a0, a1, a, extra: dict, sigue: bool) -> None:
        """`sigue`: el pedazo es la punta del texto (lo ultimo que se oyo): de el sigue el sufijo/prefijo.
        Un pedazo anterior a un tramo repetido (audio viejo, ya pasado) no lo pisa."""
        crudo = texto
        if self.dedup:
            texto, quitados = dedup_costura(self._ultimo_texto, crudo)
            if quitados:
                extra = {**extra, "dedup": {"original": crudo, "quitados": quitados}}
                if not texto.strip():
                    # duplicado entero (costura de solape o reenvio al reabrir): no se emite
                    self.res.dedup_descartados += 1
                    if self.rec:
                        self.rec.client("dedup_descartado", {"conexion": extra["conexion"],
                                                             "text": crudo, "ventanas": a.ventanas,
                                                             "previo": self._ultimo_texto})
                    return
                self.res.dedup_recortados += 1
        self.res.textos += 1
        if sigue and (self._ultimo_a1 is None or a1 >= self._ultimo_a1 - 0.05):
            # la punta del audio: un texto de la vieja que llega tarde con audio ANTERIOR al ultimo
            # emitido no pisa al vecino del sufijo/prefijo del texto que sigue
            self._ultimo_texto, self._ultimo_a1 = texto, a1
        self._recientes.append((texto, a0, a1))
        msg = self.emitir("text", text=texto, audio_start=a0, audio_end=a1,
                          t_captured=a.t_captured, _casete=extra)
        if self.traductor is not None:
            self.traductor.agregar(msg["seq"], texto, msg["t_emit"])

    def _on_traduccion(self, res) -> None:
        from worker.traductor import meta_traduccion
        self.emitir("translation", items=res.items,
                    meta=meta_traduccion(res, self.traductor.a, {"vivo": True, "intentos": res.intentos}))

    def _emitir_parcial(self, texto: str, con: _Conexion) -> None:
        # turno en curso = ventana mas vieja sin ACTIVITY_END del server; si no hay, la abierta
        if con.mapa.pend:
            a0 = con.mapa.pend[0].audio_start
        else:
            a0 = self.abierta.audio_start if self.abierta else None
        tc = self.ultima.t_captured if self.ultima is not None else None
        self.n_parciales += 1
        self.emitir("partial", text=texto, audio_start=a0, t_captured=tc)

    async def _vigilar_textos(self) -> None:
        # fallback: texto sin ACTIVITY_END en ESPERA_MAX_S -> se emite igual
        while True:
            await asyncio.sleep(0.1)
            for con in [self.con] + self.viejas:
                if con is not None:
                    self._emitir_asignaciones(con.mapa.vencidos(self.reloj()), con)

    async def _recibir(self, con: _Conexion) -> None:
        async for m in con.tr.recibir():
            t = self.reloj()
            if "_close" in m:
                con.cierre = m["_close"]
                self.res.cierre = m["_close"]
                if self.rec:
                    self.rec.server("close", m["_close"], t=t, conexion=con.id)
                con.cerrada.set()
                if con is self.con:
                    if self.fabrica is None:
                        self._fin_sala.set()
                    elif m["_close"].get("by") != "client":     # el cierre propio no reabre
                        self.pedir_reapertura("cierre", code=m["_close"].get("code"))
                break
            self.res.mensajes_server += 1
            if self.rec:
                self.rec.server(kind_server(m), m, t=t, conexion=con.id)
            if "goAway" in m:
                self.res.goaway = {"t": t, "conexion": con.id, **(m.get("goAway") or {})}
                if self.rec:
                    self.rec.client("goaway_seen", {"goAway": m.get("goAway"), "conexion": con.id,
                                                    "audio_enviado_s": con.acc_s}, t=t)
                self.log(f"[{self.sid}] GoAway recibido: {m.get('goAway')}")
                if con is self.con:
                    self.pedir_reapertura("goaway", time_left=(m.get("goAway") or {}).get("timeLeft"))
            tx = extraer_texto(m)
            itx = ((m.get("serverContent") or {}).get("interimInputTranscription") or {}).get("text")
            if tx or itx:
                con.voz_sin_texto_desde = None
            if tx:
                con.mapa.texto(t, tx)
                con.finales += 1
                if con is self.con:
                    self._atascos_seguidos = 0
            if itx and con is self.con:
                self._emitir_parcial(itx, con)
            va = m.get("voiceActivity") or {}
            off = _segundos_mapeo(va.get("audioOffset"))
            if off is not None:
                con.offset_s = max(con.offset_s, off)
            # ver worker/mapeo.py: el texto se asigna al ACTIVITY_END que le sigue (mismo ms)
            if va.get("type") == "ACTIVITY_END":
                self._emitir_asignaciones(con.mapa.activity_end(off), con)

    async def _latido(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            con = self.con
            self.emitir("heartbeat", meta={"alive": self._vivo,
                                           "audio_seconds_sent": self.res.segundos_enviados,
                                           "conexion": con.id if con else None,
                                           "atraso_s": con.atraso_s if con else None,
                                           "rotaciones": len(self.res.rotaciones)})

    # ---- ciclo completo ---------------------------------------------------------
    async def correr(self) -> Resultado:
        self.res.t_inicio = self.reloj()
        try:
            self.con = await self._abrir(self.tr, 0.0)
        except Exception as e:
            self.res.errores.append(f"connect: {type(e).__name__}: {e}")
            if self.rec:
                self.rec.client("connect_error", {"error": f"{type(e).__name__}: {e}"})
            self.emitir("error", meta={"code": "connect", "message": f"{type(e).__name__}: {e}"})
            self.res.motivo_fin = "error_connect"
            self.res.t_fin = self.reloj()
            return self.res
        self._vivo = True
        titulo = self.titulo
        if self.rotulo and not str(titulo or "").startswith("[TEST] "):
            titulo = f"[TEST] {titulo or self.sid}"
        self.emitir("session_start", meta={
            "title": titulo, "source": self.source,
            # el indice ofrece el idioma destino desde el arranque (no recien con la 1a traduccion)
            "translations_langs": [self.traductor.a] if self.traductor is not None else []})
        if self.traductor is not None:
            self.traductor.on_resultado = self._on_traduccion
            self.traductor.iniciar()
        hb = asyncio.create_task(self._latido())
        vt = asyncio.create_task(self._vigilar_textos())
        vg = asyncio.create_task(self._vigia()) if self.fabrica is not None else None
        tx = asyncio.create_task(self._enviar())
        await self._envio_terminado.wait()
        if vg is not None:
            vg.cancel()
        # DRENAJE: esperar hasta espera_final_s (30 s) los turnos pendientes (o el cierre del server)
        con = self.con
        t0 = time.monotonic()   # reloj real del loop: el inyectable puede estar congelado en tests
        pend0 = len(con.mapa.pend)
        espera_final = (min(self.espera_final_s, PARADA_DRENAJE_S) if self._parada.is_set()
                        else self.espera_final_s)
        while (con.mapa.pend and not con.cerrada.is_set()
               and time.monotonic() - t0 < espera_final):
            await asyncio.sleep(0.1)
        self.drenaje = {"pendientes_al_fin_fuente": pend0, "pendientes_tras_drenaje": len(con.mapa.pend),
                        "espero_s": round(time.monotonic() - t0, 2), "tope_s": self.espera_final_s}
        if self.rec:
            self.rec.client("drenaje", self.drenaje)
        if not con.cerrada.is_set():
            # colchon corto por si llega texto tardio de la ultima ventana
            try:
                await asyncio.wait_for(con.cerrada.wait(), 1.5)
            except asyncio.TimeoutError:
                pass
        if not con.cerrada.is_set():
            if self.rec:
                self.rec.client("close", {"motivo": self.res.motivo_fin, "conexion": con.id,
                                          "pendientes_sin_texto": len(con.mapa.pend)})
            await con.tr.cerrar()
        try:
            await asyncio.wait_for(con.rx, 5)
        except (asyncio.TimeoutError, Exception):
            con.rx.cancel()
        await tx
        for r in self._retiros:          # viejas drenando (tope DRENAJE_VIEJA_S cada una)
            try:
                await asyncio.wait_for(r, self.cfg.drenaje_vieja_s + 6)
            except (asyncio.TimeoutError, Exception):
                r.cancel()
        self._fin_sala.set()
        hb.cancel()
        vt.cancel()
        self._emitir_asignaciones(con.mapa.vaciar(), con)
        if self.traductor is not None:
            await self.traductor.cerrar(self.traductor.timeout_s + 5.0)   # ultimo lote + en vuelo
        self._vivo = False
        cierre = con.cierre or {}
        if cierre.get("by") in ("server", "red") and cierre.get("code") not in (1000, None):
            self.emitir("error", meta={"code": cierre.get("code"),
                                       "message": f"server cerro la sesion: {cierre.get('reason')}"})
        if (cierre.get("by") in ("server", "red") and not self.res.motivo_fin.startswith("tope")
                and not self._parada.is_set()):
            self.res.motivo_fin = "server_cerro" + ("_tras_goaway" if self.res.goaway else "")
        self.emitir("session_end", meta={"reason": self.res.motivo_fin,
                                         "audio_seconds_sent": self.res.segundos_enviados,
                                         "rotaciones": len(self.res.rotaciones)})
        self.res.t_fin = self.reloj()
        return self.res
