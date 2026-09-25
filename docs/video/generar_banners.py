"""Genera los rotulos (PNG, 1920x1080, fondo transparente) que `componer.py` superpone con
`overlay` sobre la pantalla dividida (LIVE) y la vista de replay (REPLAY).

Se usa PIL/Pillow en vez de `drawtext` de ffmpeg: en esta maquina `drawtext` cuelga el proceso de
ffmpeg al cerrar (probable init de fontconfig en el build nightly), aunque el archivo de salida ya
este completo; ver `reportes/demo-video-v2.md`. `overlay` con un PNG pre-renderizado no toca
fontconfig y es reproducible igual.

Uso:
    .venv/Scripts/python docs/video/generar_banners.py --salida-dir reportes/video/tmp_v2
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[2]
W, H = 1920, 1080


def _fuente(tam: int) -> ImageFont.FreeTypeFont:
    for c in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"):
        if Path(c).exists():
            return ImageFont.truetype(c, tam)
    return ImageFont.load_default()


def _banner(texto: str, color_texto: str, color_caja: tuple[int, int, int, int], y_centro: int,
            tam_fuente: int = 34) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _fuente(tam_fuente)
    bbox = d.textbbox((0, 0), texto, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 22, 14
    caja = (W // 2 - tw // 2 - pad_x, y_centro - th // 2 - pad_y,
            W // 2 + tw // 2 + pad_x, y_centro + th // 2 + pad_y)
    d.rectangle(caja, fill=color_caja)
    d.text((W // 2 - tw // 2 - bbox[0], y_centro - th // 2 - bbox[1]), texto, font=f, fill=color_texto)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida-dir", default=str(RAIZ / "reportes/video/tmp_v2"))
    args = ap.parse_args()
    out = Path(args.salida_dir)
    out.mkdir(parents=True, exist_ok=True)

    live = _banner("LIVE - Gemini Live API - session video-en", "white", (0, 0, 0, 160), y_centro=48)
    live.save(out / "banner_live.png")

    replay = _banner("REPLAY - pre-recorded with the same pipeline, not live ASR",
                      "#ffd166", (0, 0, 0, 175), y_centro=H - 90)
    replay.save(out / "banner_replay.png")

    print(f"OK -> {out / 'banner_live.png'}, {out / 'banner_replay.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
