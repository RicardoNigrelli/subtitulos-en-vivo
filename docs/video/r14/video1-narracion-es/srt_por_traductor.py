"""SRT en inglés del video (R14) con el traductor del propio proyecto (worker.traductor.Traductor, el
mismo que corre en vivo) aplicado al texto exacto de cada línea narrada. Un bloque por línea, con los
tiempos de offsets.json (inicio y duración medidos con ffprobe). No se corrige nada a mano: si una
traducción vuelve ok:false, el script falla.

Uso (desde la raíz, venv): .venv/Scripts/python docs/video/r14/video1-narracion-es/srt_por_traductor.py
Entradas: textos.json (texto exacto de cada línea narrada en español) y offsets.json (inicio y duración
de cada línea, medidos con ffprobe sobre los WAV de la narración, que no están en el repo).
Salida: narracion-en-102s.srt (+ .json con el par es/en por línea). Gasta cuota del modelo de texto, no de audio.
"""
import asyncio, json, sys
from pathlib import Path

RAIZ = next(p for p in Path(__file__).resolve().parents if (p / "worker").is_dir())
sys.path.insert(0, str(RAIZ))
from worker.traductor import Traductor  # noqa: E402

D = Path(__file__).parent


def ts(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


async def main() -> int:
    textos = json.loads((D / "textos.json").read_text(encoding="utf-8"))
    offsets = json.loads((D / "offsets.json").read_text(encoding="utf-8"))
    claves = sorted(offsets, key=lambda k: offsets[k]["inicio_s"])
    es = [textos[k]["es"] for k in claves]
    res = await Traductor(de="es", a="en").traducir(es)
    print(json.dumps({"modelo": res.modelo, "ms": res.ms, "intentos": res.intentos}, ensure_ascii=False))
    if not all(i["ok"] for i in res.items):
        print("FALLA: alguna traducción volvió ok:false; no se escribe el SRT", file=sys.stderr)
        return 1
    bloques, filas = [], []
    for n, (k, item) in enumerate(zip(claves, res.items), 1):
        ini = offsets[k]["inicio_s"]
        fin = ini + offsets[k]["duracion_s"] + 0.4
        bloques.append(f"{n}\n{ts(ini)} --> {ts(fin)}\n{item['text']}\n")
        filas.append({"clave": k, "es": textos[k]["es"], "en": item["text"], "inicio": ini, "fin": round(fin, 3)})
    (D / "narracion-en-102s.srt").write_text("\n".join(bloques), encoding="utf-8")
    (D / "narracion-en-102s.json").write_text(json.dumps(filas, indent=2, ensure_ascii=False), encoding="utf-8")
    for f in filas:
        print(f"{f['clave']} {f['inicio']:>5} | {f['en']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
