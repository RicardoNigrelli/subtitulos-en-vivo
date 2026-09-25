"""Ingesta: archivo / URL / dispositivo -> PCM s16le 16 kHz mono en chunks de 100 ms (R17a).

ffmpeg hace la conversion (skill ingesta). NO se normaliza el audio (skill gemini-live).
La fuente se alimenta a VELOCIDAD REAL: el chunk i sale en t0 + (i+1)*0.1 s, como lo entregaria
un microfono al completar sus 100 ms. `t_captured` = epoch en que el chunk entro al pipeline.

B8, tres FUENTES (`opciones_fuente`):
- "archivo": archivo local; `-ss inicio` y `-t duracion`; el ritmo real lo pone este modulo.
- "url": cualquier entrada que ffmpeg abra (HTTP/HLS/RTMP/SRT/UDP...). Un stream en vivo ya llega a
  ritmo real (la espera de abajo queda en 0: sus chunks estan "atrasados" respecto de t0 por el
  arranque); un archivo servido por HTTP se sigue pautando a ritmo real. HTTP(S) con reconexion.
  `-ss` solo si inicio > 0 (VOD).
- "mic": dispositivo de captura. Windows: `-f dshow -audio_buffer_size 50 -i audio=<nombre>` (buffer
  de 50 ms en vez de 500 ms del default de dshow). Listar: `listar_dispositivos()`.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Iterator

import numpy as np

SR = 16000
BYTES_POR_MUESTRA = 2
CHUNK_S = 0.1
CHUNK_BYTES = int(SR * CHUNK_S) * BYTES_POR_MUESTRA  # 3200
MIME = "audio/pcm;rate=16000"


@dataclass
class Chunk:
    idx: int            # indice del chunk desde el inicio del audio de la sesion
    data: bytes         # PCM s16le mono 16 kHz
    audio_start: float  # s desde el inicio del audio de la sesion
    audio_end: float
    t_captured: float   # epoch en que entro al pipeline
    rms: float = 0.0

    @property
    def dur(self) -> float:
        return len(self.data) / (SR * BYTES_POR_MUESTRA)


def rms_pcm16(data: bytes) -> float:
    n = len(data) - (len(data) % 2)
    if n <= 0:
        return 0.0
    x = np.frombuffer(data[:n], dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(x * x)))


FUENTES = ("archivo", "url", "mic")


def opciones_fuente(fuente: str, valor: str) -> tuple[str, str | None, list[str]]:
    """(entrada para -i, formato de entrada o None, opciones de entrada extra) de una FUENTE."""
    if fuente == "mic":
        import sys
        if sys.platform == "win32":
            nombre = valor[len("audio="):] if valor.startswith("audio=") else valor
            return f"audio={nombre}", "dshow", ["-audio_buffer_size", "50"]
        if sys.platform == "darwin":
            return (valor if valor.startswith(":") else f":{valor}"), "avfoundation", []
        return valor or "default", "pulse", []
    if fuente == "url":
        extra = []
        if valor.lower().startswith(("http://", "https://")):
            extra = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        return valor, None, extra
    if fuente == "archivo":
        return valor, None, []
    raise ValueError(f"fuente desconocida: {fuente!r} (una de {FUENTES})")


def listar_dispositivos() -> tuple[list[str], str]:
    """Dispositivos de AUDIO de dshow (Windows): (nombres, salida cruda de ffmpeg)."""
    import re
    ff = shutil.which("ffmpeg") or "ffmpeg"
    r = subprocess.run([ff, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    crudo = (r.stderr or "") + (r.stdout or "")
    nombres = [m.group(1) for m in re.finditer(r'"([^"]+)"\s*\(audio\)', crudo)]
    return nombres, crudo


def comando_ffmpeg(entrada: str, inicio: float = 0.0, duracion: float | None = None,
                   formato_entrada: str | None = None,
                   opciones_entrada: list[str] | None = None) -> list[str]:
    ff = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin"]
    cmd += list(opciones_entrada or [])
    if formato_entrada:
        cmd += ["-f", formato_entrada]
    if inicio and not formato_entrada:
        cmd += ["-ss", f"{inicio:.3f}"]
    cmd += ["-i", entrada]
    if duracion:
        cmd += ["-t", f"{duracion:.3f}"]
    # mono, 16 kHz, s16le crudo por stdout. Sin filtros de volumen: no se normaliza.
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-acodec", "pcm_s16le", "-f", "s16le", "-"]
    return cmd


def trocear(pcm: bytes, t0_epoch: float | None = None) -> Iterator[Chunk]:
    """Parte PCM en chunks de 100 ms sin tiempo real (tests, calculos offline)."""
    t0 = time.time() if t0_epoch is None else t0_epoch
    n = 0
    for off in range(0, len(pcm), CHUNK_BYTES):
        d = pcm[off: off + CHUNK_BYTES]
        a0 = off / (SR * BYTES_POR_MUESTRA)
        a1 = (off + len(d)) / (SR * BYTES_POR_MUESTRA)
        yield Chunk(n, d, round(a0, 3), round(a1, 3), t0 + a1, rms_pcm16(d))
        n += 1


async def fuente_ffmpeg(entrada: str, inicio: float = 0.0, duracion: float | None = None,
                        tiempo_real: bool = True, formato_entrada: str | None = None,
                        opciones_entrada: list[str] | None = None) -> AsyncIterator[Chunk]:
    """Chunks de 100 ms desde ffmpeg. Con tiempo_real=True respeta el reloj de pared."""
    cmd = comando_ffmpeg(entrada, inicio, duracion, formato_entrada, opciones_entrada)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    loop = asyncio.get_running_loop()
    t0_loop = loop.time()
    idx = 0
    try:
        while True:
            try:
                d = await proc.stdout.readexactly(CHUNK_BYTES)
            except asyncio.IncompleteReadError as e:
                d = e.partial
                if not d:
                    break
            if tiempo_real:
                espera = t0_loop + (idx + 1) * CHUNK_S - loop.time()
                if espera > 0:
                    await asyncio.sleep(espera)
            a0 = idx * CHUNK_S
            a1 = a0 + len(d) / (SR * BYTES_POR_MUESTRA)
            yield Chunk(idx, d, round(a0, 3), round(a1, 3), time.time(), rms_pcm16(d))
            idx += 1
            if len(d) < CHUNK_BYTES:
                break
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except Exception:
            pass


def leer_pcm(entrada: str, inicio: float = 0.0, duracion: float | None = None) -> bytes:
    """Decodifica completo (sin tiempo real) a PCM s16le 16 kHz mono."""
    r = subprocess.run(comando_ffmpeg(entrada, inicio, duracion), capture_output=True, check=True)
    return r.stdout


async def desde_lista(chunks: Iterable[Chunk]) -> AsyncIterator[Chunk]:
    for c in chunks:
        yield c
