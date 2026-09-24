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
- Timeout 3 s por llamada. 429/timeout/error => backoff exponencial por modelo hasta 30 s.
- Contador: una linea por llamada en reportes/cuota-texto.log: hora | modelo | items | estado | ms

    python -m worker.traductor --listar                        # ids exactos (models.list)
    python -m worker.traductor --probar "Hello world" --a es   # UNA llamada real (gasta 1 RPD)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

RAIZ = Path(__file__).resolve().parent.parent
LOG_TEXTO = Path(os.environ.get("CUOTA_TEXTO_LOG", RAIZ / "reportes" / "cuota-texto.log"))
LOTE_MAX = 3
LOTE_S = 8.0
TIMEOUT_S = 3.0
RPM = 12
CAPACIDAD = 2
RPD_TOPE = int(os.environ.get("TRADUCTOR_RPD_TOPE", 480))
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
FMT = "%Y-%m-%d %H:%M:%S"
IDIOMAS = {"en": "English", "es": "Spanish"}


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
            if getattr(e, "code", None) == 429:
                raise Error429(str(e)[:200]) from e
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
    return arr


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


class Limitador:
    """Token bucket por modelo (rpm/min, capacidad `capacidad`) + backoff + tope diario."""

    def __init__(self, modelos: list[str], rpm: float = RPM, capacidad: float = CAPACIDAD,
                 rpd_tope: int = RPD_TOPE, log: Optional[Path] = LOG_TEXTO,
                 reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.rpm = rpm
        self.rate = rpm / 60.0
        self.cap = capacidad
        self.rpd_tope = rpd_tope
        self.log = log
        self.reloj = reloj
        self.dormir = dormir
        t = reloj()
        self.m = {n: EstadoModelo(n, capacidad, t) for n in modelos}
        self.orden = list(modelos)
        self._rr = 0
        if log is not None:
            d0 = inicio_dia()
            for dt, mod, _est in leer_log(log):
                if dt >= d0 and mod in self.m:
                    self.m[mod].hoy += 1

    def _recargar(self, e: EstadoModelo, t: float) -> None:
        e.tokens = min(self.cap, e.tokens + (t - e.t_ref) * self.rate)
        e.t_ref = t

    def _recientes_en_log(self, modelo: str) -> int:
        if self.log is None:
            return 0
        lim = datetime.now() - timedelta(seconds=60)
        return sum(1 for dt, mod, _ in leer_log(self.log)[-40:] if mod == modelo and dt >= lim)

    def espera(self, modelo: str) -> float:
        """Segundos hasta que `modelo` pueda llamar (inf si agoto el tope diario)."""
        e = self.m[modelo]
        if e.hoy >= self.rpd_tope:
            return float("inf")
        t = self.reloj()
        self._recargar(e, t)
        w = max(0.0, e.bloqueado_hasta - t)
        if e.tokens < 1:
            w = max(w, (1 - e.tokens) / self.rate)
        return w

    async def tomar(self, evitar: Optional[str] = None, espera_max: float = 60.0) -> Optional[str]:
        """Modelo a usar (consume un token) o None si ninguno queda libre dentro de espera_max."""
        t_lim = self.reloj() + espera_max
        while True:
            rot = self.orden[self._rr:] + self.orden[:self._rr]
            cands = [n for n in rot if n != evitar] + [n for n in rot if n == evitar]
            esperas = [(self.espera(n), i, n) for i, n in enumerate(cands)]
            w, _, n = min(esperas)
            if w == float("inf"):
                return None
            if w <= 0 and self._recientes_en_log(n) < self.rpm:
                e = self.m[n]
                e.tokens -= 1
                e.hoy += 1
                self._rr = (self.orden.index(n) + 1) % len(self.orden)
                return n
            w = max(w, 0.5)
            if self.reloj() + w > t_lim:
                return None
            await self.dormir(w)

    def exito(self, modelo: str) -> None:
        self.m[modelo].backoff_s = 0.0

    def fallo(self, modelo: str) -> float:
        e = self.m[modelo]
        e.backoff_s = min(BACKOFF_MAX, max(BACKOFF_MIN, e.backoff_s * 2))
        e.bloqueado_hasta = self.reloj() + e.backoff_s
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


class Traductor:
    def __init__(self, de: str, a: str, transporte: Optional[TransporteTexto] = None,
                 modelos: Optional[list[str]] = None, limitador: Optional[Limitador] = None,
                 log: Optional[Path] = LOG_TEXTO, timeout_s: float = TIMEOUT_S,
                 lotes: Optional[Lotes] = None, espera_cupo_s: float = 2 * LOTE_S,
                 reintentos: int = 1,
                 on_resultado: Optional[Callable[[ResultadoLote], None]] = None,
                 logger: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True)):
        self.de, self.a = de, a
        self.tr = transporte if transporte is not None else TransporteGenAI()
        self.modelos = modelos or modelos_por_defecto()
        self.lim = limitador or Limitador(self.modelos, log=log)
        self.log = log
        self.timeout_s = timeout_s
        self.lotes = lotes or Lotes()
        self.espera_cupo_s = espera_cupo_s
        self.reintentos = reintentos
        self.on_resultado = on_resultado
        self.logger = logger
        self.n_llamadas = 0
        self.n_lotes = 0
        self.n_ok_false = 0
        self.por_modelo: dict[str, int] = {}
        self._cola: Optional[asyncio.Queue] = None
        self._tareas: list[asyncio.Task] = []
        self._en_vuelo = None

    def _anotar(self, modelo: str, n: int, estado: str, ms: int) -> None:
        self.n_llamadas += 1
        self.por_modelo[modelo] = self.por_modelo.get(modelo, 0) + 1
        if self.log is None:
            return
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime(FMT)} | {modelo} | {n} | {estado} | {ms}\n")

    async def traducir(self, textos: list[str], seqs: Optional[list[int]] = None) -> ResultadoLote:
        """UNA llamada por lote (+1 reintento). Nunca levanta excepcion."""
        seqs = seqs if seqs is not None else list(range(len(textos)))
        prompt = prompt_lote(textos, self.de, self.a)
        intentos: list[dict] = []
        t_lote = time.monotonic()
        previo = None
        for _ in range(1 + self.reintentos):
            modelo = await self.lim.tomar(evitar=previo, espera_max=self.espera_cupo_s)
            if modelo is None:
                intentos.append({"modelo": None, "estado": "sin_cupo", "ms": 0})
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
            except Error429:
                ms, estado = int((time.monotonic() - t0) * 1000), "429"
            except Exception as e:
                ms, estado = int((time.monotonic() - t0) * 1000), f"error:{type(e).__name__}"
            self._anotar(modelo, len(textos), estado, ms)
            espera = self.lim.fallo(modelo) if estado != "cantidad" else 0.0
            intentos.append({"modelo": modelo, "estado": estado, "ms": ms, "backoff_s": espera})
            self.logger(f"[traductor] {modelo} {estado} en {ms} ms (lote de {len(textos)})")
            previo = modelo
        self.n_ok_false += 1
        return ResultadoLote([{"seq": s, "text": None, "ok": False} for s in seqs],
                             intentos[-1]["modelo"] if intentos else None,
                             int((time.monotonic() - t_lote) * 1000), intentos)

    # -- modo vivo (SessionWorker) --
    def agregar(self, seq: int, text: str, t: float) -> None:
        for lote in self.lotes.agregar(seq, text, t):
            self._poner(lote)

    def _poner(self, lote) -> None:
        if self._cola is None:
            self._cola = asyncio.Queue()
        self._cola.put_nowait(lote)

    async def _vigilar(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            lote = self.lotes.vencido(time.time())
            if lote:
                self._poner(lote)

    async def _consumir(self) -> None:
        if self._cola is None:
            self._cola = asyncio.Queue()
        while True:
            lote = await self._cola.get()
            if lote is None:
                return
            try:
                self.n_lotes += 1
                self._en_vuelo = lote
                res = await self.traducir([i.text for i in lote.items], [i.seq for i in lote.items])
                self._en_vuelo = None
                if self.on_resultado:
                    self.on_resultado(res)
            except Exception as e:     # nunca se cae el worker
                self.logger(f"[traductor] error inesperado: {type(e).__name__}: {e}")

    def _abandonar(self) -> int:
        """Al cerrar por timeout: lo que quedo sin traducir sale MARCADO (ok:false), no mudo."""
        lotes = [self._en_vuelo] if getattr(self, "_en_vuelo", None) else []
        while self._cola is not None and not self._cola.empty():
            x = self._cola.get_nowait()
            if x is not None:
                lotes.append(x)
        for lote in lotes:
            self.n_ok_false += 1
            res = ResultadoLote([{"seq": i.seq, "text": None, "ok": False} for i in lote.items], None, 0,
                                [{"modelo": None, "estado": "cierre", "ms": 0}])
            if self.on_resultado:
                self.on_resultado(res)
        self._en_vuelo = None
        return len(lotes)

    def iniciar(self) -> None:
        if not self._tareas:
            self._tareas = [asyncio.create_task(self._vigilar()), asyncio.create_task(self._consumir())]

    async def cerrar(self, timeout: float = 15.0) -> None:
        """Manda el lote pendiente y espera las traducciones en vuelo (hasta timeout)."""
        lote = self.lotes.vaciar(time.time())
        if lote:
            self._poner(lote)
        self._poner(None)
        if self._tareas:
            try:
                await asyncio.wait_for(asyncio.shield(self._tareas[1]), timeout)
            except (asyncio.TimeoutError, Exception):
                pass
            for t in self._tareas:
                t.cancel()
            if not self._tareas[1].done() or getattr(self, "_en_vuelo", None):
                await asyncio.sleep(0)
            self._abandonar()


def meta_traduccion(res: ResultadoLote, lang_to: str, source=None) -> dict:
    # el contrato exige model string: sin llamada exitosa ni intento (sin cupo, cierre) va "ninguno"
    m = {"lang_to": lang_to, "model": res.modelo or "ninguno", "batch_ms": int(res.ms)}
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
