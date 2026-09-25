"""Traductor por LOTES del texto final (R19; generico por idioma destino: EN->ES y ES->EN).

Skill gemini-live: traduccion en dos pasos (Live transcribe; un modelo de texto traduce el final).
Diseno B2 (ESTADO.md, Decisiones):
- Lotes de hasta LOTE_MAX=3 textos u LOTE_S=8 s desde el primero pendiente. Una llamada por lote:
  se pide un JSON array con UNA traduccion por linea de entrada, mismo orden.
- Si la cantidad no coincide o hay timeout/error: 1 reintento (con el otro modelo si esta libre);
  despues el lote sale con ok:false (nunca se retrasa ni se cae el original).
- Limitador token-bucket 12 RPM POR MODELO (capacidad 2 => peor caso 14 llamadas en 60 s < 15 RPM
  del free tier) y chequeo cruzado entre procesos contra reportes/cuota-texto.log (<=12 en 60 s).
- Rotacion entre GEMINI_TEXT_MODEL y GEMINI_TEXT_MODEL_ALT. Tope diario por modelo (RPD_TOPE,
  500 RPD en free tier; se corta antes). Reset del tope: 04:00 AR.
- Timeout 15 s por llamada + 1 reintento (B3; el de 3 s vencia casi todo en vivo: 5 de 37 a tiempo).
  429/timeout/error => backoff exponencial por modelo hasta 30 s.
- Thinking (B3, medido con worker/medir_thinking.py -> reportes/audio-pipeline-b3-thinking*.log):
  en gemini-3.5-flash-lite NO se puede apagar: ThinkingConfig(thinking_budget=0) -> 400
  INVALID_ARGUMENT (10/10). Se deja thinking_level="minimal" (el mas bajo que acepta): p50 4694 ms
  vs 8311 ms sin thinking_config (n=10 c/u, intercalados); p95 ~24 s en ambos. p50 NO baja de 3 s.
- Contador: una linea por llamada en reportes/cuota-texto.log: hora | modelo | items | estado | ms
B4 (R19 en vivo: en B3 12 de 49 ok y 29-41 s tarde con 2 sesiones; hipotesis del orquestador: los
lotes se despachaban EN SERIE y uno lento bloqueaba la cola):
- Cada lote es una TAREA INDEPENDIENTE (asyncio.create_task). La unica compuerta es el limitador por
  modelo (12 RPM, token bucket, rotacion 3.5 <-> 3.1 flash-lite).
- Lote = LOTE_MAX=2 ventanas o LOTE_S=5 s desde el primero pendiente.
- Timeout TIMEOUT_S=20 s; al vencer, ok:false SIN reintento (el reintento duplica carga). Reintento
  SOLO ante 429/5xx, con backoff del modelo (el reintento prefiere el otro modelo). Respuesta con
  cantidad distinta o error de otro tipo: ok:false sin reintento.
B5 (R19 en vivo 32/41; gemini-3.1-flash-lite fallo 8 de 11 con 5xx/timeout; ESTADO.md, Decisiones):
- CORTACIRCUITO POR MODELO: FALLAS_CORTE=3 fallas SEGUIDAS (5xx, timeout, 429) sacan al modelo de la
  rotacion CORTE_S=60 s (TRADUCTOR_CORTE_S). Cada corte sucesivo sin una llamada ok en el medio
  duplica la duracion, hasta CORTE_MAX_S=480 s (8 min, TRADUCTOR_CORTE_MAX_S). Al vencer, el modelo
  REINGRESA A PRUEBA: su contador no se reinicia, asi que UNA falla mas lo vuelve a cortar (con el
  doble). Una llamada ok reinicia contador y duplicacion (y levanta el corte si llega de una llamada
  que estaba en vuelo). "cantidad" y otros errores no cuentan ni reinician. Estado POR PROCESO.
- UN SOLO MODELO SANO: lote de LOTE_MAX_SOLO=3 textos u LOTE_S_SOLO=8 s, limitador RPM_SOLO=14 con
  capacidad 1 (peor caso 15 en 60 s). Siempre: tope DURO de TOPE_RPM=15 llamadas por modelo en
  cualquier ventana de 60 s (dentro del proceso).
- NINGUN MODELO SANO: el lote sale ok:false YA, sin llamar, con translation.meta.reason "sin_modelo".
- Cada corte / reingreso / recuperacion deja una linea ROTULADA en reportes/cuota-texto.log que
  empieza con "# cortacircuito" (leer_log la saltea: NO cuenta como llamada; contar llamadas con
  grep -vc '^#' reportes/cuota-texto.log).
- translation.meta.reason (campo AGREGADO, el esquema lo admite): en los ok:false, el estado del
  ultimo intento (sin_modelo | sin_cupo | timeout | 5xx | 429 | cantidad | cierre | error:X).

B8 (con 2 sesiones cada proceso contaba solo sus llamadas TERMINADAS: las que estaban en vuelo en otro
proceso no se veian y se podia pasar de 15 RPM por modelo):
- RESERVA ANTES DE LLAMAR, compartida entre procesos: `Reservas` apende una linea JSON por llamada
  INICIADA a reportes/cuota-texto-reservas.jsonl (CUOTA_TEXTO_RESERVAS) bajo un LOCK DE ARCHIVO
  (`<reservas>.lock`, msvcrt en Windows / fcntl en POSIX). Dentro del lock cuenta las reservas de ese
  modelo con t > ahora - 60 s (de TODOS los procesos) y reserva solo si hay menos que el limite
  (rpm del modo: 12, o 14 con un solo modelo sano). Toda ventana de 60 s queda con <= limite llamadas
  iniciadas, sumando procesos. El token bucket y el tope duro por proceso siguen igual.
- Lote = TRADUCTOR_LOTE_MAX=2 ventanas o TRADUCTOR_LOTE_S=4 s (B4-B7: 5 s).

    python -m worker.traductor --listar                        # ids exactos (models.list)
    python -m worker.traductor --probar "Hello world" --a es   # UNA llamada real (gasta 1 RPD)
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

RAIZ = Path(__file__).resolve().parent.parent
LOG_TEXTO = Path(os.environ.get("CUOTA_TEXTO_LOG", RAIZ / "reportes" / "cuota-texto.log"))
RESERVAS_TEXTO = Path(os.environ.get("CUOTA_TEXTO_RESERVAS",
                                     RAIZ / "reportes" / "cuota-texto-reservas.jsonl"))
TIMEOUT_S = 20.0
ESPERA_CUPO_S = 15.0      # un lote que no consigue cupo en 15 s sale ok:false (ya llegaria tarde)
RPM = 12
CAPACIDAD = 2
RPD_TOPE = int(os.environ.get("TRADUCTOR_RPD_TOPE", 480))
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
FMT = "%Y-%m-%d %H:%M:%S"
IDIOMAS = {"en": "English", "es": "Spanish"}


def _env_num(nombre: str, defecto: float) -> float:
    try:
        return float(os.environ.get(nombre, defecto))
    except ValueError:
        return float(defecto)


# B5: cortacircuito por modelo (ver docstring)
FALLAS_CORTE = int(_env_num("TRADUCTOR_FALLAS_CORTE", 3))
CORTE_S = _env_num("TRADUCTOR_CORTE_S", 60.0)
CORTE_MAX_S = _env_num("TRADUCTOR_CORTE_MAX_S", 480.0)
CUENTAN_PARA_CORTE = ("5xx", "timeout", "429")
RPM_SOLO = 14
CAPACIDAD_SOLO = 1
TOPE_RPM = 15            # free tier: 15 RPM por modelo -> nunca mas de 15 llamadas en 60 s
LOTE_MAX_SOLO = 3
LOTE_S_SOLO = 8.0
MARCA_EVENTO = "# cortacircuito"
LOTE_MAX = int(_env_num("TRADUCTOR_LOTE_MAX", 2))
LOTE_S = _env_num("TRADUCTOR_LOTE_S", 4.0)
VENTANA_RESERVAS_S = 60.0


class LockArchivo:
    """Lock EXCLUSIVO entre procesos sobre un archivo (msvcrt / fcntl), con espera activa corta.
    La seccion critica es chica (leer la cola del archivo de reservas y apendar una linea)."""

    def __init__(self, ruta: Path, timeout_s: float = 5.0):
        self.ruta = Path(ruta)
        self.timeout_s = timeout_s
        self.f = None

    def __enter__(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.ruta, "a+b")
        t_lim = time.monotonic() + self.timeout_s
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > t_lim:
                    self.f.close()
                    raise TimeoutError(f"lock {self.ruta} ocupado mas de {self.timeout_s} s")
                time.sleep(0.005)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        finally:
            self.f.close()
        return False


class Reservas:
    """Llamadas al modelo de texto RESERVADAS ANTES de llamar, compartidas entre procesos (B8).

    Una linea JSON por llamada iniciada: {"t": epoch, "hora": "AAAA-MM-DD HH:MM:SS", "modelo": ...,
    "pid": ..., "etiqueta": ...}. `reservar(modelo, limite)` cuenta, DENTRO del lock, las lineas de
    ese modelo con t > ahora - ventana_s y apenda solo si hay menos que `limite`."""

    COLA_BYTES = 256 * 1024          # 15 RPM x 2 modelos x varios procesos entra de sobra

    def __init__(self, ruta: Path = RESERVAS_TEXTO, ventana_s: float = VENTANA_RESERVAS_S,
                 reloj: Callable[[], float] = time.time, etiqueta: str = ""):
        self.ruta = Path(ruta)
        self.lock = self.ruta.with_name(self.ruta.name + ".lock")
        self.ventana_s = ventana_s
        self.reloj = reloj
        self.etiqueta = etiqueta
        self.n_reservadas = 0
        self.n_rechazadas = 0

    def _recientes(self, ahora: float) -> list[dict]:
        if not self.ruta.exists():
            return []
        with open(self.ruta, "rb") as f:
            f.seek(0, 2)
            tam = f.tell()
            f.seek(max(0, tam - self.COLA_BYTES))
            crudo = f.read().decode("utf-8", errors="replace")
        out = []
        for linea in crudo.splitlines():
            try:
                d = json.loads(linea)
                if float(d["t"]) > ahora - self.ventana_s:
                    out.append(d)
            except (ValueError, KeyError, TypeError):
                continue                  # primera linea cortada por la cola, o basura
        return out

    def contar(self, modelo: str) -> int:
        with LockArchivo(self.lock):
            return sum(1 for d in self._recientes(self.reloj()) if d.get("modelo") == modelo)

    def espera(self, modelo: str, limite: int) -> float:
        """Segundos hasta que se libere un lugar para `modelo` (0 si ya hay)."""
        with LockArchivo(self.lock):
            ahora = self.reloj()
            ts = sorted(float(d["t"]) for d in self._recientes(ahora) if d.get("modelo") == modelo)
        if len(ts) < limite:
            return 0.0
        return max(0.0, ts[len(ts) - limite] + self.ventana_s - ahora)

    def reservar(self, modelo: str, limite: int) -> bool:
        with LockArchivo(self.lock):
            ahora = self.reloj()
            n = sum(1 for d in self._recientes(ahora) if d.get("modelo") == modelo)
            if n >= limite:
                self.n_rechazadas += 1
                return False
            linea = json.dumps({"t": round(ahora, 3),
                                "hora": datetime.fromtimestamp(ahora).strftime(FMT),
                                "modelo": modelo, "pid": os.getpid(), "etiqueta": self.etiqueta,
                                "en_ventana": n + 1, "limite": int(limite)}, ensure_ascii=False)
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(linea + "\n")
            self.n_reservadas += 1
            return True


def modelos_por_defecto() -> list[str]:
    try:
        from worker.gemini import _cargar_env
        _cargar_env()
    except Exception:
        pass
    a = os.environ.get("GEMINI_TEXT_MODEL") or "gemini-3.5-flash-lite"
    b = os.environ.get("GEMINI_TEXT_MODEL_ALT") or "gemini-3.1-flash-lite"
    return [a] if a == b else [a, b]


# ---- transporte ---------------------------------------------------------------------------
class TransporteTexto(Protocol):
    async def generar(self, modelo: str, prompt: str) -> str: ...


class Error429(Exception):
    pass


class Error5xx(Exception):
    pass


REINTENTABLES = ("429", "5xx")


class TransporteGenAI:
    """Transporte REAL (google-genai, models.generate_content). Unico camino de produccion."""

    def __init__(self, nombre_key: str = "GEMINI_API_KEY", thinking_level: Optional[str] = "minimal"):
        self.nombre_key = nombre_key
        self.thinking_level = thinking_level
        self._c = None

    async def generar(self, modelo: str, prompt: str) -> str:
        from google.genai import errors, types
        if self._c is None:
            from worker.gemini import cliente
            self._c = cliente(self.nombre_key)
        kw = {}
        if self.thinking_level:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking_level)
        cfg = types.GenerateContentConfig(
            temperature=0.2, response_mime_type="application/json",
            response_schema={"type": "ARRAY", "items": {"type": "STRING"}}, **kw)
        try:
            r = await self._c.aio.models.generate_content(model=modelo, contents=prompt, config=cfg)
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code == 429:
                raise Error429(str(e)[:200]) from e
            if isinstance(code, int) and 500 <= code < 600:
                raise Error5xx(f"{code}: {str(e)[:200]}") from e
            raise
        return r.text or ""


def prompt_lote(textos: list[str], de: str, a: str) -> str:
    src, dst = IDIOMAS.get(de, de), IDIOMAS.get(a, a)
    return (f"You translate live conference subtitles from {src} to {dst}.\n"
            f"Input: a JSON array with {len(textos)} subtitle fragments, in order. Fragments may start "
            f"or end mid-sentence: translate each one as a fragment, do not merge or split them.\n"
            f"Output: ONLY a JSON array of exactly {len(textos)} strings, one {dst} translation per "
            f"input element, same order. Keep proper names, numbers and technical terms (software "
            f"names, acronyms) as they are. No extra text, no notes.\n"
            f"Input:\n{json.dumps(textos, ensure_ascii=False)}")


def parsear(raw: str, n: int) -> Optional[list[str]]:
    raw = (raw or "").strip()
    raw = re.sub(r"^`{3}(?:json)?\s*|\s*`{3}$", "", raw)
    try:
        arr = json.loads(raw)
    except Exception:
        return None
    if not isinstance(arr, list) or len(arr) != n or not all(isinstance(x, str) for x in arr):
        return None
    return [limpiar(x) for x in arr]


# B7 (adversario B5): 4 traducciones EN->ES de la corrida larga llegaron del modelo con entidades HTML
# ("trav&eacute;s") y caracteres de control (U+0010 dos veces antes de "Qu&eacute;"), y la vista las
# mostraba tal cual.
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def limpiar(texto: str) -> str:
    """Desescapa entidades HTML (una pasada) y DESPUES quita los caracteres de control salvo el salto
    de linea (asi "&#16;" tampoco sobrevive). No toca espacios ni corrige otras roturas del modelo
    (p.ej. "abstracci3n")."""
    return _CONTROL.sub("", html.unescape(texto))


# ---- limitador -----------------------------------------------------------------------------
def inicio_dia(ahora: Optional[datetime] = None) -> datetime:
    """El RPD se resetea 04:00 AR (skill cuota-gemini)."""
    ahora = ahora or datetime.now()
    d = ahora.replace(hour=4, minute=0, second=0, microsecond=0)
    return d if ahora >= d else d - timedelta(days=1)


def leer_log(log: Path = LOG_TEXTO) -> list[tuple[datetime, str, str]]:
    out = []
    if not log.exists():
        return out
    for linea in log.read_text(encoding="utf-8").splitlines():
        p = [x.strip() for x in linea.split("|")]
        if len(p) < 5:
            continue
        try:
            out.append((datetime.strptime(p[0], FMT), p[1], p[3]))
        except ValueError:
            continue
    return out


@dataclass
class EstadoModelo:
    nombre: str
    tokens: float = CAPACIDAD
    t_ref: float = 0.0
    backoff_s: float = 0.0
    bloqueado_hasta: float = 0.0
    hoy: int = 0
    fallas_seguidas: int = 0         # 5xx/timeout/429 seguidas; SOLO una llamada ok lo reinicia
    cortes: int = 0                  # cortes desde la ultima llamada ok (duplica la duracion)
    cortado_hasta: float = 0.0
    en_corte: bool = False           # para anotar el reingreso una sola vez
    ultimas: list = field(default_factory=list)       # ultimos estados que contaron (log)
    llamadas: deque = field(default_factory=deque)    # t de cada token tomado (tope duro 60 s)


class Limitador:
    """Token bucket por modelo (rpm/min, capacidad `capacidad`) + backoff + tope diario.
    B5: cortacircuito por modelo; con UN solo modelo activo, rpm_solo/capacidad_solo; y un tope DURO
    de `tope_rpm` tokens por modelo en cualquier ventana de 60 s."""

    def __init__(self, modelos: list[str], rpm: float = RPM, capacidad: float = CAPACIDAD,
                 rpd_tope: int = RPD_TOPE, log: Optional[Path] = LOG_TEXTO,
                 reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 fallas_corte: int = FALLAS_CORTE, corte_s: float = CORTE_S,
                 corte_max_s: float = CORTE_MAX_S, rpm_solo: float = RPM_SOLO,
                 capacidad_solo: float = CAPACIDAD_SOLO, tope_rpm: int = TOPE_RPM,
                 on_evento: Optional[Callable[[str], None]] = None,
                 reservas="auto"):
        self.rpm = rpm
        self.rate = rpm / 60.0
        self.cap = capacidad
        self.rpd_tope = rpd_tope
        self.log = log
        self.reloj = reloj
        self.dormir = dormir
        self.fallas_corte = fallas_corte
        self.corte_s = corte_s
        self.corte_max_s = corte_max_s
        self.rpm_solo = rpm_solo
        self.cap_solo = capacidad_solo
        self.tope_rpm = tope_rpm
        self.on_evento = on_evento
        self.eventos: list[dict] = []    # cortes / reingresos / recuperaciones (resumen de la corrida)
        t = reloj()
        self.m = {n: EstadoModelo(n, capacidad, t) for n in modelos}
        self.orden = list(modelos)
        self._rr = 0
        # B8: reservas entre procesos. "auto": junto al log de cuota (el de reportes/ usa
        # CUOTA_TEXTO_RESERVAS); sin log (tests con reloj falso) no hay reservas.
        if reservas == "auto":
            if log is None:
                reservas = None
            elif Path(log) == LOG_TEXTO:
                reservas = Reservas(RESERVAS_TEXTO)
            else:
                reservas = Reservas(Path(log).with_name("cuota-texto-reservas.jsonl"))
        self.reservas: Optional[Reservas] = reservas
        if log is not None:
            d0 = inicio_dia()
            for dt, mod, _est in leer_log(log):
                if dt >= d0 and mod in self.m:
                    self.m[mod].hoy += 1

    # -- cortacircuito --
    def sanos(self) -> list[str]:
        """Modelos en la rotacion (sin corte vigente). Un corte vencido REINGRESA a prueba."""
        t = self.reloj()
        out = []
        for n in self.orden:
            e = self.m[n]
            if e.cortado_hasta > t:
                continue
            if e.en_corte:
                e.en_corte = False
                self._evento(n, "reingreso", f"corte vencido, a prueba: fallas seguidas "
                             f"{e.fallas_seguidas}, una mas lo vuelve a cortar")
            out.append(n)
        return out

    def activos(self) -> list[str]:
        """Sanos y con tope diario disponible."""
        return [n for n in self.sanos() if self.m[n].hoy < self.rpd_tope]

    def modo(self) -> tuple[float, float]:
        """(rpm, capacidad). Con UN solo modelo activo: rpm_solo / capacidad_solo."""
        if len(self.activos()) == 1:
            return self.rpm_solo, self.cap_solo
        return self.rpm, self.cap

    def _evento(self, modelo: str, tipo: str, detalle: str) -> None:
        t = self.reloj()
        sanos = [n for n in self.orden if self.m[n].cortado_hasta <= t]
        linea = (f"{MARCA_EVENTO} | {datetime.now().strftime(FMT)} | {modelo} | {tipo} | {detalle} | "
                 f"sanos: {','.join(sanos) or 'ninguno'}")
        self.eventos.append({"t": t, "modelo": modelo, "tipo": tipo, "detalle": detalle,
                             "sanos": sanos})
        if self.on_evento is not None:
            try:
                self.on_evento(linea)
            except Exception:
                pass

    # -- token bucket --
    def _recargar(self, e: EstadoModelo, t: float, rpm: Optional[float] = None,
                  cap: Optional[float] = None) -> None:
        if rpm is None or cap is None:
            rpm, cap = self.modo()
        e.tokens = min(cap, e.tokens + (t - e.t_ref) * rpm / 60.0)
        e.t_ref = t

    def _reservar(self, modelo: str, limite: float) -> bool:
        """B8: reserva la llamada en el archivo compartido entre procesos (si hay reservas)."""
        if self.reservas is None:
            return True
        try:
            return self.reservas.reservar(modelo, int(limite))
        except TimeoutError:
            return False                   # lock trabado: no llamar ahora, reintentar en 0,5 s

    def espera(self, modelo: str) -> float:
        """Segundos hasta que `modelo` pueda llamar (inf si agoto el tope diario)."""
        e = self.m[modelo]
        if e.hoy >= self.rpd_tope:
            return float("inf")
        t = self.reloj()
        rpm, cap = self.modo()
        self._recargar(e, t, rpm, cap)
        w = max(0.0, e.bloqueado_hasta - t, e.cortado_hasta - t)
        if e.tokens < 1 - 1e-9:
            w = max(w, (1 - e.tokens) / (rpm / 60.0))
        while e.llamadas and t - e.llamadas[0] >= 60.0:
            e.llamadas.popleft()
        if len(e.llamadas) >= self.tope_rpm:           # tope duro: nunca > tope_rpm en 60 s
            w = max(w, e.llamadas[-self.tope_rpm] + 60.0 - t)
        return w

    async def elegir(self, evitar: Optional[str] = None,
                     espera_max: float = 60.0) -> tuple[Optional[str], str]:
        """(modelo, "ok") consumiendo un token. (None, "sin_modelo") si NINGUN modelo esta sano: de
        inmediato, sin esperar. (None, "sin_cupo") si no hay token dentro de espera_max o se agoto el
        tope diario."""
        t_lim = self.reloj() + espera_max
        while True:
            sanos = self.sanos()
            if not sanos:
                return None, "sin_modelo"
            rpm, _cap = self.modo()
            rot = [n for n in self.orden[self._rr:] + self.orden[:self._rr] if n in sanos]
            cands = [n for n in rot if n != evitar] + [n for n in rot if n == evitar]
            esperas = sorted((self.espera(n), i, n) for i, n in enumerate(cands))
            w = esperas[0][0]
            if w == float("inf"):
                return None, "sin_cupo"
            for wn, _, n in esperas:        # el primero libre que consiga RESERVA entre procesos
                if wn > 0:
                    break
                if self._reservar(n, rpm):
                    e = self.m[n]
                    e.tokens -= 1
                    e.hoy += 1
                    e.llamadas.append(self.reloj())
                    self._rr = (self.orden.index(n) + 1) % len(self.orden)
                    return n, "ok"
            t = self.reloj()
            vence = [self.m[x].cortado_hasta - t for x in self.orden if x not in sanos]
            if vence:                    # un corte que vence antes: reevaluar ahi (reingreso)
                w = min(w, max(min(vence), 0.0))
            w = max(w, 0.5)
            if t + w > t_lim:
                return None, "sin_cupo"
            await self.dormir(w)

    async def tomar(self, evitar: Optional[str] = None, espera_max: float = 60.0) -> Optional[str]:
        """Modelo a usar (consume un token) o None si ninguno queda libre dentro de espera_max."""
        n, _motivo = await self.elegir(evitar, espera_max)
        return n

    def exito(self, modelo: str) -> None:
        e = self.m[modelo]
        e.backoff_s = 0.0
        if e.cortes or e.en_corte or e.cortado_hasta > self.reloj():
            e.cortado_hasta = 0.0
            e.en_corte = False
            self._evento(modelo, "recuperacion", f"llamada ok tras {e.cortes} corte(s): contador y "
                         f"duplicacion reiniciados")
        e.fallas_seguidas = 0
        e.cortes = 0
        e.ultimas = []

    def fallo(self, modelo: str, estado: str = "error") -> float:
        """Backoff exponencial (1..30 s) y, si `estado` es 5xx/timeout/429, cortacircuito."""
        e = self.m[modelo]
        e.backoff_s = min(BACKOFF_MAX, max(BACKOFF_MIN, e.backoff_s * 2))
        t = self.reloj()
        e.bloqueado_hasta = t + e.backoff_s
        if estado in CUENTAN_PARA_CORTE:
            e.fallas_seguidas += 1
            e.ultimas = (e.ultimas + [estado])[-5:]
            if e.fallas_seguidas >= self.fallas_corte and e.cortado_hasta <= t:
                dur = min(self.corte_max_s, self.corte_s * (2 ** e.cortes))
                e.cortes += 1
                e.cortado_hasta = t + dur
                e.en_corte = True
                self._evento(modelo, "corte", f"{dur:g} s (corte {e.cortes} desde la ultima ok); "
                             f"fallas seguidas {e.fallas_seguidas}: {','.join(e.ultimas)}")
        return e.backoff_s


# ---- lotes -----------------------------------------------------------------------------------
@dataclass
class Item:
    seq: int
    text: str
    t: float


@dataclass
class Lote:
    items: list[Item]
    t_cierre: float
    motivo: str          # "lleno" | "tiempo" | "fin"


class Lotes:
    """Hasta `maximo` textos u `ventana_s` desde el primero pendiente (lo usan vivo y offline)."""

    def __init__(self, maximo: int = LOTE_MAX, ventana_s: float = LOTE_S):
        self.maximo = maximo
        self.ventana_s = ventana_s
        self.pend: list[Item] = []

    def agregar(self, seq: int, text: str, t: float) -> list[Lote]:
        out = []
        if self.pend and t - self.pend[0].t >= self.ventana_s:
            out.append(Lote(self.pend, self.pend[0].t + self.ventana_s, "tiempo"))
            self.pend = []
        self.pend.append(Item(seq, text, t))
        if len(self.pend) >= self.maximo:
            out.append(Lote(self.pend, t, "lleno"))
            self.pend = []
        return out

    def vencido(self, ahora: float) -> Optional[Lote]:
        if self.pend and len(self.pend) >= self.maximo:       # B5: el maximo cambia con los modelos sanos
            lote = Lote(self.pend, ahora, "lleno")
            self.pend = []
            return lote
        if self.pend and ahora - self.pend[0].t >= self.ventana_s:
            lote = Lote(self.pend, self.pend[0].t + self.ventana_s, "tiempo")
            self.pend = []
            return lote
        return None

    def vaciar(self, ahora: float) -> Optional[Lote]:
        if not self.pend:
            return None
        lote = Lote(self.pend, ahora, "fin")
        self.pend = []
        return lote


# ---- traductor ---------------------------------------------------------------------------------
@dataclass
class ResultadoLote:
    items: list[dict]            # [{"seq", "text", "ok"}]
    modelo: Optional[str]
    ms: int
    intentos: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(i["ok"] for i in self.items)

    @property
    def reason(self) -> Optional[str]:
        """B5: en un lote ok:false, el estado del ultimo intento (p.ej. "sin_modelo")."""
        if self.ok or not self.intentos:
            return None
        return self.intentos[-1].get("estado")


class Traductor:
    def __init__(self, de: str, a: str, transporte: Optional[TransporteTexto] = None,
                 modelos: Optional[list[str]] = None, limitador: Optional[Limitador] = None,
                 log: Optional[Path] = LOG_TEXTO, timeout_s: float = TIMEOUT_S,
                 lotes: Optional[Lotes] = None, espera_cupo_s: float = ESPERA_CUPO_S,
                 reintentos: int = 1,
                 lote_solo: tuple[int, float] = (LOTE_MAX_SOLO, LOTE_S_SOLO),
                 on_resultado: Optional[Callable[[ResultadoLote], None]] = None,
                 logger: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True)):
        self.de, self.a = de, a
        self.tr = transporte if transporte is not None else TransporteGenAI()
        self.modelos = modelos or modelos_por_defecto()
        self.lim = limitador or Limitador(self.modelos, log=log)
        self.log = log
        self.timeout_s = timeout_s
        self._lotes_adaptativos = lotes is None      # B5: con un lote propio no se toca
        self.lotes = lotes or Lotes()
        self._lote_normal = (self.lotes.maximo, self.lotes.ventana_s)
        self._lote_solo = lote_solo
        self.lote_solo = False
        if self.lim.on_evento is None:
            self.lim.on_evento = self._anotar_evento
        self.espera_cupo_s = espera_cupo_s
        self.reintentos = reintentos
        self.on_resultado = on_resultado
        self.logger = logger
        self.n_llamadas = 0
        self.n_lotes = 0
        self.n_ok_false = 0
        self.por_modelo: dict[str, int] = {}
        self._vigia: Optional[asyncio.Task] = None
        self._vuelo: dict = {}           # tarea -> lote (lotes en vuelo, EN PARALELO)
        self.max_en_vuelo = 0
        self.n_timeout = 0
        self.n_reintentos = 0
        self.n_sin_modelo = 0

    def _anotar(self, modelo: str, n: int, estado: str, ms: int) -> None:
        self.n_llamadas += 1
        self.por_modelo[modelo] = self.por_modelo.get(modelo, 0) + 1
        if self.log is None:
            return
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime(FMT)} | {modelo} | {n} | {estado} | {ms}\n")

    def _anotar_evento(self, linea: str) -> None:
        """Linea ROTULADA del cortacircuito en el mismo log de cuota (leer_log la saltea)."""
        self.logger(f"[traductor] {linea}")
        if self.log is None:
            return
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(linea + "\n")

    @property
    def cortes(self) -> int:
        return sum(1 for e in self.lim.eventos if e["tipo"] == "corte")

    async def traducir(self, textos: list[str], seqs: Optional[list[int]] = None) -> ResultadoLote:
        """UNA llamada por lote (+1 reintento SOLO ante 429/5xx). Nunca levanta excepcion."""
        seqs = seqs if seqs is not None else list(range(len(textos)))
        prompt = prompt_lote(textos, self.de, self.a)
        intentos: list[dict] = []
        t_lote = time.monotonic()
        previo = None
        for _ in range(1 + self.reintentos):
            modelo, motivo = await self.lim.elegir(evitar=previo, espera_max=self.espera_cupo_s)
            if modelo is None:            # "sin_modelo" (ninguno sano: sin llamar) o "sin_cupo"
                intentos.append({"modelo": None, "estado": motivo, "ms": 0})
                if motivo == "sin_modelo":
                    self.n_sin_modelo += 1
                break
            t0 = time.monotonic()
            try:
                raw = await asyncio.wait_for(self.tr.generar(modelo, prompt), self.timeout_s)
                arr = parsear(raw, len(textos))
                ms = int((time.monotonic() - t0) * 1000)
                if arr is not None:
                    self._anotar(modelo, len(textos), "ok", ms)
                    self.lim.exito(modelo)
                    intentos.append({"modelo": modelo, "estado": "ok", "ms": ms})
                    return ResultadoLote([{"seq": s, "text": x, "ok": True} for s, x in zip(seqs, arr)],
                                         modelo, int((time.monotonic() - t_lote) * 1000), intentos)
                estado = "cantidad"
            except asyncio.TimeoutError:
                ms, estado = int((time.monotonic() - t0) * 1000), "timeout"
                self.n_timeout += 1
            except Error429:
                ms, estado = int((time.monotonic() - t0) * 1000), "429"
            except Error5xx:
                ms, estado = int((time.monotonic() - t0) * 1000), "5xx"
            except Exception as e:
                ms, estado = int((time.monotonic() - t0) * 1000), f"error:{type(e).__name__}"
            self._anotar(modelo, len(textos), estado, ms)
            espera = self.lim.fallo(modelo, estado) if estado != "cantidad" else 0.0
            intentos.append({"modelo": modelo, "estado": estado, "ms": ms, "backoff_s": espera})
            self.logger(f"[traductor] {modelo} {estado} en {ms} ms (lote de {len(textos)})")
            previo = modelo
            if estado not in REINTENTABLES:
                break                     # timeout / cantidad / otro error: ok:false sin reintento
            self.n_reintentos += 1
        self.n_ok_false += 1
        return ResultadoLote([{"seq": s, "text": None, "ok": False} for s in seqs],
                             intentos[-1]["modelo"] if intentos else None,
                             int((time.monotonic() - t_lote) * 1000), intentos)

    # -- modo vivo (SessionWorker) --
    def _ajustar_lotes(self) -> None:
        """B5: con UN solo modelo sano, lotes de 3 textos u 8 s (menos llamadas para el mismo texto)."""
        if not self._lotes_adaptativos:
            return
        activos = self.lim.activos()
        solo = len(activos) == 1
        if solo != self.lote_solo:
            self.lote_solo = solo
            self.lotes.maximo, self.lotes.ventana_s = self._lote_solo if solo else self._lote_normal
            self.logger(f"[traductor] lotes: {self.lotes.maximo} textos / {self.lotes.ventana_s:g} s "
                        f"(modelos activos: {','.join(activos) or 'ninguno'})")

    def agregar(self, seq: int, text: str, t: float) -> None:
        self._ajustar_lotes()
        for lote in self.lotes.agregar(seq, text, t):
            self._poner(lote)

    def _poner(self, lote) -> None:
        """Cada lote es una tarea independiente: un lote lento NO bloquea a los siguientes."""
        t = asyncio.create_task(self._procesar(lote))
        self._vuelo[t] = lote
        self.max_en_vuelo = max(self.max_en_vuelo, len(self._vuelo))

    async def _procesar(self, lote) -> None:
        try:
            self.n_lotes += 1
            res = await self.traducir([i.text for i in lote.items], [i.seq for i in lote.items])
        except asyncio.CancelledError:
            raise                      # cerrar() la marca ok:false
        except Exception as e:         # nunca se cae el worker
            self.logger(f"[traductor] error inesperado: {type(e).__name__}: {e}")
            self.n_ok_false += 1
            res = ResultadoLote([{"seq": i.seq, "text": None, "ok": False} for i in lote.items], None, 0,
                                [{"modelo": None, "estado": f"error:{type(e).__name__}", "ms": 0}])
        self._vuelo.pop(asyncio.current_task(), None)
        if self.on_resultado:
            try:
                self.on_resultado(res)
            except Exception as e:
                self.logger(f"[traductor] on_resultado fallo: {type(e).__name__}: {e}")

    async def _vigilar(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            self._ajustar_lotes()
            lote = self.lotes.vencido(time.time())
            if lote:
                self._poner(lote)

    def _abandonar(self, lotes: list) -> int:
        """Al cerrar por timeout: lo que quedo sin traducir sale MARCADO (ok:false), no mudo."""
        for lote in lotes:
            self.n_ok_false += 1
            res = ResultadoLote([{"seq": i.seq, "text": None, "ok": False} for i in lote.items], None, 0,
                                [{"modelo": None, "estado": "cierre", "ms": 0}])
            if self.on_resultado:
                self.on_resultado(res)
        return len(lotes)

    def iniciar(self) -> None:
        if self._vigia is None:
            self._vigia = asyncio.create_task(self._vigilar())

    async def cerrar(self, timeout: float = TIMEOUT_S + 5.0) -> None:
        """Manda el lote pendiente y espera las traducciones en vuelo (hasta timeout)."""
        lote = self.lotes.vaciar(time.time())
        if lote:
            self._poner(lote)
        if self._vigia is not None:
            self._vigia.cancel()
        tareas = list(self._vuelo)
        if tareas:
            await asyncio.wait(tareas, timeout=timeout)
        quedan = [(t, self._vuelo.pop(t)) for t in list(self._vuelo)]
        for t, _ in quedan:
            t.cancel()
        for t, _ in quedan:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._abandonar([l for _, l in quedan])


def meta_traduccion(res: ResultadoLote, lang_to: str, source=None) -> dict:
    # el contrato exige model string: sin llamada exitosa ni intento (sin cupo, cierre) va "ninguno"
    m = {"lang_to": lang_to, "model": res.modelo or "ninguno", "batch_ms": int(res.ms)}
    if res.reason:                     # B5 (campo agregado): p.ej. "sin_modelo"
        m["reason"] = res.reason
    if source is not None:
        m["source"] = source
    return m


def listar(out=sys.stdout) -> int:
    from worker.gemini import cliente
    c = cliente()
    todos = list(c.models.list())
    print(f"# models.list(): {len(todos)} modelos; filtro 'flash-lite'", file=out)
    for m in todos:
        if "flash-lite" in (m.name or ""):
            print(f"{m.name} | actions={list(getattr(m, 'supported_actions', None) or [])} | "
                  f"in_tokens={getattr(m, 'input_token_limit', None)}", file=out)
    print(f"# configurados (GEMINI_TEXT_MODEL, GEMINI_TEXT_MODEL_ALT): {modelos_por_defecto()}", file=out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.traductor")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--probar", nargs="*", default=None, help="textos a traducir en UNA llamada real")
    ap.add_argument("--de", default="en")
    ap.add_argument("--a", default="es")
    x = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if x.listar:
        return listar()
    if x.probar:
        tr = Traductor(x.de, x.a)
        res = asyncio.run(tr.traducir(list(x.probar)))
        print(json.dumps({"items": res.items, "modelo": res.modelo, "ms": res.ms,
                          "intentos": res.intentos}, ensure_ascii=False))
        return 0 if res.ok else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
