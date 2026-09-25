"""Importar una charla y cortar un clip de prueba (R17b: "audios de prueba en el repo y una opcion
simple para importarlos").

    python -m worker.importar <url|archivo> --slug X [--inicio S] [--duracion D] [--format-id ID]
    python -m worker.importar <url> --listar-formatos          # yt-dlp -F (elegir la pista original)

- URL: baja con yt-dlp COMO MODULO del venv (skill ingesta: el .exe ignora SSL_CERT_FILE) la pista
  de audio marcada "original (default)" (YouTube sirve doblajes automaticos: no se confia en
  bestaudio) a fixtures/audio/full/<slug>.<ext> (git la ignora). Si ya esta bajada, NO se vuelve a
  bajar (YouTube devuelve 429 si se abusa). --format-id fuerza la pista.
- Archivo local: se usa tal cual (no se copia).
- Corta [inicio, inicio+duracion] con ffmpeg a WAV PCM s16le 16 kHz mono en
  fixtures/audio/clips/<slug>-<inicio>s-<duracion>s.wav. SIN normalizar (skill gemini-live).
- Imprime la linea para la tabla de clips de fixtures/audio/FUENTES.md (y la de la fuente si es URL).
Exit 0 = clip escrito; 1 = fallo de descarga/corte; 2 = uso.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FULL = RAIZ / "fixtures" / "audio" / "full"
CLIPS = RAIZ / "fixtures" / "audio" / "clips"


def es_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s))


def _mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 60:02d}:{s % 60:02d}"


def _yt(*args: str, capturar: bool = True) -> subprocess.CompletedProcess:
    from worker.seguridad import entorno_subproceso      # B7: yt-dlp sin credenciales
    return subprocess.run([sys.executable, "-m", "yt_dlp", *args], capture_output=capturar,
                          text=True, encoding="utf-8", errors="replace", env=entorno_subproceso())


def elegir_pista(info: dict) -> dict | None:
    """Pista SOLO audio marcada 'original' (o la de mayor language_preference); prefiere m4a y mas abr."""
    audios = [f for f in info.get("formats") or [] if f.get("vcodec") in (None, "none")
              and f.get("acodec") not in (None, "none")]
    if not audios:
        return None
    orig = [f for f in audios if "original" in (f.get("format_note") or "").lower()
            or "original" in (f.get("format") or "").lower()]
    if not orig:
        top = max((f.get("language_preference") or -1) for f in audios)
        orig = [f for f in audios if (f.get("language_preference") or -1) == top]
    return max(orig, key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0))


def bajar(url: str, slug: str, format_id: str | None, full_dir: Path = FULL) -> tuple[Path, dict]:
    full_dir.mkdir(parents=True, exist_ok=True)
    ya = sorted(full_dir.glob(f"{slug}.*"))
    if ya:
        print(f"[importar] ya bajado: {ya[0]} (no se vuelve a bajar)", file=sys.stderr)
        return ya[0], {"format_id": None, "reusado": True}
    p = _yt("-J", "--no-playlist", url)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp -J fallo: {p.stderr[-400:]}")
    info = json.loads(p.stdout)
    if format_id:
        pista = next((f for f in info.get("formats") or [] if f.get("format_id") == format_id), None)
    else:
        pista = elegir_pista(info)
    if pista is None:
        raise RuntimeError("no encontre una pista de audio (usar --listar-formatos y --format-id)")
    salida = full_dir / f"{slug}.%(ext)s"
    p = _yt("-f", pista["format_id"], "--no-playlist", "-o", str(salida), url, capturar=False)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp fallo al bajar (exit {p.returncode})")
    bajado = sorted(full_dir.glob(f"{slug}.*"))[0]
    meta = {"format_id": pista["format_id"], "ext": pista.get("ext"),
            "format_note": pista.get("format_note"), "language": pista.get("language"),
            "title": info.get("title"), "id": info.get("id"), "duration": info.get("duration"),
            "upload_date": info.get("upload_date"), "uploader": info.get("uploader")}
    return bajado, meta


def cortar(entrada: Path | str, salida: Path, inicio: float, duracion: float) -> None:
    ff = shutil.which("ffmpeg") or "ffmpeg"
    salida.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{inicio}", "-t", f"{duracion}",
           "-i", str(entrada), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(salida)]
    from worker.seguridad import entorno_subproceso      # B7: ffmpeg sin credenciales
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=entorno_subproceso())
    if p.returncode != 0 or not salida.exists():
        raise RuntimeError(f"ffmpeg fallo (exit {p.returncode}): {p.stderr[-400:]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.importar")
    ap.add_argument("fuente", help="URL (YouTube u otra que yt-dlp entienda) o archivo local")
    ap.add_argument("--slug", default=None, help="nombre corto, p.ej. nerdearla-en-booch")
    ap.add_argument("--inicio", type=float, default=0.0)
    ap.add_argument("--duracion", type=float, default=60.0)
    ap.add_argument("--format-id", default=None, help="fuerza la pista de yt-dlp")
    ap.add_argument("--listar-formatos", action="store_true")
    ap.add_argument("--clips-dir", default=str(CLIPS))
    ap.add_argument("--full-dir", default=str(FULL))
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if a.listar_formatos:
        if not es_url(a.fuente):
            print("--listar-formatos es para URLs", file=sys.stderr)
            return 2
        return _yt("-F", "--no-playlist", a.fuente, capturar=False).returncode
    if not a.slug or not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", a.slug):
        print("--slug obligatorio, formato ^[a-z0-9][a-z0-9_-]{0,63}$", file=sys.stderr)
        return 2
    try:
        if es_url(a.fuente):
            origen, meta = bajar(a.fuente, a.slug, a.format_id, Path(a.full_dir))
        else:
            origen, meta = Path(a.fuente), {}
            if not origen.exists():
                print(f"no existe: {origen}", file=sys.stderr)
                return 2
        clip = Path(a.clips_dir) / f"{a.slug}-{int(a.inicio)}s-{int(a.duracion)}s.wav"
        cortar(origen, clip, a.inicio, a.duracion)
    except Exception as e:
        print(f"[importar] ERROR: {e}", file=sys.stderr)
        return 1
    ref = meta.get("id") or (a.fuente if es_url(a.fuente) else Path(a.fuente).name)
    tramo = f"{_mmss(a.inicio)}–{_mmss(a.inicio + a.duracion)}"
    print(f"[importar] clip: {clip} ({a.duracion:g} s, PCM s16le 16 kHz mono, sin normalizar)", file=sys.stderr)
    print("# linea para fixtures/audio/FUENTES.md (tabla de clips):")
    print(f"| `{clip.name}` | {tramo} de {ref} | importado con `python -m worker.importar` |")
    if es_url(a.fuente) and meta.get("format_id"):
        print("# linea para la tabla de fuentes (completar Charla/Orador si hace falta):")
        print(f"| `{a.slug}` | {meta.get('title')} | {meta.get('uploader')} | {a.fuente} | "
              f"{meta.get('duration')} s | {meta.get('upload_date')} | `{meta.get('format_id')}` "
              f"{meta.get('ext')}, `{meta.get('format_note')}` | (fecha de hoy) |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
