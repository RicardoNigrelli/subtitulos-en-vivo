"""Genera los WAV de narracion (EN) del corte v2 con la API REST de Cartesia (sonic).

Lee los textos desde `docs/video/narracion_textos.json` (clave -> {"en": ..., "es": ..., "cita": ...})
y pide un WAV por clave a `POST https://api.cartesia.ai/tts/bytes`. La API key sale de la variable
de entorno CARTESIA_API_KEY (normalmente en `.env`); ESTE SCRIPT NUNCA la imprime ni la loguea.

Probado a mano el 24/09 20:40 AR con una llamada corta antes de generar los 6 tramos (ver
`reportes/demo-video-v2.md`): `Cartesia-Version: 2024-06-10`, `model_id: sonic-2`, voz
`33d406dd-ff6f-4be7-a7f5-8b1ba183b3e4` ("Nandi - Poised Concierge", en, neutra), HTTP 200,
`audio/wav`, 217166 bytes para una frase corta de prueba.

Uso:
    .venv/Scripts/python docs/video/narrar_cartesia.py \
        --textos docs/video/narracion_textos.json \
        --salida-dir reportes/video/narracion_wav \
        --voice-id 33d406dd-ff6f-4be7-a7f5-8b1ba183b3e4 \
        --model-id sonic-2 \
        --version 2024-06-10

Si Cartesia no responde 200 para algun tramo, el script lo reporta y sigue con los demas (no aborta
todo el lote); el llamador decide si falta algo y cae a la voz local (System.Speech) para esa clave.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _cargar_env(env_path: Path) -> None:
    """Carga pares CLAVE=valor de un .env simple en os.environ, sin pisar variables ya definidas."""
    if not env_path.exists():
        return
    for linea in env_path.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        if clave and clave not in os.environ:
            os.environ[clave] = valor.strip()


def pedir_tts(texto: str, api_key: str, voice_id: str, model_id: str, version: str, lang: str, velocidad: str = "fast") -> bytes:
    cuerpo = json.dumps(
        {
            "model_id": model_id,
            "transcript": texto,
            "voice": {"mode": "id", "id": voice_id, "__experimental_controls": {"speed": velocidad}},  # Ricardo 22:50: "un poco mas rapida"
            "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
            "language": lang,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.cartesia.ai/tts/bytes",
        data=cuerpo,
        method="POST",
        headers={
            "X-API-Key": api_key,
            "Cartesia-Version": version,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--textos", default=str(RAIZ / "docs/video/narracion_textos.json"))
    ap.add_argument("--salida-dir", default=str(RAIZ / "reportes/video/narracion_wav"))
    ap.add_argument("--velocidad", default="fast", choices=["slow", "normal", "fast"], help="control de velocidad de Cartesia (default fast)")
    ap.add_argument("--voice-id", default="a7beff01-8f8b-4809-bfe6-e2166e57e0c2")  # Iria (Ricardo, 24/09 22:45)
    ap.add_argument("--model-id", default="sonic-2")
    ap.add_argument("--version", default="2024-06-10")
    ap.add_argument("--lang", default="es", help="codigo de idioma para Cartesia (es/en)")
    ap.add_argument("--campo", default="es", help="clave del texto en el JSON a locutar (es/en)")
    ap.add_argument("--solo", default=None, help="generar solo esta clave (para pruebas)")
    args = ap.parse_args()

    _cargar_env(RAIZ / ".env")
    api_key = os.environ.get("CARTESIA_API_KEY")
    if not api_key:
        print("FALTA CARTESIA_API_KEY en el entorno o en .env", file=sys.stderr)
        return 2

    textos = json.loads(Path(args.textos).read_text(encoding="utf-8"))
    salida_dir = Path(args.salida_dir)
    salida_dir.mkdir(parents=True, exist_ok=True)

    fallas = []
    for clave, item in textos.items():
        if args.solo and clave != args.solo:
            continue
        destino = salida_dir / f"{clave}.wav"
        try:
            audio = pedir_tts(item[args.campo], api_key, args.voice_id, args.model_id, args.version, args.lang, args.velocidad)
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode("utf-8", errors="replace")[:300]
            print(f"FALLO {clave}: HTTP {e.code} {cuerpo}", file=sys.stderr)
            fallas.append(clave)
            continue
        except Exception as e:  # noqa: BLE001
            print(f"FALLO {clave}: {e!r}", file=sys.stderr)
            fallas.append(clave)
            continue
        destino.write_bytes(audio)
        print(f"OK {clave} -> {destino} ({len(audio)} bytes)")

    if fallas:
        print("Tramos sin audio de Cartesia (usar voz local como reemplazo):", fallas, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
