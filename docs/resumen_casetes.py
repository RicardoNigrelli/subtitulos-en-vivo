"""Resumen de casetes reales (evidencia): audio enviado, textos emitidos, primer texto y rotaciones.

Lee los casetes JSONL de tres capas que graba `worker.run` (cabecera sin `dir`; líneas `client`,
`server`, `emit`) y, por cada uno, imprime una fila:

    archivo  sesión  idioma  audio=<s enviados>  textos=<finales emitidos>  primero=+<s hasta el primer
    texto>  rot=<rotaciones>  lost=<audio perdido en rotaciones, s>  [(motivo, perdido_s), ...]

Es el comando que respalda las tablas de `docs/evidencia.md`. No toca la API.

Uso (desde la raíz, venv):
    .venv/Scripts/python docs/resumen_casetes.py fixtures/casetes/evidencia-25-09/*.jsonl
"""
import json
import sys
from pathlib import Path


def resumir(ruta: Path) -> dict:
    t0 = None
    audio = 0.0
    textos = 0
    rot: list[tuple[str, float]] = []
    primero = None
    sid = lang = "?"
    with ruta.open(encoding="utf-8") as fh:
        for linea in fh:
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if "dir" not in d:                      # cabecera del casete
                t0 = d.get("started_at")
                sid = d.get("session_id", sid)
                lang = d.get("lang", lang)
                continue
            p = d.get("payload") or {}
            if d["dir"] == "client" and d.get("kind") == "ventana":
                audio += p.get("n_chunks", 0) * 0.1     # chunks de 100 ms
            elif d["dir"] == "emit":
                tipo = p.get("type")
                if tipo == "text":
                    textos += 1
                    if primero is None and t0 is not None:
                        primero = d["t"] - t0
                elif tipo == "rotation":
                    m = p.get("meta") or {}
                    perdido = float(m.get("audio_lost_s") or 0)
                    rot.append((str(m.get("reason") or "?"), perdido))
    return {"archivo": ruta.name, "sesion": sid, "lang": lang, "audio_s": round(audio, 1), "textos": textos,
            "primer_texto_s": round(primero, 1) if primero is not None else None,
            "rotaciones": rot, "audio_perdido_s": round(sum(p for _, p in rot), 1)}


def main(argv: list[str]) -> int:
    rutas = [Path(a) for a in argv[1:]]
    if not rutas:
        print(__doc__, file=sys.stderr)
        return 2
    for ruta in sorted(rutas):
        r = resumir(ruta)
        primero = f"+{r['primer_texto_s']:5.1f}s" if r["primer_texto_s"] is not None else "   -   "
        print(f"{r['archivo']:36} {r['sesion']:26} {r['lang']:3} audio={r['audio_s']:6.1f}s "
              f"textos={r['textos']:3d} primero={primero} rot={len(r['rotaciones'])} "
              f"lost={r['audio_perdido_s']:.1f}s {r['rotaciones']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
