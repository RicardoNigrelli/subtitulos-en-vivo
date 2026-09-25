"""Video 2 (narración en inglés): arma pista.wav (106 s, 48 kHz estéreo) con cada WAV en su inicio_s, y el SRT en español con el
traductor del propio proyecto (worker.traductor, EN→ES) sobre el texto exacto de cada línea narrada. Un bloque
por línea; si alguna traducción vuelve ok:false, falla sin escribir el SRT. Nada se corrige a mano.

Uso (desde la raíz, venv): .venv/Scripts/python docs/video/r14/video2-narracion-en/armar.py
Necesita los WAV de la narración en wav/<clave>.wav (no están en el repo: son la voz sintética del video) y ffprobe/ffmpeg.
Sin los WAV, la parte reproducible desde el repo es la traducción: textos.json + offsets.json → narracion-es.srt."""
import asyncio, json, subprocess, sys
from pathlib import Path

D = Path(__file__).parent
RAIZ = next(p for p in Path(__file__).resolve().parents if (p / "worker").is_dir())
sys.path.insert(0, str(RAIZ))
from worker.traductor import Traductor  # noqa: E402

TOTAL_S = 106.0


def dur(wav: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(wav)], text=True).strip())


def ts(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


async def main() -> int:
    t = json.loads((D / "textos.json").read_text(encoding="utf-8"))
    claves = sorted(t, key=lambda k: t[k]["inicio_s"])
    offsets, entradas, filtros = {}, [], []
    for i, k in enumerate(claves):
        wav = D / "wav" / f"{k}.wav"
        d = dur(wav)
        fin = t[k]["inicio_s"] + d
        offsets[k] = {"inicio_s": t[k]["inicio_s"], "duracion_s": round(d, 3), "fin_s": round(fin, 3),
                      "ventana_hasta_s": t[k]["ventana_hasta_s"], "entra_en_ventana": fin <= t[k]["ventana_hasta_s"]}
        ms = int(round(t[k]["inicio_s"] * 1000))
        entradas += ["-i", str(wav)]
        filtros.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
    malas = [k for k in claves if not offsets[k]["entra_en_ventana"]]
    if malas:
        print(f"NO ENTRAN: {malas}", file=sys.stderr)
        return 1
    mezcla = "".join(f"[a{i}]" for i in range(len(claves)))
    filtros.append(f"{mezcla}amix=inputs={len(claves)}:duration=longest:normalize=0,apad=whole_dur={TOTAL_S}[aout]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *entradas, "-filter_complex", ";".join(filtros), "-map", "[aout]",
                    "-t", str(TOTAL_S), "-ar", "48000", "-ac", "2", str(D / "pista.wav")], check=True)
    (D / "offsets.json").write_text(json.dumps(offsets, indent=2, ensure_ascii=False), encoding="utf-8")

    res = await Traductor(de="en", a="es").traducir([t[k]["en"] for k in claves])
    print(json.dumps({"modelo": res.modelo, "ms": res.ms, "intentos": res.intentos}, ensure_ascii=False))
    if not all(i["ok"] for i in res.items):
        print("FALLA: alguna traducción volvió ok:false", file=sys.stderr)
        return 1
    bloques, filas = [], []
    for n, (k, item) in enumerate(zip(claves, res.items), 1):
        ini, fin = offsets[k]["inicio_s"], offsets[k]["fin_s"] + 0.4
        bloques.append(f"{n}\n{ts(ini)} --> {ts(fin)}\n{item['text']}\n")
        filas.append({"clave": k, "en": t[k]["en"], "es": item["text"], "inicio": ini, "fin": round(fin, 3)})
        print(f"{k} {ini:>5} | {item['text']}")
    (D / "narracion-es.srt").write_text("\n".join(bloques), encoding="utf-8")
    (D / "narracion-es.json").write_text(json.dumps(filas, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
