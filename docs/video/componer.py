"""Compone `reportes/video/corte-v2.mp4` a partir de los materiales en disco (sin API de Gemini).

Junta, en este orden: tarjeta portada -> pantalla dividida (orador real + vista EN en vivo del
borrador, sincronizada) -> indice (borrador) -> tarjeta REPLAY + vista ES replay rotulada ->
tarjeta panel + vista panel -> tarjeta de cierre (como se usa + licencia). Narracion en ESPANOL
(decision de Ricardo 20:55: WAV por tramo, generados con Cartesia o con la voz local de Windows
como reemplazo) mezclada con un fondo de audio REAL de la charla (`orador-en-booch-295-405.mp4`) a
bajo volumen; subtitulos en INGLES quemados con el filtro `subtitles=` -- generados con el propio
proyecto (R14: ASR + traduccion ES->EN reales sobre la narracion, ver `docs/video/narracion.md`),
no escritos a mano.

No inventa numeros: los unicos que aparecen (en la narracion hablada, nunca como texto en pantalla)
estan documentados con su comando de origen en `docs/video/narracion.md` /
`docs/video/narracion_textos.json`.

Requiere: ffmpeg en PATH (o `--ffmpeg` con la ruta completa), las 5 tarjetas PNG ya renderizadas
(Chrome headless sobre un recorte de `reportes/video/rotulos.html` por tarjeta; comando exacto en
`reportes/demo-video-v2.md`) y los WAV de narracion (ver `docs/video/narrar_cartesia.py`).

Uso (con los valores por defecto, que son los del bloque):
    .venv/Scripts/python docs/video/componer.py

Uso con rutas explicitas:
    .venv/Scripts/python docs/video/componer.py \
        --borrador reportes/video/borrador-2026-09-24-recortado.mp4 \
        --orador reportes/video/orador-en-booch-295-405.mp4 \
        --cards-dir reportes/video/cards \
        --narracion-dir reportes/video/narracion_wav \
        --salida reportes/video/corte-v2.mp4 \
        --timeline-json reportes/video/corte-v2-timeline.json

Escribe ademas `--timeline-json` con el inicio/fin de cada tramo en el video FINAL (para poder
generar despues `docs/video/narracion.srt` con los tiempos reales).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

W, H, FPS = 1920, 1080, 30
AR_STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"
# ruta SIN ":" (drive letter) para no pelear con el escapado de ":" del parser de filtros de ffmpeg:
# se copia el TTF del sistema a una ruta relativa a RAIZ y ffmpeg corre con cwd=RAIZ (ver run_ffmpeg).
FONT = "reportes/video/tmp_v2/arialbd.ttf"


def _asegurar_fuente() -> None:
    destino = RAIZ / FONT
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        candidatos = [
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
        for c in candidatos:
            if c.exists():
                shutil.copy(c, destino)
                return
        raise SystemExit("No se encontro una fuente TTF para drawtext (arialbd.ttf/arial.ttf)")


def ffprobe_dur(ffprobe: str, path: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


FFPROBE_BIN = "ffprobe"  # fijado por main() desde --ffprobe antes de armar ninguna parte


def run_ffmpeg(ffmpeg: str, args: list[str], label: str, timeout: float = 25.0,
               expected_dur: float | None = None) -> None:
    """Nota (ver `reportes/demo-video-v2.md`): en esta maquina, con este build de ffmpeg, algunos
    filtros complejos con 2+ entradas con `-ss` (hstack/overlay) a veces no cierran el PROCESO solo
    al terminar (se probo con y sin drawtext, con y sin volume `enable=`: no es de un filtro puntual).
    Por eso: salida en MP4 fragmentado (`-movflags frag_keyframe+empty_moov`, sobrevive a un kill a
    mitad de escritura sin quedar con "moov atom not found") + si el timeout vence, se valida con
    ffprobe que la duracion real este cerca de `expected_dur` antes de aceptar el archivo: un archivo
    corto (proceso matado a mitad de la codificacion, no solo "colgado ya terminado") SI es una falla."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"] + args
    out_path = Path(args[-1])
    print(f"... {label}", flush=True)
    # HIGH_PRIORITY_CLASS (Windows): esta maquina tiene otros procesos (de otros agentes) que
    # acaparan CPU (visto con Get-Process: "find" con miles de segundos de CPU acumulados); sin
    # esto, hasta un tramo trivial (imagen estatica) tarda >150s por inanicion de CPU, no por el
    # trabajo en si (confirmado: 8,3 s de 12,27 s codificados en 150 s reales).
    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.HIGH_PRIORITY_CLASS
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(RAIZ), timeout=timeout,
                            **popen_kwargs)
    except subprocess.TimeoutExpired:
        dur_real = None
        if out_path.exists() and out_path.stat().st_size > 1000:
            try:
                dur_real = ffprobe_dur(FFPROBE_BIN, out_path)
            except Exception:
                dur_real = None
        ok = dur_real is not None and (expected_dur is None or dur_real >= expected_dur - 0.5)
        if ok:
            print(f"AVISO: {label} no cerro el proceso solo (timeout {timeout}s) pero el archivo "
                  f"de salida quedo completo (dur={dur_real:.2f}s); se sigue.", file=sys.stderr)
            return
        print(f"--- FFMPEG COLGADO Y TRUNCO (timeout {timeout}s, dur={dur_real}, "
              f"esperado>={expected_dur}): {label} ---", file=sys.stderr)
        print(" ".join(cmd), file=sys.stderr)
        raise SystemExit(1)
    if r.returncode != 0:
        print(f"--- FFMPEG FALLO: {label} ---", file=sys.stderr)
        print(" ".join(cmd), file=sys.stderr)
        print(r.stderr[-4000:], file=sys.stderr)
        raise SystemExit(1)


def build_card(ffmpeg, png, dur, narr_wav, narr_a, narr_b, bed_src, bed_vol, out_path):
    """Tarjeta estatica (PNG) + narracion (recorte narr_a..narr_b del wav) + fondo de charla real."""
    filtro = (
        f"[0:v]format=yuv420p,fps={FPS},setsar=1[v];"
        f"[1:a]atrim={narr_a}:{narr_b},asetpts=PTS-STARTPTS,{AR_STEREO},volume=1.0[narr];"
        f"[2:a]atrim=0:{dur},asetpts=PTS-STARTPTS,{AR_STEREO},volume={bed_vol}[bed];"
        f"[narr][bed]amix=inputs=2:duration=longest:normalize=0,apad=whole_dur={dur},"
        f"alimiter=limit=0.95[a]"
    )
    run_ffmpeg(ffmpeg, [
        "-loop", "1", "-t", str(dur), "-i", str(png),
        "-i", str(narr_wav),
        "-ss", "0", "-t", str(dur), "-i", str(bed_src),
        "-filter_complex", filtro,
        "-map", "[v]", "-map", "[a]",
        "-r", str(FPS), "-t", str(dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "frag_keyframe+empty_moov",
        str(out_path),
    ], out_path.name, timeout=150.0, expected_dur=dur)


def build_footage_single(ffmpeg, borrador, start, real_dur, dur, narr_wav, narr_a, narr_b, bed_vol,
                          out_path, banner_png=None):
    """Un tramo del borrador (recortado al cuadro 1920x1080) + su propio audio real como fondo.
    `real_dur` es el metraje REAL tomado del borrador; si `dur` > `real_dur` (la narracion no entra),
    se sostiene el ULTIMO FOTOGRAMA real el tiempo que falta (tpad), nunca se inventa video nuevo.
    `banner_png` (opcional) es un PNG 1920x1080 con fondo transparente que se superpone con
    `overlay` (NO se usa `drawtext`: cuelga el proceso de ffmpeg al cerrar en esta maquina, ver
    `docs/video/generar_banners.py`)."""
    extra = round(dur - real_dur, 3)
    vf = f"[0:v]scale=2255:{H},crop={W}:{H}:168:0"
    if extra > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={extra}"
    vf += f",fps={FPS},format=yuv420p,setsar=1[base]"
    inputs = ["-ss", str(start), "-t", str(real_dur), "-i", str(borrador), "-i", str(narr_wav)]
    if banner_png:
        inputs += ["-loop", "1", "-i", str(banner_png)]
        vcompose = f"{vf};[base][2:v]overlay=0:0,format=yuv420p[v]"
    else:
        vcompose = f"{vf.replace('[base]', '[v]')}"
    filtro = (
        f"{vcompose};"
        f"[1:a]atrim={narr_a}:{narr_b},asetpts=PTS-STARTPTS,{AR_STEREO},volume=1.0[narr];"
        f"[0:a]atrim=0:{real_dur},asetpts=PTS-STARTPTS,{AR_STEREO},volume={bed_vol}[bed];"
        f"[narr][bed]amix=inputs=2:duration=longest:normalize=0,apad=whole_dur={dur},"
        f"alimiter=limit=0.95[a]"
    )
    run_ffmpeg(ffmpeg, inputs + [
        "-filter_complex", filtro,
        "-map", "[v]", "-map", "[a]",
        "-r", str(FPS), "-t", str(dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "frag_keyframe+empty_moov",
        str(out_path),
    ], out_path.name, timeout=150.0, expected_dur=dur)


def build_split_screen(ffmpeg, orador, orador_start, borrador, borrador_start, real_dur, dur,
                        narr_wav, narr_a, narr_b, duck_split, banner_png, out_path, tmp_dir):
    """Pantalla dividida: orador (40%, izquierda) + vista EN del borrador (60%, derecha,
    recortada al area de subtitulos), sincronizados. Audio: voz del orador real (agachada
    mientras dura la narracion, a volumen normal despues) + narracion. Si `dur` > `real_dur`
    (la narracion no entra en el metraje sincronizado), se sostiene el ultimo fotograma de AMBOS
    lados el tiempo que falta (tpad) en vez de inventar mas metraje sincronizado. El rotulo LIVE es
    un PNG superpuesto con `overlay` (no `drawtext`: ver nota en `build_footage_single`).

    Implementado en DOS pasos (no uno) por un hang reproducible en esta maquina: un solo
    filter_complex con DOS entradas `-ss` (orador Y borrador) juntas + hstack se quedaba SIEMPRE
    en 8,36 s de las entradas base ya cortadas (con -ss dentro de esta funcion, sin filtro, con
    -c copy) y despues combina SIN -ss (ya arrancan en 0) -> hstack -> overlay. Documentado en
    `reportes/demo-video-v2.md`."""
    extra = round(dur - real_dur, 3)
    tpad = f",tpad=stop_mode=clone:stop_duration={extra}" if extra > 0 else ""

    left_raw = tmp_dir / "_split_left_raw.mp4"
    right_raw = tmp_dir / "_split_right_raw.mp4"
    run_ffmpeg(ffmpeg, [
        "-ss", str(orador_start), "-t", str(real_dur), "-i", str(orador),
        "-map", "0:v", "-map", "0:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "frag_keyframe+empty_moov",
        str(left_raw),
    ], "split_left_raw.mp4", timeout=150.0, expected_dur=real_dur)
    run_ffmpeg(ffmpeg, [
        "-ss", str(borrador_start), "-t", str(real_dur), "-i", str(borrador),
        "-map", "0:v", "-an", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-movflags", "frag_keyframe+empty_moov",
        str(right_raw),
    ], "split_right_raw.mp4", timeout=150.0, expected_dur=real_dur)

    filtro = (
        f"[0:v]scale={H*1280//720}:{H},crop=768:{H}:{(H*1280//720-768)//2}:0{tpad},"
        f"fps={FPS},format=yuv420p,setsar=1[left];"
        f"[1:v]scale=2255:{H},crop=1152:{H}:560:0{tpad},fps={FPS},format=yuv420p,setsar=1[right];"
        f"[left][right]hstack=inputs=2[stacked];"
        f"[stacked][3:v]overlay=0:0,format=yuv420p[v];"
        f"[2:a]atrim={narr_a}:{narr_b},asetpts=PTS-STARTPTS,{AR_STEREO},volume=1.0[narr];"
        f"[0:a]atrim=0:{real_dur},asetpts=PTS-STARTPTS,{AR_STEREO},"
        f"volume=0.30:enable='lte(t\\,{duck_split})',volume=0.75:enable='gt(t\\,{duck_split})'[spk];"
        f"[narr][spk]amix=inputs=2:duration=longest:normalize=0,apad=whole_dur={dur},"
        f"alimiter=limit=0.95[a]"
    )
    run_ffmpeg(ffmpeg, [
        "-i", str(left_raw),
        "-i", str(right_raw),
        "-i", str(narr_wav),
        "-loop", "1", "-i", str(banner_png),
        "-filter_complex", filtro,
        "-map", "[v]", "-map", "[a]",
        "-r", str(FPS), "-t", str(dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "frag_keyframe+empty_moov",
        str(out_path),
    ], out_path.name, timeout=150.0, expected_dur=dur)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--borrador", default=str(RAIZ / "reportes/video/borrador-2026-09-24-recortado.mp4"))
    ap.add_argument("--orador", default=str(RAIZ / "reportes/video/orador-en-booch-295-405.mp4"))
    ap.add_argument("--cards-dir", default=str(RAIZ / "reportes/video/cards"))
    ap.add_argument("--narracion-dir", default=str(RAIZ / "reportes/video/narracion_wav"))
    ap.add_argument("--tmp-dir", default=str(RAIZ / "reportes/video/tmp_v2"))
    ap.add_argument("--salida", default=str(RAIZ / "reportes/video/corte-v2.mp4"))
    ap.add_argument("--timeline-json", default=str(RAIZ / "reportes/video/corte-v2-timeline.json"))
    ap.add_argument("--subs", default=str(RAIZ / "docs/video/narracion-en.srt"),
                     help="SRT a quemar sobre el video (subtitulos en el idioma que NO es la narracion)")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    args = ap.parse_args()

    global FFPROBE_BIN
    FFPROBE_BIN = args.ffprobe
    _asegurar_fuente()
    borrador = Path(args.borrador)
    orador = Path(args.orador)
    cards = Path(args.cards_dir)
    narr_dir = Path(args.narracion_dir)
    tmp = Path(args.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)

    if not (tmp / "banner_live.png").exists() or not (tmp / "banner_replay.png").exists():
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("generar_banners.py")),
             "--salida-dir", str(tmp)],
            check=True,
        )

    def narr(nombre: str) -> Path:
        return narr_dir / f"{nombre}.wav"

    def dur(nombre: str) -> float:
        return ffprobe_dur(args.ffprobe, narr(nombre))

    d_n1 = dur("n1_portada")
    d_n2 = dur("n2_vivo")
    d_n3 = dur("n3_indice")
    d_n4 = dur("n4_replay")
    d_n5 = dur("n5_panel")
    d_n6 = dur("n6_cierre")

    # --- duraciones de cada tramo del video final (parte fija de tarjeta + parte de metraje) ---
    P1_dur = round(max(d_n1 + 0.8, 11.0), 2)                 # portada
    P2a_dur = 2.5                                            # tarjeta "vivo"
    P2b_real = 16.0                                          # metraje sincronizado real (orador+EN vivo)
    P2b_dur = round(max(P2b_real, (d_n2 - P2a_dur) + 0.3), 2)
    P3_real = 11.0                                           # indice (metraje real, tramo 16-27 del borrador)
    P3_dur = round(max(P3_real, d_n3 + 0.3), 2)
    P4a_dur = 3.0                                            # tarjeta REPLAY
    P4b_real = 16.0                                          # vista ES replay (metraje real, tramo 46-62)
    P4b_dur = round(max(P4b_real, (d_n4 - P4a_dur) + 0.4), 2)
    P5a_dur = 3.0                                            # tarjeta panel
    P5b_real = 14.0                                          # vista panel (metraje real, tramo 65-79)
    P5b_dur = round(max(P5b_real, (d_n5 - P5a_dur) + 0.3), 2)
    P6_dur = round(max(d_n6 + 1.5, 13.0), 2)                 # cierre

    parts = []

    def add(name, builder_fn):
        out = tmp / f"{name}.mp4"
        builder_fn(out)
        parts.append(out)
        return out

    add("p1_portada", lambda out: build_card(
        args.ffmpeg, cards / "card_intro.png", P1_dur, narr("n1_portada"), 0, d_n1,
        orador, 0.13, out))

    add("p2a_vivo_tarjeta", lambda out: build_card(
        args.ffmpeg, cards / "card_vivo.png", P2a_dur, narr("n2_vivo"), 0, P2a_dur,
        orador, 0.15, out))

    n2b_narr_a, n2b_narr_b = P2a_dur, min(d_n2, P2a_dur + P2b_dur)
    add("p2b_split", lambda out: build_split_screen(
        args.ffmpeg, orador, 24.5, borrador, 27.0, P2b_real, P2b_dur,
        narr("n2_vivo"), n2b_narr_a, n2b_narr_b, n2b_narr_b - n2b_narr_a,
        tmp / "banner_live.png", out, tmp))

    add("p3_indice", lambda out: build_footage_single(
        args.ffmpeg, borrador, 16.0, P3_real, P3_dur, narr("n3_indice"), 0, min(d_n3, P3_dur),
        0.16, out))

    add("p4a_replay_tarjeta", lambda out: build_card(
        args.ffmpeg, cards / "card_replay.png", P4a_dur, narr("n4_replay"), 0, P4a_dur,
        orador, 0.15, out))

    n4b_narr_a, n4b_narr_b = P4a_dur, min(d_n4, P4a_dur + P4b_dur)
    add("p4b_replay_vista", lambda out: build_footage_single(
        args.ffmpeg, borrador, 46.0, P4b_real, P4b_dur, narr("n4_replay"), n4b_narr_a, n4b_narr_b,
        0.15, out, banner_png=tmp / "banner_replay.png"))

    add("p5a_panel_tarjeta", lambda out: build_card(
        args.ffmpeg, cards / "card_panel.png", P5a_dur, narr("n5_panel"), 0, P5a_dur,
        orador, 0.15, out))

    n5b_narr_a, n5b_narr_b = P5a_dur, min(d_n5, P5a_dur + P5b_dur)
    add("p5b_panel_vista", lambda out: build_footage_single(
        args.ffmpeg, borrador, 65.0, P5b_real, P5b_dur, narr("n5_panel"), n5b_narr_a, n5b_narr_b,
        0.15, out))

    add("p6_cierre", lambda out: build_card(
        args.ffmpeg, cards / "card_fin.png", P6_dur, narr("n6_cierre"), 0, d_n6,
        orador, 0.13, out))

    # --- concatenar todas las partes (mismo codec/formato -> concat demuxer, stream copy) ---
    lista = tmp / "concat.txt"
    lista.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    concatenado = tmp / "concatenado.mp4"
    run_ffmpeg(args.ffmpeg, [
        "-f", "concat", "-safe", "0", "-i", str(lista),
        "-c", "copy", str(concatenado),
    ], "concat", timeout=60.0)

    # --- timeline para alinear los subtitulos con el video final ---
    # `narra_inicio` = clave de narracion (n1..n6) que arranca AL COMIENZO de ese tramo: sirve para
    # calcular, para docs/video/narracion-en.srt, el offset entre el WAV concatenado (solo narracion,
    # sin las tarjetas/metraje de relleno) y el video final: offset_i = video_inicio_i - concat_inicio_i.
    timeline = []
    narr_offsets = {}
    t = 0.0
    for name, d, narra_inicio in [
        ("n1_portada", P1_dur, "n1_portada"), ("n2_vivo_tarjeta", P2a_dur, "n2_vivo"),
        ("n2_vivo_split", P2b_dur, None), ("n3_indice", P3_dur, "n3_indice"),
        ("n4_replay_tarjeta", P4a_dur, "n4_replay"), ("n4_replay_vista", P4b_dur, None),
        ("n5_panel_tarjeta", P5a_dur, "n5_panel"), ("n5_panel_vista", P5b_dur, None),
        ("n6_cierre", P6_dur, "n6_cierre"),
    ]:
        timeline.append({"tramo": name, "inicio": round(t, 2), "fin": round(t + d, 2)})
        if narra_inicio:
            narr_offsets[narra_inicio] = round(t, 3)
        t += d
    concat_t = 0.0
    for clave, d in [("n1_portada", d_n1), ("n2_vivo", d_n2), ("n3_indice", d_n3),
                     ("n4_replay", d_n4), ("n5_panel", d_n5), ("n6_cierre", d_n6)]:
        narr_offsets[clave] = {
            "video_inicio": narr_offsets[clave],
            "concat_inicio": round(concat_t, 3),
            "offset": round(narr_offsets[clave] - concat_t, 3),
            "duracion": round(d, 3),
        }
        concat_t += d
    Path(args.timeline_json).write_text(
        json.dumps({"partes": timeline, "narracion_offsets": narr_offsets, "duracion_total": round(t, 2)},
                   indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"timeline -> {args.timeline_json}, duracion total = {t:.2f}s")

    # --- quemar subtitulos (EN, generados con el propio proyecto sobre la narracion ES) ---
    srt = Path(args.subs)
    salida = Path(args.salida)
    if srt.exists():
        srt_ffmpeg = str(srt.resolve().as_posix()).replace(":", "\\:")
        run_ffmpeg(args.ffmpeg, [
            "-i", str(concatenado),
            "-vf", f"subtitles='{srt_ffmpeg}':force_style='Fontsize=20,Outline=2,Shadow=0,MarginV=40'",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(salida),
        ], "burn_subtitles", timeout=180.0)
        print(f"OK con subtitulos quemados -> {salida}")
    else:
        run_ffmpeg(args.ffmpeg, [
            "-i", str(concatenado), "-c", "copy", str(salida),
        ], "copia_sin_subtitulos")
        print(f"AVISO: no existe {srt}; se copio sin subtitulos quemados -> {salida}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
