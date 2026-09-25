"""Traductor por LOTES del texto final (R19; generico por idioma destino: EN->ES y ES->EN obligan;
cualquier otro codigo ISO sale del mismo camino via --a/--traducir-a, ej. pt R8b "mas idiomas";
verificado B10 con pt offline contra fixtures/casetes/, ver worker/traducir_casete.py).

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

FINAL (25/09, cola larga: en las tomas reales la traduccion llego p50 3,7 s / p95 12,8 s despues de su
linea; la cola eran 5xx y timeouts de UN modelo):
- PEDIDO CUBIERTO ("hedge"): si un lote no respondio en TRADUCTOR_HEDGE_S (default 4,0 s), se dispara
  el MISMO prompt al OTRO modelo sano, SOLO si ese modelo tiene cupo YA (token del limitador + reserva
  entre procesos; no espera). Gana la primera respuesta valida; la otra llamada se cancela (queda en el
  log con estado "cancelado": el server la recibio y cuenta para el RPD). Si una de las dos falla, se
  espera a la otra. Sin otro modelo sano (o sin cupo en el otro, dejando TRADUCTOR_HEDGE_MARGEN=3
  lugares libres en su ventana de 60 s para lotes nuevos) no se cubre. TRADUCTOR_HEDGE_S=0
  apaga la cobertura (comportamiento anterior).
- Reservas POR KEY: cada linea de reserva lleva `key` (NOMBRE de la variable, nunca el valor) y el
  tope se cuenta por (key, modelo). Dos salas con --key distinta (proyectos distintos) no se limitan
  entre si aunque compartan el archivo; dos con la misma key si. Lineas viejas sin `key` cuentan como
  GEMINI_API_KEY.

LATENCIA (25/09: el traducido llegaba ~7 s detras del orador; texto->traduccion p50 3,8 s / p95 12,8 s,
la mitad era la espera del lote):
- TRADUCTOR_MODO=inmediato (default): cada `text` se traduce SOLO y YA si el modelo menos cargado de la
  key tiene <= TRADUCTOR_INMEDIATO_MAX=6 reservas en los ultimos 60 s (tope 12). Si no hay ese margen
  cae al lote normal (2 textos / 4 s) y, con > TRADUCTOR_APRETADO_MAX=9 o con un lote de la sala
  ESPERANDO cupo en el limitador > TRADUCTOR_CONGESTION_S=1 s (congestion), al lote de 3 textos / 8 s.
  Umbrales 6/9 y no 9/11 (test_inmediato.py con reloj falso, cadencia real, 240 s): con 9/11, 1 sala
  por key sale 57/67 inmediato pero 3 salas por key bajan la cobertura a 0,688 (lote de hoy: 0,769);
  con 6/9, 1 sala 41/67 inmediato (p50 1,4 s vs 3,5 s en lote) y 3 salas 0,804.
  El conteo lo refresca el vigia cada 0,2 s EN UN HILO (lee el archivo de reservas con lock); el tope
  lo sigue imponiendo el limitador (token bucket + reserva entre procesos): el modo solo decide el
  tamano del lote. TRADUCTOR_MODO=lote = comportamiento anterior (2/4 s; 3/8 s con un solo modelo).
- Modelo por PREFERENCIA (3.5-flash-lite primero, medido mas rapido), no rotacion (TRADUCTOR_ROTAR=1
  la restituye). Pedido cubierto a los TRADUCTOR_HEDGE_S=2,5 s (antes 4,0).

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

def _env_num_pre(nombre: str, defecto: float) -> float:
    try:
        return float(os.environ.get(nombre, defecto))
    except ValueError:
        return float(defecto)


RAIZ = Path(__file__).resolve().parent.parent
LOG_TEXTO = Path(os.environ.get("CUOTA_TEXTO_LOG", RAIZ / "reportes" / "cuota-texto.log"))
RESERVAS_TEXTO = Path(os.environ.get("CUOTA_TEXTO_RESERVAS",
                                     RAIZ / "reportes" / "cuota-texto-reservas.jsonl"))
TIMEOUT_S = 20.0
ESPERA_CUPO_S = 15.0      # un lote que no consigue cupo en 15 s sale ok:false (ya llegaria tarde)
# Topes propios por (key, modelo). Defaults = nivel GRATUITO (15 RPM por modelo, vistos en AI Studio el 24/09).
# En nivel PAGO subirlos con los valores de la vista de limites de AI Studio del proyecto, p. ej.
# TRADUCTOR_RPM=300 TRADUCTOR_TOPE_RPM=400: con cupo holgado cada linea se traduce sola y al instante.
RPM = _env_num_pre("TRADUCTOR_RPM", 12)
CAPACIDAD = 2
RPD_TOPE = int(os.environ.get("TRADUCTOR_RPD_TOPE", 480))
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
FMT = "%Y-%m-%d %H:%M:%S"
# Nombres para el prompt (mejora la calidad); GENERICO por codigo: un idioma que no este aca igual
# funciona, prompt_lote cae al codigo ISO tal cual (IDIOMAS.get(x, x)), Gemini lo entiende.
IDIOMAS = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German",
           "it": "Italian"}


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
RPM_SOLO = _env_num("TRADUCTOR_RPM_SOLO", max(RPM + 2, 14) if RPM > 12 else 14)
CAPACIDAD_SOLO = 1
TOPE_RPM = int(_env_num("TRADUCTOR_TOPE_RPM", 15))  # free tier: 15 RPM por modelo -> nunca mas de 15 en 60 s
LOTE_MAX_SOLO = 3
LOTE_S_SOLO = 8.0
MARCA_EVENTO = "# cortacircuito"
LOTE_MAX = int(_env_num("TRADUCTOR_LOTE_MAX", 2))
LOTE_S = _env_num("TRADUCTOR_LOTE_S", 4.0)
HEDGE_S = _env_num("TRADUCTOR_HEDGE_S", 2.5)       # LATENCIA 25/09: 4,0 -> 2,5 s
# el pedido cubierto solo reserva si el otro modelo tiene al menos HEDGE_MARGEN lugares libres en la
# ventana de 60 s (de esa key): con el cupo casi lleno, cubrir le quita lugar a lotes nuevos
# (simulacion: 2 salas / 1 key con cobertura 0,97 con hedge sin margen vs 1,0 sin hedge)
HEDGE_MARGEN = int(_env_num("TRADUCTOR_HEDGE_MARGEN", 3))
KEY_DEFAULT = "GEMINI_API_KEY"
VENTANA_RESERVAS_S = 60.0
# LATENCIA 25/09: traduccion INMEDIATA ADAPTATIVA (ver docstring). TRADUCTOR_MODO=inmediato|lote.
MODO = (os.environ.get("TRADUCTOR_MODO") or "inmediato").strip().lower()
# inmediato si el modelo menos cargado de ESTA key tiene <= INMEDIATO_MAX reservas en 60 s (tope 12);
# si no, lote normal (2/4 s); con > APRETADO_MAX, lote solo (3/8 s).
# por defecto escalan con RPM (6 y 9 con el tope gratuito de 12)
INMEDIATO_MAX = int(_env_num("TRADUCTOR_INMEDIATO_MAX", round(RPM * 0.5)))
APRETADO_MAX = int(_env_num("TRADUCTOR_APRETADO_MAX", round(RPM * 0.75)))
# orden de PREFERENCIA (el primero de la lista, 3.5-flash-lite, medido mas rapido) en vez de rotar;
# TRADUCTOR_ROTAR=1 vuelve a la rotacion 3.5 <-> 3.1 de antes.
ROTAR = os.environ.get("TRADUCTOR_ROTAR", "0") == "1"
CONGESTION_S = _env_num("TRADUCTOR_CONGESTION_S", 1.0)


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
                 reloj: Callable[[], float] = time.time, etiqueta: str = "", key: str = KEY_DEFAULT):
        self.ruta = Path(ruta)
        self.key = key or KEY_DEFAULT          # NOMBRE de la variable de la key, nunca el valor
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

    def _mia(self, d: dict, modelo: str) -> bool:
        """La reserva cuenta para (esta key, modelo). Sin campo key (lineas previas): GEMINI_API_KEY."""
        return d.get("modelo") == modelo and (d.get("key") or KEY_DEFAULT) == self.key

    def contar(self, modelo: str) -> int:
        with LockArchivo(self.lock):
            return sum(1 for d in self._recientes(self.reloj()) if self._mia(d, modelo))

    def espera(self, modelo: str, limite: int) -> float:
        """Segundos hasta que se libere un lugar para `modelo` (0 si ya hay)."""
        with LockArchivo(self.lock):
            ahora = self.reloj()
            ts = sorted(float(d["t"]) for d in self._recientes(ahora) if self._mia(d, modelo))
        if len(ts) < limite:
            return 0.0
        return max(0.0, ts[len(ts) - limite] + self.ventana_s - ahora)

    def reservar(self, modelo: str, limite: int) -> bool:
        with LockArchivo(self.lock):
            ahora = self.reloj()
            n = sum(1 for d in self._recientes(ahora) if self._mia(d, modelo))
            if n >= limite:
                self.n_rechazadas += 1
                return False
            linea = json.dumps({"t": round(ahora, 3),
                                "hora": datetime.fromtimestamp(ahora).strftime(FMT),
                                "modelo": modelo, "key": self.key, "pid": os.getpid(),
                                "etiqueta": self.etiqueta,
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
            # doble (25/09): crear el cliente (httpx + contexto SSL) frenaba el loop ~0,7-0,9 s en la
            # primera traduccion; va a un hilo para no atrasar el pautado de la fuente
            self._c = await asyncio.to_thread(cliente, self.nombre_key)
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
            f"The source may contain words glued together or repeated at segment joins; translate the "
            f"intended meaning into clean, well-punctuated text.\n"
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
                 reservas="auto", key: str = KEY_DEFAULT, rotar: Optional[bool] = None):
        self.rotar = ROTAR if rotar is None else bool(rotar)
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
                reservas = Reservas(RESERVAS_TEXTO, key=key)
            else:
                reservas = Reservas(Path(log).with_name("cuota-texto-reservas.jsonl"), key=key)
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

    async def _reservar_async(self, modelo: str, limite: float) -> bool:
        """Doble (25/09): la reserva toma un LOCK DE ARCHIVO con espera activa (time.sleep, hasta
        LockArchivo.timeout_s) y lee la cola del archivo: va a un hilo (asyncio.to_thread) para que
        NUNCA frene el loop donde corren el pautado de la fuente y el envio de audio."""
        if self.reservas is None:
            return True
        try:
            return await asyncio.to_thread(self.reservas.reservar, modelo, int(limite))
        except TimeoutError:
            return False

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
            rr = self._rr if self.rotar else 0           # sin rotar: orden de preferencia
            rot = [n for n in self.orden[rr:] + self.orden[:rr] if n in sanos]
            cands = [n for n in rot if n != evitar] + [n for n in rot if n == evitar]
            esperas = sorted((self.espera(n), i, n) for i, n in enumerate(cands))
            w = esperas[0][0]
            if w == float("inf"):
                return None, "sin_cupo"
            for wn, _, n in esperas:        # el primero libre que consiga RESERVA entre procesos
                if wn > 0:
                    break
                if await self._reservar_async(n, rpm):
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

    def carga(self, modelo: str) -> int:
        """Llamadas de `modelo` en los ultimos 60 s PARA ESTA KEY: reservas entre procesos (con lock:
        llamar desde un hilo) o, sin reservas, los tokens tomados por este proceso."""
        if self.reservas is not None:
            try:
                return self.reservas.contar(modelo)
            except TimeoutError:
                return 10 ** 6            # lock trabado: sin dato, no arriesgar inmediato
        e = self.m[modelo]
        t = self.reloj()
        return sum(1 for x in e.llamadas if t - x < 60.0)

    async def tomar_alterno(self, modelo: str) -> Optional[str]:
        """FINAL (hedge): OTRO modelo sano con cupo YA (token + tope duro + reserva entre procesos), o
        None. No espera: un pedido cubierto que tiene que esperar cupo ya no sirve."""
        rpm, _cap = self.modo()
        limite = rpm - HEDGE_MARGEN
        if limite < 1:
            return None
        for n in self.sanos():
            if n == modelo or self.espera(n) > 0:
                continue
            if await self._reservar_async(n, limite):
                e = self.m[n]
                e.tokens -= 1
                e.hoy += 1
                e.llamadas.append(self.reloj())
                return n
        return None

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
                 logger: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True),
                 hedge_s: Optional[float] = None, key: Optional[str] = None,
                 modo: Optional[str] = None, inmediato_max: int = INMEDIATO_MAX,
                 apretado_max: int = APRETADO_MAX, reloj: Callable[[], float] = time.time):
        self.de, self.a = de, a
        self.modo = (modo or MODO)
        if self.modo not in ("inmediato", "lote"):
            raise ValueError(f"TRADUCTOR_MODO invalido: {self.modo!r} (inmediato|lote)")
        self.inmediato_max = int(inmediato_max)
        self.apretado_max = int(apretado_max)
        self.reloj = reloj
        self.carga_min = 0               # min de reservas en 60 s entre modelos activos (refresca el vigia)
        self.nivel = "inmediato" if self.modo == "inmediato" else "lote"
        self.textos_por_nivel: dict[str, int] = {}
        self.esperando_cupo: dict = {}   # id -> t de inicio: lotes de esta sala esperando cupo (congestion)
        self.tr = transporte if transporte is not None else TransporteGenAI()
        self.modelos = modelos or modelos_por_defecto()
        # reservas por (key, modelo): la key es la del transporte (--key de la sala)
        self.key = key or getattr(self.tr, "nombre_key", None) or KEY_DEFAULT
        self.lim = limitador or Limitador(self.modelos, log=log, key=self.key)
        self.hedge_s = HEDGE_S if hedge_s is None else float(hedge_s)
        self.n_cubiertos = 0             # lotes a los que se les disparo el pedido cubierto
        self.n_cubiertos_ganados = 0     # lotes en los que el pedido cubierto respondio primero
        self.n_cancelados = 0            # llamadas canceladas por perder la carrera
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
            clave = object()
            self.esperando_cupo[clave] = self.reloj()
            try:
                modelo, motivo = await self.lim.elegir(evitar=previo, espera_max=self.espera_cupo_s)
            finally:
                self.esperando_cupo.pop(clave, None)
            if modelo is None:            # "sin_modelo" (ninguno sano: sin llamar) o "sin_cupo"
                intentos.append({"modelo": None, "estado": motivo, "ms": 0})
                if motivo == "sin_modelo":
                    self.n_sin_modelo += 1
                break
            modelo, arr, estado, ms = await self._llamar_cubierto(modelo, prompt, len(textos), intentos)
            if arr is not None:
                self._anotar(modelo, len(textos), "ok", ms)
                self.lim.exito(modelo)
                intentos.append({"modelo": modelo, "estado": "ok", "ms": ms})
                return ResultadoLote([{"seq": s, "text": x, "ok": True} for s, x in zip(seqs, arr)],
                                     modelo, int((time.monotonic() - t_lote) * 1000), intentos)
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

    async def _intento(self, modelo: str, prompt: str, n: int) -> tuple:
        """UNA llamada: (modelo, arr | None, estado, ms). No anota ni toca el limitador."""
        t0 = time.monotonic()
        try:
            raw = await asyncio.wait_for(self.tr.generar(modelo, prompt), self.timeout_s)
            arr = parsear(raw, n)
            estado = "ok" if arr is not None else "cantidad"
        except asyncio.TimeoutError:
            arr, estado = None, "timeout"
            self.n_timeout += 1
        except Error429:
            arr, estado = None, "429"
        except Error5xx:
            arr, estado = None, "5xx"
        except Exception as e:
            arr, estado = None, f"error:{type(e).__name__}"
        return modelo, arr, estado, int((time.monotonic() - t0) * 1000)

    def _lateral(self, r: tuple, n: int, intentos: list) -> None:
        """Una llamada de la carrera que NO decide el lote (fallo con la otra en vuelo): se anota y
        cuenta para el cortacircuito igual que siempre."""
        modelo, _arr, estado, ms = r
        self._anotar(modelo, n, estado, ms)
        espera = self.lim.fallo(modelo, estado) if estado != "cantidad" else 0.0
        intentos.append({"modelo": modelo, "estado": estado, "ms": ms, "backoff_s": espera,
                         "cubierto": True})
        self.logger(f"[traductor] {modelo} {estado} en {ms} ms (lote de {n}, carrera cubierta)")

    async def _llamar_cubierto(self, modelo: str, prompt: str, n: int, intentos: list) -> tuple:
        """FINAL: la llamada del lote con PEDIDO CUBIERTO (ver docstring del modulo). Devuelve el
        resultado que decide el lote; las demas llamadas de la carrera quedan anotadas en `intentos`."""
        ta = asyncio.create_task(self._intento(modelo, prompt, n))
        if self.hedge_s <= 0:
            return await ta
        pend = {ta}
        t_a = time.monotonic()
        try:
            done, _ = await asyncio.wait(pend, timeout=self.hedge_s)
            if done:
                return ta.result()
            otro = await self.lim.tomar_alterno(modelo)
            if otro is None or ta.done():
                # sin alterno sano con cupo (o A respondio mientras se reservaba: esa reserva se pierde)
                return await ta
            self.n_cubiertos += 1
            self.logger(f"[traductor] {modelo} sin respuesta en {self.hedge_s:g} s: pedido cubierto a {otro}")
            t_b = time.monotonic()
            tb = asyncio.create_task(self._intento(otro, prompt, n))
            pend = {ta, tb}
            while True:
                done, pend = await asyncio.wait(pend, return_when=asyncio.FIRST_COMPLETED)
                res = [t.result() for t in done]
                oks = [r for r in res if r[1] is not None]
                malos = [r for r in res if r[1] is None]
                if oks:
                    for r in malos:
                        self._lateral(r, n, intentos)
                    for t in pend:
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                        perdedor = otro if t is tb else modelo
                        ms = int((time.monotonic() - (t_b if t is tb else t_a)) * 1000)
                        self._anotar(perdedor, n, "cancelado", ms)
                        self.n_cancelados += 1
                        intentos.append({"modelo": perdedor, "estado": "cancelado", "ms": ms,
                                         "cubierto": True})
                    pend = set()
                    if oks[0][0] == otro:
                        self.n_cubiertos_ganados += 1
                    return oks[0]
                if not pend:
                    for r in malos[:-1]:
                        self._lateral(r, n, intentos)
                    return malos[-1]
                for r in malos:
                    self._lateral(r, n, intentos)
        finally:
            for t in pend:
                if not t.done():
                    t.cancel()

    # -- modo vivo (SessionWorker) --
    def actualizar_carga(self) -> int:
        """Minimo de reservas en 60 s (esta key) entre los modelos activos. Toma el lock del archivo de
        reservas: el vigia lo llama en un hilo."""
        activos = self.lim.activos()
        self.carga_min = min((self.lim.carga(m) for m in activos), default=10 ** 6)
        return self.carga_min

    def _ajustar_lotes(self) -> None:
        """B5: con UN solo modelo sano, lotes de 3 textos u 8 s (menos llamadas para el mismo texto).
        LATENCIA 25/09 (modo inmediato): lote de 1 si hay margen; si no, 2/4 s; muy apretado, 3/8 s."""
        if not self._lotes_adaptativos:
            return
        activos = self.lim.activos()
        solo = len(activos) == 1
        self.lote_solo = solo
        # congestion: un lote de esta sala lleva > CONGESTION_S esperando cupo (la reserva normal tarda
        # milisegundos en su hilo: contarla como congestion hacia oscilar inmediato <-> solo, corrida C)
        ahora = self.reloj()
        congestion = any(ahora - t0 > CONGESTION_S for t0 in self.esperando_cupo.values())
        if (self.modo == "inmediato" and activos and self.carga_min <= self.inmediato_max
                and not congestion):
            nivel, params = "inmediato", (1, 0.0)
        elif solo or (self.modo == "inmediato" and (congestion or self.carga_min > self.apretado_max)):
            nivel, params = "solo", self._lote_solo
        else:
            nivel, params = "lote", self._lote_normal
        if (self.lotes.maximo, self.lotes.ventana_s) != params:
            self.lotes.maximo, self.lotes.ventana_s = params
            self.logger(f"[traductor] lotes: {nivel} {params[0]} textos / {params[1]:g} s (carga min "
                        f"{self.carga_min} en 60 s; activos: {','.join(activos) or 'ninguno'})")
        self.nivel = nivel

    def agregar(self, seq: int, text: str, t: float) -> None:
        self._ajustar_lotes()
        self.textos_por_nivel[self.nivel] = self.textos_por_nivel.get(self.nivel, 0) + 1
        for lote in self.lotes.agregar(seq, text, t):
            self._poner(lote)
            if self.modo == "inmediato":
                self.carga_min += 1       # hasta el proximo refresco del vigia

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

    def tic(self, ahora: float) -> None:
        """Un paso del vigia (sin E/S): ajusta el nivel y despacha el lote vencido."""
        self._ajustar_lotes()
        lote = self.lotes.vencido(ahora)
        if lote:
            self._poner(lote)

    async def _vigilar(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            if self.modo == "inmediato":
                try:
                    await asyncio.to_thread(self.actualizar_carga)
                except Exception as e:
                    self.logger(f"[traductor] carga: {type(e).__name__}: {e}")
            self.tic(self.reloj())

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
        lote = self.lotes.vaciar(self.reloj())
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
