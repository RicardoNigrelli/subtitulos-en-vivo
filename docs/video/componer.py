"""Compone el video final del Guion A a partir de `docs/video/guion-final.json` (14 planos).

Reemplaza la version anterior (hardcodeada a 6 tramos de `corte-v2.mp4`, ver
`reportes/demo-video-v2.md`) por una version GENERICA guiada por datos: cada plano del JSON trae
tipo ("real" | "toma" | "split" | "terminal" | "card"), fuente(s), narracion (wav + texto) y cita.

No inventa audio ni video: si un plano tipo "toma"/"split"/"terminal"/"card" apunta a un archivo que
todavia no existe (grabacion pendiente del orquestador, ver `docs/video/tomas.md`), este script
FALLA CLARO listando exactamente que falta -- no genera un video incompleto en silencio. Con
`--placeholders` genera clips de reemplazo rotulados ("PENDIENTE: plano NN") para poder revisar el
timeline completo mientras faltan tomas reales.

Uso:
    .venv/Scripts/python docs/video/componer.py --guion docs/video/guion-final.json \
        --salida reportes/video/final.mp4
    .venv/Scripts/python docs/video/componer.py --guion docs/video/guion-final.json \
        --salida reportes/video/preview.mp4 --placeholders
    .venv/Scripts/python docs/video/componer.py --guion docs/video/guion-final.json --solo-validar

Requiere ffmpeg/ffprobe en PATH (o --ffmpeg/--ffprobe).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
AR_STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"


def ffprobe_dur(ffprobe: str, path: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def run_ffmpeg(ffmpeg: str, args: list[str], label: str, timeout: float = 150.0) -> None:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"] + args
    print(f"... {label}", flush=True)
    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.HIGH_PRIORITY_CLASS
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(RAIZ), timeout=timeout, **popen_kwargs)
    if r.returncode != 0:
        print(f"--- FFMPEG FALLO: {label} ---", file=sys.stderr)
        print(" ".join(cmd), file=sys.stderr)
        print(r.stderr[-4000:], file=sys.stderr)
        raise SystemExit(1)


def _archivos_esperados(plano: dict) -> list[tuple[str, Path]]:
    """[(descripcion, path)] de TODOS los archivos que este plano necesita (para validar)."""
    tipo = plano["tipo"]
    f = plano["fuente"]
    if tipo == "split":
        return [
            (f"plano {plano['id']} (izquierda)", RAIZ / f["izquierda"]["archivo"]),
            (f"plano {plano['id']} (derecha)", RAIZ / f["derecha"]["archivo"]),
        ]
    return [(f"plano {plano['id']}", RAIZ / f["archivo"])]


def validar(guion: dict) -> list[tuple[str, Path]]:
    faltantes = []
    for plano in guion["planos"]:
        for desc, path in _archivos_esperados(plano):
            if not path.exists():
                faltantes.append((desc, path))
        nw = plano.get("narracion_wav")
        if nw and not (RAIZ / nw).exists():
            faltantes.append((f"plano {plano['id']} (narracion_wav)", RAIZ / nw))
    return faltantes


def generar_placeholder(ffmpeg: str, plano: dict, path: Path, dur: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    texto = f"PENDIENTE - plano {plano['id']:02d}\\n{plano['descripcion']}".replace("'", "\\'")
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", f"color=c=0x202028:s=1920x1080:d={dur}",
        "-vf", f"drawtext=text='{texto}':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=12",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(path),
    ], f"placeholder plano {plano['id']}")


def mezclar_audio(narr_wav: str | None, bed_src: Path, bed_start: float, dur: float) -> tuple[list[str], list[str]]:
    """Devuelve (inputs_ffmpeg, filtro_lineas) para mezclar narracion (si hay) + cama de audio real."""
    inputs = []
    if narr_wav:
        inputs += ["-i", str(RAIZ / narr_wav)]
    inputs += ["-ss", str(bed_start), "-t", str(dur), "-i", str(bed_src)]
    return inputs, []


def build_plano(ffmpeg: str, ffprobe: str, plano: dict, tmp_dir: Path, bed_src: Path,
                 placeholders: bool) -> Path:
    out = tmp_dir / f"plano-{plano['id']:02d}.mp4"
    dur = float(plano["duracion"])
    tipo = plano["tipo"]
    narr_wav = plano.get("narracion_wav")
    narr_dur = ffprobe_dur(ffprobe, RAIZ / narr_wav) if narr_wav else 0.0

    if tipo in ("toma", "card") and placeholders and not (RAIZ / plano["fuente"]["archivo"]).exists():
        generar_placeholder(ffmpeg, plano, out, dur)
        return out
    if tipo == "split" and placeholders and not (RAIZ / plano["fuente"]["derecha"]["archivo"]).exists():
        generar_placeholder(ffmpeg, plano, out, dur)
        return out

    if tipo == "card":
        png = RAIZ / plano["fuente"]["archivo"]
        filtro = (
            f"[0:v]format=yuv420p,fps=30,setsar=1[v];"
            f"[1:a]atrim=0:{narr_dur},asetpts=PTS-STARTPTS,{AR_STEREO}[narr];"
            f"[2:a]atrim=0:{dur},asetpts=PTS-STARTPTS,{AR_STEREO},volume=0.15[bed];"
            f"[narr][bed]amix=inputs=2:duration=longest:normalize=0,apad=whole_dur={dur},"
            f"alimiter=limit=0.95[a]" if narr_wav else
            f"[0:v]format=yuv420p,fps=30,setsar=1[v];"
            f"[2:a]atrim=0:{dur},asetpts=PTS-STARTPTS,{AR_STEREO}[a]"
        )
        inputs = ["-loop", "1", "-t", str(dur), "-i", str(png)]
        inputs += ["-i", str(RAIZ / narr_wav)] if narr_wav else ["-i", str(RAIZ / narr_wav or bed_src)]
        inputs += ["-ss", "0", "-t", str(dur), "-i", str(bed_src)]
        run_ffmpeg(ffmpeg, inputs + ["-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
            "-r", "30", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], out.name)
        return out

    if tipo == "real":
        fuente = plano["fuente"]
        rec = plano["recorte"]
        vf = f"[0:v]crop={rec['crop_previo']},scale={rec['scale']},crop={rec['crop_final']},fps=30,format=yuv420p,setsar=1[v]"
        filtro = f"{vf};[0:a]atrim=0:{dur},asetpts=PTS-STARTPTS,{AR_STEREO}[a]"
        run_ffmpeg(ffmpeg, ["-ss", str(fuente["start_s"]), "-t", str(dur), "-i", str(RAIZ / fuente["archivo"]),
            "-filter_complex", filtro, "-map", "[v]", "-map", "[a]", "-r", "30", "-t", str(dur),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], out.name)
        return out

    if tipo == "toma":
        clip = RAIZ / plano["fuente"]["archivo"]
        zoom = plano.get("zoom")
        vf = "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
        if zoom:
            frames = int(dur * 30)
            vf += f",zoompan=z='min(zoom+{(zoom['hasta']-zoom['desde'])/max(frames,1):.6f},{zoom['hasta']})':d=1:s=1920x1080:fps=30"
        vf += ",format=yuv420p,setsar=1[base]"
        filtro = (
            f"{vf};"
            f"[1:a]atrim=0:{narr_dur},asetpts=PTS-STARTPTS,{AR_STEREO}[narr];"
            f"[base][narr]concat=n=1:v=1:a=0[v0];" if False else vf.replace("[base]", "[v]")
        )
        # audio: narracion sola (la toma ya trae su propio audio de UI, normalmente sin voz util)
        filtro_audio = f"[1:a]atrim=0:{dur},asetpts=PTS-STARTPTS,{AR_STEREO},apad=whole_dur={dur}[a]" if narr_wav else \
            f"[0:a]atrim=0:{dur},asetpts=PTS-STARTPTS,{AR_STEREO},apad=whole_dur={dur}[a]"
        inputs = ["-t", str(dur), "-i", str(clip)]
        inputs += ["-i", str(RAIZ / narr_wav)] if narr_wav else []
        run_ffmpeg(ffmpeg, inputs + ["-filter_complex", f"{vf};{filtro_audio}", "-map", "[v]", "-map", "[a]",
            "-r", "30", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], out.name)
        return out

    if tipo == "terminal":
        clip = RAIZ / plano["fuente"]["archivo"]
        a, b = plano["fuente"]["recorte_s"]
        filtro_v = "[0:v]scale=1920:1080,format=yuv420p,setsar=1[v]"
        filtro_audio = f"[1:a]atrim=0:{narr_dur},asetpts=PTS-STARTPTS,{AR_STEREO},apad=whole_dur={dur}[a]" if narr_wav else None
        inputs = ["-ss", str(a), "-t", str(dur), "-i", str(clip)]
        if narr_wav:
            inputs += ["-i", str(RAIZ / narr_wav)]
            filtro = f"{filtro_v};{filtro_audio}"
        else:
            filtro = f"{filtro_v};[0:a]atrim=0:{dur},{AR_STEREO},apad=whole_dur={dur}[a]"
        run_ffmpeg(ffmpeg, inputs + ["-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
            "-r", "30", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], out.name)
        return out

    if tipo == "split":
        # Combinacion simple: izquierda (orador, recortado) + derecha (toma) lado a lado.
        izq = plano["fuente"]["izquierda"]
        der = RAIZ / plano["fuente"]["derecha"]["archivo"]
        rec = izq["recorte"]
        filtro = (
            f"[0:v]crop={rec['crop_previo']},scale={rec['scale']},crop={rec['crop_final']},"
            f"scale=960:1080,fps=30,format=yuv420p,setsar=1[left];"
            f"[1:v]scale=960:1080:force_original_aspect_ratio=increase,crop=960:1080,fps=30,"
            f"format=yuv420p,setsar=1[right];"
            f"[left][right]hstack=inputs=2[v];"
        )
        if narr_wav:
            filtro += f"[2:a]atrim=0:{narr_dur},asetpts=PTS-STARTPTS,{AR_STEREO},apad=whole_dur={dur}[a]"
        else:
            filtro += f"[0:a]atrim=0:{dur},{AR_STEREO},apad=whole_dur={dur}[a]"
        inputs = ["-ss", str(izq["start_s"]), "-t", str(dur), "-i", str(RAIZ / izq["archivo"]),
                  "-t", str(dur), "-i", str(der)]
        if narr_wav:
            inputs += ["-i", str(RAIZ / narr_wav)]
        run_ffmpeg(ffmpeg, inputs + ["-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
            "-r", "30", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], out.name)
        return out

    raise SystemExit(f"tipo de plano desconocido: {tipo}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--guion", default=str(RAIZ / "docs/video/guion-final.json"))
    ap.add_argument("--salida", default=str(RAIZ / "reportes/video/final.mp4"))
    ap.add_argument("--tmp-dir", default=str(RAIZ / "reportes/video/tmp_final"))
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--placeholders", action="store_true",
                     help="genera clips PENDIENTE rotulados para las tomas que todavia no existen")
    ap.add_argument("--solo-validar", action="store_true", help="sólo chequea que existan los archivos, no renderiza")
    args = ap.parse_args()

    guion = json.loads(Path(args.guion).read_text(encoding="utf-8"))
    faltantes = validar(guion)

    if faltantes and not args.placeholders:
        print(f"FALTAN {len(faltantes)} archivo(s) para componer {args.salida}:", file=sys.stderr)
        for desc, path in faltantes:
            print(f"  - {desc}: {path}", file=sys.stderr)
        print("Grabalos (ver docs/video/tomas.md) o corré con --placeholders para una vista previa.",
              file=sys.stderr)
        return 1

    print(f"Validacion OK: {len(guion['planos'])} planos, {len(faltantes)} pendientes"
          f"{' (se reemplazan con placeholders)' if faltantes else ''}.")
    if args.solo_validar:
        return 0

    tmp = Path(args.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    bed_src = RAIZ / guion["audio_bed"]
    parts = []
    for plano in guion["planos"]:
        parts.append(build_plano(args.ffmpeg, args.ffprobe, plano, tmp, bed_src, args.placeholders))

    lista = tmp / "concat.txt"
    lista.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args.ffmpeg, ["-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(salida)],
               "concat_final")
    dur_total = ffprobe_dur(args.ffprobe, salida)
    print(f"OK -> {salida} ({dur_total:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
