"""ops/control.py -- servicio HTTP de control: crear/arrancar/parar salas desde el panel, SIN
terminal (pedido de Ricardo). Reutiliza la lógica de `ops/salas.py` (Sala, comando_worker,
enviar_parada/detener_proceso) para lanzar un `worker.run` por sala, con reinicio automático con
backoff si el proceso muere solo, logs por sala y parada limpia.

Uso:
    python -m ops.control --hub ws://localhost:8100/ingest --port 8110 [--host 127.0.0.1] \
        [--transporte casete:fixtures/casetes/evidencia-25-09/simple-en-053454.jsonl] \
        [--persist ops/salas.control.json]

Con `--transporte casete:<archivo.jsonl>` cada sala usa el transporte de test rotulado de
worker/transporte_casete.py (skill cuota-gemini): 0 llamadas a la API de Gemini.

CONTRATO (todas las rutas exigen `Authorization: Bearer <HUB_TOKEN>`, el MISMO token del hub, salvo
`GET /api/control/salud`; 401 sin token; comparación en tiempo constante con `hmac.compare_digest`):

    GET    /api/control/fuentes                -> {"microfonos": [...], "archivos": [...], "keys": [...],
                                                     "idiomas": [{"codigo","nombre_es","nombre_en","probado"}]}
    GET    /api/control/salas                  -> {"salas": [ {...} ]}
    POST   /api/control/salas                  -> 201 con la sala creada (400/409 si no valida)
    POST   /api/control/salas/{id}/iniciar     -> 200 con la sala
    POST   /api/control/salas/{id}/detener     -> 200 con la sala
    DELETE /api/control/salas/{id}             -> 204
    GET    /api/control/salud (sin auth)        -> {"ok": true, "salas": N}
    GET    /api/control/audio/<nombre>.wav (sin auth) -> el .wav SI Y SÓLO SI está en
        fixtures/audio/clips/ (allowlist exacta, sin '/', '..' ni '\\'; 404 para todo lo demás),
        `Content-Type: audio/wav`, con soporte de Range (addendum: comparar audio vs. transcripción).

Cada sala de GET /api/control/salas suma "audio_inicio" (epoch s en que el worker empezó a entregar
audio de la fuente, leído de la línea "[run] fuente archivo|url|mic:" de su log; null si no arrancó
o no se vio esa línea todavía) y "video_origen" ({"url","inicio_s"} si el clip es un recorte
conocido citado en fixtures/agenda.json, por nombre de archivo; null si no se puede inferir).

`lang` acepta {en, es, pt, fr, de, it} (sólo en/es están PROBADOS en este proyecto, ver
worker/README.md; el resto queda declarado con `probado:false` en /api/control/fuentes).
`traducir_a` acepta "auto", "none" o una lista separada por comas de esos códigos, cada uno
distinto de `lang` y sin repetidos (p.ej. "es,pt,fr"); se pasa TAL CUAL a `worker.run --traducir-a`
(addendum aditivo aprobado por Ricardo, no reemplaza el es/en original).

CORS: `Access-Control-Allow-Origin` = el Origin de la request si es http://localhost:*,
http://127.0.0.1:* o el mismo host del propio servicio; `Authorization, Content-Type` permitidos;
métodos GET, POST, DELETE, OPTIONS.

Persistencia: las DEFINICIONES de sala (no el estado de proceso) se guardan en `ops/salas.control.json`
(gitignored) para recordarlas entre reinicios de este servicio; al arrancar, todas quedan "detenida"
(los procesos del reinicio anterior ya no existen).

Tope: 20 salas (409 al superarlo). Nunca shell: `subprocess.Popen` con lista de args (nunca `shell=True`).
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

from ops.salas import ID_RE, TIPOS_FUENTE, Sala, comando_worker, detener_proceso, enviar_parada

RAIZ = Path(__file__).resolve().parent.parent
PERSIST_PATH_DEFAULT = RAIZ / "ops" / "salas.control.json"
CLIPS_DIR = RAIZ / "fixtures" / "audio" / "clips"
TOPE_SALAS = 20
LOG_TAIL_N = 5
REINTENTOS_MAX = 5
BACKOFF_INICIAL_S = 1.0
BACKOFF_MAX_S = 30.0

KEY_RE = re.compile(r"^GEMINI_API_KEY")
URL_RE = re.compile(r"^(udp|srt|rtmp|rtmps|http|https)://[^\s;|$&()`<>'\"]+$")
ORIGEN_LOCAL_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")

# Addendum aprobado por Ricardo (aditivo, no reemplaza lo anterior): lang y traducir_a cubren 6
# idiomas; sólo en/es están PROBADOS en este proyecto (worker/README.md), el resto queda declarado
# para cuando se prueben (`probado: false` en /api/control/fuentes, campo "idiomas").
LANGS_VALIDOS = ("en", "es", "pt", "fr", "de", "it")
IDIOMAS = [
    {"codigo": "en", "nombre_es": "inglés", "nombre_en": "English", "probado": True},
    {"codigo": "es", "nombre_es": "español", "nombre_en": "Spanish", "probado": True},
    {"codigo": "pt", "nombre_es": "portugués", "nombre_en": "Portuguese", "probado": False},
    {"codigo": "fr", "nombre_es": "francés", "nombre_en": "French", "probado": False},
    {"codigo": "de", "nombre_es": "alemán", "nombre_en": "German", "probado": False},
    {"codigo": "it", "nombre_es": "italiano", "nombre_en": "Italian", "probado": False},
]

FUENTE_LOG_RE = re.compile(r"^\[run\] fuente (archivo|url|mic):")
AGENDA_PATH = RAIZ / "fixtures" / "agenda.json"
CLIP_INICIO_RE = re.compile(r"-(\d+)s-\d+s\.wav$", re.IGNORECASE)

log = logging.getLogger("ops.control")


# --------------------------------------------------------------------------------- detección de fuentes
def _cargar_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv es opcional
        return
    load_dotenv(RAIZ / ".env", override=False)


def detectar_keys() -> list[str]:
    """NOMBRES de variables GEMINI_API_KEY* definidas en el entorno o en .env; NUNCA valores."""
    _cargar_env()
    return sorted(k for k in os.environ if KEY_RE.match(k))


def detectar_archivos() -> list[str]:
    """fixtures/audio/clips/*.wav, ruta relativa a la raíz con '/' (nada fuera de fixtures/audio)."""
    if not CLIPS_DIR.is_dir():
        return []
    return sorted(
        str(p.relative_to(RAIZ)).replace("\\", "/") for p in CLIPS_DIR.glob("*.wav")
    )


_AGENDA_CACHE: dict[str, str] | None = None


def _agenda_urls() -> dict[str, str]:
    """{palabra clave (id o apellido del orador, en minúsculas): url} desde fixtures/agenda.json,
    para inferir el video de origen de un clip conocido (addendum: booch/paez)."""
    global _AGENDA_CACHE
    if _AGENDA_CACHE is not None:
        return _AGENDA_CACHE
    urls: dict[str, str] = {}
    try:
        datos = json.loads(AGENDA_PATH.read_text(encoding="utf-8"))
        for s in datos.get("sesiones", []) if isinstance(datos, dict) else []:
            url = s.get("url")
            if not url:
                continue
            sid = str(s.get("id") or "")
            if sid:
                urls[sid.split("-")[0].lower()] = url
            orador = str(s.get("orador") or "").strip().split()
            if orador:
                urls.setdefault(orador[-1].lower(), url)
    except (OSError, ValueError):
        pass
    _AGENDA_CACHE = urls
    return urls


def video_origen_de(fuente: dict) -> dict | None:
    """{"url", "inicio_s"} si el clip de la sala (fuente archivo) es un recorte conocido de un
    video de fixtures/agenda.json (nombre `..-<clave>-<inicio>s-<dur>s.wav`); None si no se puede
    inferir (addendum de Ricardo, no cubre --fuente url/mic)."""
    if not isinstance(fuente, dict) or fuente.get("tipo") != "archivo":
        return None
    valor = fuente.get("valor") or ""
    nombre = Path(str(valor)).name.lower()
    m = CLIP_INICIO_RE.search(nombre)
    if not m:
        return None
    inicio_s = int(m.group(1))
    for clave, url in _agenda_urls().items():
        if clave and clave in nombre:
            return {"url": url, "inicio_s": inicio_s}
    return None


_MIC_CACHE: dict = {"t": 0.0, "valor": []}
MIC_CACHE_S = 30.0


def _listar_microfonos_windows() -> list[str]:
    try:
        r = subprocess.run(
            ["ffmpeg", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, timeout=10,  # bytes crudos: ffmpeg imprime los nombres dshow en UTF-8
        )                                     # (medido 25/09; con cp1252 salia MicrÃ³fono)
    except (OSError, subprocess.TimeoutExpired):
        return []                                
    crudo = (r.stderr or b"") + (r.stdout or b"")
    try:
        salida = crudo.decode("utf-8")          # ffmpeg en Windows imprime los nombres dshow en UTF-8
    except UnicodeDecodeError:
        salida = crudo.decode("cp1252", errors="replace")
    return [m.group(1) for m in re.finditer(r'"([^"]+)"\s*\(audio\)', salida)]


def _listar_microfonos_linux() -> list[str]:
    try:
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    nombres = []
    for linea in (r.stdout or "").splitlines():
        m = re.match(r"^card \d+: (.+?),", linea)
        if m:
            nombres.append(m.group(1))
    return nombres


def detectar_microfonos(forzar: bool = False) -> list[str]:
    """Cacheado 30 s: `ffmpeg -list_devices` (Windows) tarda y no cambia seguido."""
    ahora = time.time()
    if not forzar and ahora - _MIC_CACHE["t"] < MIC_CACHE_S:
        return _MIC_CACHE["valor"]
    valor = _listar_microfonos_windows() if os.name == "nt" else _listar_microfonos_linux()
    _MIC_CACHE["t"] = ahora
    _MIC_CACHE["valor"] = valor
    return valor


# --------------------------------------------------------------------------------- ciclo de vida de una sala
@dataclass
class SalaDef:
    id: str
    titulo: str
    lang: str
    traducir_a: str
    fuente: dict
    key: str
    duracion_s: float | None = None

    def a_dict(self) -> dict:
        return {
            "id": self.id, "titulo": self.titulo, "lang": self.lang, "traducir_a": self.traducir_a,
            "fuente": self.fuente, "key": self.key, "duracion_s": self.duracion_s,
        }


class SalaProceso:
    """Un `worker.run` por sala: iniciar()/detener() individuales, reinicio con backoff si el
    proceso muere solo (no si lo para un DELETE/detener explícito), log por sala en
    $TEMP/ops-control-logs/<id>.log."""

    def __init__(self, d: SalaDef, *, hub: str, transporte: str | None, python: str):
        self.d = d
        self.hub = hub
        self.transporte = transporte
        self.python = python
        self.estado = "detenida"
        self.pid: int | None = None
        self.intentos = 0
        self.inicio: float | None = None
        self.ultimo_error: str | None = None
        self.audio_inicio: float | None = None
        self._proc: subprocess.Popen | None = None
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()
        self._parar_evt = threading.Event()

    def _log_dir(self) -> Path:
        d = Path(os.environ.get("TEMP", "/tmp")) / "ops-control-logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _log_path(self) -> Path:
        return self._log_dir() / f"{self.d.id}.log"

    def log_tail(self, n: int = LOG_TAIL_N) -> list[str]:
        p = self._log_path()
        if not p.exists():
            return []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()
        except OSError:
            return []
        return [l.rstrip("\n") for l in lineas[-n:]]

    def iniciar(self) -> None:
        with self._lock:
            if self.estado in ("arrancando", "corriendo", "reiniciando"):
                return
            self._parar_evt.clear()
            self.estado = "arrancando"
            self.intentos = 0
            self.ultimo_error = None
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def _leer_salida(self, proc: subprocess.Popen, f) -> None:
        """Copia stdout+stderr del hijo al log y detecta '[run] fuente archivo|url|mic:' para
        marcar `audio_inicio` (addendum de Ricardo: comparar audio contra transcripción)."""
        try:
            for linea in proc.stdout:
                f.write(linea)
                f.flush()
                if self.audio_inicio is None and FUENTE_LOG_RE.match(linea.strip()):
                    with self._lock:
                        if self.audio_inicio is None:
                            self.audio_inicio = time.time()
        except (ValueError, OSError):
            pass

    def _bucle(self) -> None:
        backoff = BACKOFF_INICIAL_S
        while not self._parar_evt.is_set():
            self.intentos += 1
            self.audio_inicio = None
            sala = Sala(id=self.d.id, titulo=self.d.titulo, lang=self.d.lang, fuente=self.d.fuente,
                        key=self.d.key)
            cmd = comando_worker(sala, hub=self.hub, transporte=self.transporte, python=self.python,
                                  traducir_a=self.d.traducir_a, duracion_s=self.d.duracion_s)
            log_path = self._log_path()
            try:
                f = open(log_path, "a", encoding="utf-8")
            except OSError as e:
                self.ultimo_error = str(e)
                self.estado = "error"
                return
            with f:
                f.write(f"\n--- intento {self.intentos} {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                f.write("$ " + " ".join(cmd) + "\n")
                f.flush()
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                try:
                    proc = subprocess.Popen(cmd, cwd=str(RAIZ), env={**os.environ, "CUOTA_GUARDA": "0"}, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                             errors="replace", bufsize=1,
                                             creationflags=creationflags)
                except OSError as e:
                    self.ultimo_error = str(e)
                    self.estado = "error"
                    return
                with self._lock:
                    if self._parar_evt.is_set():
                        enviar_parada(proc)
                        self.estado = "detenida"
                        proc.wait(timeout=10)
                        return
                    self._proc = proc
                    self.pid = proc.pid
                    self.inicio = time.time()
                    self.estado = "corriendo"
                lector = threading.Thread(target=self._leer_salida, args=(proc, f), daemon=True)
                lector.start()
                codigo = proc.wait()
                lector.join(timeout=5)
            with self._lock:
                self._proc = None
                self.pid = None
            if self._parar_evt.is_set():
                self.estado = "detenida"
                return
            if codigo == 0:
                self.estado = "detenida"
                return
            self.ultimo_error = f"exit {codigo}"
            if self.intentos >= REINTENTOS_MAX:
                self.estado = "error"
                return
            self.estado = "reiniciando"
            if self._parar_evt.wait(backoff):
                self.estado = "detenida"
                return
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    def detener(self, timeout_s: float = 10.0) -> None:
        self._parar_evt.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            detener_proceso(proc, timeout_s=timeout_s)
        hilo = self._hilo
        if hilo is not None:
            hilo.join(timeout=timeout_s + 2)
        with self._lock:
            self._proc = None
            self.pid = None
        self.estado = "detenida"

    def a_dict(self) -> dict:
        return {
            **self.d.a_dict(),
            "estado": self.estado,
            "pid": self.pid,
            "intentos": self.intentos,
            "inicio": self.inicio,
            "ultimo_error": self.ultimo_error,
            "log_tail": self.log_tail(),
            "audio_inicio": self.audio_inicio,
            "video_origen": video_origen_de(self.d.fuente),
        }


class Control:
    """Estado en memoria de todas las salas + persistencia de sus DEFINICIONES en un JSON."""

    def __init__(self, *, hub: str, transporte: str | None, python: str,
                 persist_path: Path = PERSIST_PATH_DEFAULT):
        self.hub = hub
        self.transporte = transporte
        self.python = python
        self.persist_path = persist_path
        self.salas: dict[str, SalaProceso] = {}
        self._lock = threading.Lock()
        self._cargar()

    def _cargar(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            datos = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("no se pudo leer %s: se arranca sin salas persistidas", self.persist_path)
            return
        if not isinstance(datos, list):
            return
        for d in datos:
            try:
                sd = SalaDef(
                    id=d["id"], titulo=d.get("titulo", d["id"]), lang=d["lang"],
                    traducir_a=d.get("traducir_a", "auto"), fuente=d["fuente"], key=d["key"],
                    duracion_s=d.get("duracion_s"),
                )
            except (KeyError, TypeError):
                continue
            self.salas[sd.id] = SalaProceso(sd, hub=self.hub, transporte=self.transporte,
                                             python=self.python)
        if self.salas:
            log.info("%d sala(s) recordada(s) desde %s (detenidas: hay que iniciarlas)",
                      len(self.salas), self.persist_path)

    def _guardar(self) -> None:
        datos = [sp.d.a_dict() for sp in self.salas.values()]
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.persist_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.persist_path)

    def crear(self, sd: SalaDef, *, arrancar: bool) -> SalaProceso | None:
        with self._lock:
            if sd.id in self.salas:
                return None
            if len(self.salas) >= TOPE_SALAS:
                return None
            sp = SalaProceso(sd, hub=self.hub, transporte=self.transporte, python=self.python)
            self.salas[sd.id] = sp
            self._guardar()
        if arrancar:
            sp.iniciar()
        return sp

    def obtener(self, sid: str) -> SalaProceso | None:
        with self._lock:
            return self.salas.get(sid)

    def listar(self) -> list[SalaProceso]:
        with self._lock:
            return list(self.salas.values())

    def eliminar(self, sid: str) -> bool:
        with self._lock:
            sp = self.salas.pop(sid, None)
            if sp is not None:
                self._guardar()
        if sp is None:
            return False
        sp.detener()
        return True


CTRL_KEY: web.AppKey[Control] = web.AppKey("ctrl", Control)


# --------------------------------------------------------------------------------- auth + CORS
def _token_actual() -> str:
    _cargar_env()
    return os.environ.get("HUB_TOKEN", "")


def _bearer_ok(request: web.Request) -> bool:
    tipo, _, valor = request.headers.get("Authorization", "").partition(" ")
    if tipo.lower() != "bearer":
        return False
    esperado = _token_actual()
    if not esperado:
        return False
    return hmac.compare_digest(valor.strip().encode(), esperado.encode())


def _origen_permitido(origen: str, request: web.Request) -> bool:
    if not origen:
        return False
    if ORIGEN_LOCAL_RE.match(origen):
        return True
    try:
        o = urlparse(origen)
    except ValueError:
        return False
    return bool(o.hostname) and o.hostname == request.url.host


PUBLICO = {"/api/control/salud"}
AUDIO_PREFIX = "/api/control/audio/"


def _es_publico(path: str) -> bool:
    return path in PUBLICO or path.startswith(AUDIO_PREFIX)


@web.middleware
async def cors_mw(request: web.Request, handler):
    origen = request.headers.get("Origin", "")
    permitido = _origen_permitido(origen, request)
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            if permitido:
                e.headers["Access-Control-Allow-Origin"] = origen
                e.headers["Vary"] = "Origin"
            raise
    if permitido:
        resp.headers["Access-Control-Allow-Origin"] = origen
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Max-Age"] = "600"
    return resp


@web.middleware
async def auth_mw(request: web.Request, handler):
    if request.method == "OPTIONS" or _es_publico(request.path):
        return await handler(request)
    if not _bearer_ok(request):
        resp = _error(401, "falta Authorization: Bearer <HUB_TOKEN>")
        resp.headers["WWW-Authenticate"] = "Bearer"
        return resp
    return await handler(request)


# --------------------------------------------------------------------------------- handlers
def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(status: int, mensaje: str) -> web.Response:
    return web.json_response({"error": mensaje}, status=status)


async def h_fuentes(request: web.Request) -> web.Response:
    return _json({
        "microfonos": detectar_microfonos(),
        "archivos": detectar_archivos(),
        "keys": detectar_keys(),
        "idiomas": IDIOMAS,
    })


def _validar_traducir_a(valor: object, lang: str) -> tuple[bool, str | None]:
    """'auto' | 'none' | lista separada por comas de códigos de LANGS_VALIDOS, cada uno distinto de
    `lang` y sin repetidos. Se pasa TAL CUAL a `worker.run --traducir-a` (addendum de Ricardo)."""
    if not isinstance(valor, str) or not valor:
        return False, None
    if valor in ("auto", "none"):
        return True, valor
    codigos = [c.strip() for c in valor.split(",")]
    if any(not c for c in codigos):
        return False, None
    if len(set(codigos)) != len(codigos):
        return False, None
    for c in codigos:
        if c not in LANGS_VALIDOS or c == lang:
            return False, None
    return True, ",".join(codigos)


async def h_listar_salas(request: web.Request) -> web.Response:
    ctrl: Control = request.app[CTRL_KEY]
    return _json({"salas": [sp.a_dict() for sp in ctrl.listar()]})


def _validar_cuerpo(body) -> tuple[SalaDef | None, bool, web.Response | None]:
    if not isinstance(body, dict):
        return None, False, _error(400, "cuerpo inválido: se espera un objeto JSON")
    sid = body.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        return None, False, _error(400, "id inválido: tiene que matchear ^[a-z0-9][a-z0-9_-]{0,63}$")
    lang = body.get("lang")
    if lang not in LANGS_VALIDOS:
        return None, False, _error(400, f"lang debe ser uno de {LANGS_VALIDOS}")
    ok_traducir, traducir_a = _validar_traducir_a(body.get("traducir_a", "auto"), lang)
    if not ok_traducir:
        return None, False, _error(
            400, f"traducir_a debe ser 'auto', 'none' o una lista separada por comas de "
                 f"{LANGS_VALIDOS} distintos de lang y sin repetidos")
    fuente = body.get("fuente")
    if not isinstance(fuente, dict) or fuente.get("tipo") not in TIPOS_FUENTE:
        return None, False, _error(400, f"fuente.tipo debe ser uno de {TIPOS_FUENTE}")
    tipo = fuente["tipo"]
    valor = fuente.get("valor")
    if not isinstance(valor, str) or not valor:
        return None, False, _error(400, "fuente.valor vacío")
    if tipo == "mic":
        if valor not in detectar_microfonos():
            return None, False, _error(400, f"micrófono desconocido: {valor!r}")
    elif tipo == "archivo":
        if valor not in detectar_archivos():
            return None, False, _error(400, f"archivo fuera de fixtures/audio/clips: {valor!r}")
    elif tipo == "url":
        if " " in valor or not URL_RE.match(valor):
            return None, False, _error(
                400, "url inválida: esquema udp|srt|rtmp|rtmps|http|https, sin espacios")
    key = body.get("key")
    if not isinstance(key, str) or key not in detectar_keys():
        return None, False, _error(400, f"key desconocida: {key!r}")
    duracion_s = body.get("duracion_s")
    if duracion_s is not None and not isinstance(duracion_s, (int, float)):
        return None, False, _error(400, "duracion_s debe ser número o null")
    titulo = body.get("titulo") or sid
    if not isinstance(titulo, str):
        return None, False, _error(400, "titulo debe ser texto")
    arrancar = body.get("arrancar", True)
    if not isinstance(arrancar, bool):
        return None, False, _error(400, "arrancar debe ser booleano")
    sd = SalaDef(id=sid, titulo=titulo, lang=lang, traducir_a=traducir_a, fuente=fuente, key=key,
                 duracion_s=duracion_s)
    return sd, arrancar, None


async def h_crear_sala(request: web.Request) -> web.Response:
    ctrl: Control = request.app[CTRL_KEY]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _error(400, "JSON inválido")
    sd, arrancar, err = _validar_cuerpo(body)
    if err is not None:
        return err
    if ctrl.obtener(sd.id) is not None:
        return _error(409, f"id ya existe: {sd.id}")
    if len(ctrl.listar()) >= TOPE_SALAS:
        return _error(409, f"tope de {TOPE_SALAS} salas alcanzado")
    sp = ctrl.crear(sd, arrancar=arrancar)
    if sp is None:
        # se coló una carrera entre el chequeo y la creación: mismo resultado, 409
        return _error(409, f"id ya existe o tope de {TOPE_SALAS} salas alcanzado")
    return _json(sp.a_dict(), status=201)


async def h_iniciar(request: web.Request) -> web.Response:
    ctrl: Control = request.app[CTRL_KEY]
    sp = ctrl.obtener(request.match_info["id"])
    if sp is None:
        return _error(404, f"sala desconocida: {request.match_info['id']}")
    sp.iniciar()
    return _json(sp.a_dict())


async def h_detener(request: web.Request) -> web.Response:
    ctrl: Control = request.app[CTRL_KEY]
    sp = ctrl.obtener(request.match_info["id"])
    if sp is None:
        return _error(404, f"sala desconocida: {request.match_info['id']}")
    await asyncio.get_running_loop().run_in_executor(None, sp.detener)
    return _json(sp.a_dict())


async def h_eliminar(request: web.Request) -> web.Response:
    ctrl: Control = request.app[CTRL_KEY]
    sid = request.match_info["id"]
    ok = await asyncio.get_running_loop().run_in_executor(None, ctrl.eliminar, sid)
    if not ok:
        return _error(404, f"sala desconocida: {sid}")
    return web.Response(status=204)


async def h_salud(request: web.Request) -> web.Response:
    ctrl: Control = request.app[CTRL_KEY]
    return _json({"ok": True, "salas": len(ctrl.listar())})


async def h_audio(request: web.Request) -> web.Response:
    """SIN auth (addendum: los clips son públicos en el repo): sólo sirve nombres EXACTOS de la
    allowlist fixtures/audio/clips/*.wav; nada con '/', '..' o '\\' pasa nunca de acá."""
    nombre = request.match_info["nombre"]
    if not nombre or "/" in nombre or "\\" in nombre or ".." in nombre:
        return _error(404, "no encontrado")
    permitidos = {Path(a).name for a in detectar_archivos()}
    if nombre not in permitidos:
        return _error(404, "no encontrado")
    ruta = CLIPS_DIR / nombre
    if not ruta.is_file():
        return _error(404, "no encontrado")
    resp = web.FileResponse(ruta)
    resp.content_type = "audio/wav"
    return resp


# --------------------------------------------------------------------------------- app + CLI
def crear_app(*, hub: str, transporte: str | None = None, python: str = sys.executable,
              persist_path: Path = PERSIST_PATH_DEFAULT) -> web.Application:
    app = web.Application(middlewares=[cors_mw, auth_mw])
    app[CTRL_KEY] = Control(hub=hub, transporte=transporte, python=python, persist_path=persist_path)
    app.router.add_get("/api/control/fuentes", h_fuentes)
    app.router.add_get("/api/control/salas", h_listar_salas)
    app.router.add_post("/api/control/salas", h_crear_sala)
    app.router.add_post("/api/control/salas/{id}/iniciar", h_iniciar)
    app.router.add_post("/api/control/salas/{id}/detener", h_detener)
    app.router.add_delete("/api/control/salas/{id}", h_eliminar)
    app.router.add_get("/api/control/salud", h_salud)
    app.router.add_get("/api/control/audio/{nombre}", h_audio)
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m ops.control",
        description="Servicio de control: crear/arrancar/parar salas desde el panel, sin terminal.",
    )
    ap.add_argument("--hub", required=True, help="ws://host:puerto/ingest, se pasa a cada worker.run")
    ap.add_argument("--port", type=int, default=8110)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--transporte", default=None,
                     help="se agrega TAL CUAL a --transporte de CADA worker.run (p.ej. "
                          "casete:archivo.jsonl), para probar sin API")
    ap.add_argument("--python", default=sys.executable,
                     help="intérprete para 'python -m worker.run' (default: el mismo de este proceso)")
    ap.add_argument("--persist", default=str(PERSIST_PATH_DEFAULT),
                     help="ruta del JSON de persistencia (default ops/salas.control.json)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                         datefmt="%H:%M:%S")
    if not _token_actual():
        log.warning("HUB_TOKEN no está definido (ni en el entorno ni en .env): todas las rutas con "
                    "auth van a devolver 401.")

    app = crear_app(hub=args.hub, transporte=args.transporte, python=args.python,
                     persist_path=Path(args.persist))
    log.info("ops.control escuchando en http://%s:%d (hub=%s, transporte=%s)",
              args.host, args.port, args.hub, args.transporte or "gemini")
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
