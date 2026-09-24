# Fuentes de audio (R17b, R13a)

Charlas públicas del canal de YouTube de Nerdearla (`youtube.com/@nerdearla`). En el repo van SOLO
clips cortos de 60 s, citados con URL y minuto (decisión de Ricardo 24/09 12:55, X3). Las charlas
enteras se bajan a `fixtures/audio/full/`, que git ignora por la regla `fixtures/audio/full/` del `.gitignore` de la raíz.

| Slug | Charla | Orador | URL | Duración | Publicado | Pista usada | Descargado |
|---|---|---|---|---|---|---|---|
| `nerdearla-en-booch` | The Third Golden Age | Grady Booch | https://www.youtube.com/watch?v=cPaqkFCqWeg | 1964 s (32:44) | 2026-04-16 | `140` m4a, `[en-US] English (US) original (default)` | 24/09/2026 13:06 AR |
| `nerdearla-es-paez` | Brownfield Engineering: cómo cambiar código que da miedo cambiar | Nicolas Paez | https://www.youtube.com/watch?v=V2YxvP-XXEc | 1502 s (25:02) | 2026-09-23 | `140-1` m4a, `[es-US] Spanish (US) original (default)` (el video trae además un doblaje automático en inglés, `140-0`, que NO se usa) | 24/09/2026 13:06 AR |

El idioma se confirmó con `yt-dlp -F`: la pista marcada "original (default)" trae el código de idioma
(skill `ingesta`: no confiar en `bestaudio`, YouTube sirve doblajes automáticos).

## Clips en el repo (`fixtures/audio/clips/`, WAV PCM s16le 16 kHz mono)

| Archivo | Tramo de la charla | Uso |
|---|---|---|
| `nerdearla-en-booch-300s-60s.wav` | 05:00–06:00 de cPaqkFCqWeg | corrida real de 60 s EN (casete `fixtures/casetes/b1-en-60s.jsonl`) |
| `nerdearla-es-paez-300s-60s.wav` | 05:00–06:00 de V2YxvP-XXEc | corrida real de 60 s ES |

## Tramos usados para los casetes largos (desde el archivo completo, no se commitea)

Se arrancó en 05:00 (300 s) para evitar la intro; el tope pedido era 1050 s (17:30) o GoAway/cierre.
NINGUNA sesión llegó al GoAway: el server cerró antes (ver `reportes/audio-pipeline-b1.md` y
`reportes/adversario-b1-pasada2.md`). Casetes que quedaron, con el cierre crudo que traen:

| Casete | Archivo | Desde | Audio enviado | Cierre del server |
|---|---|---|---|---|
| `b1-nerdearla-en-intento1-1011.jsonl` | `full/nerdearla-en-booch.m4a` | 300 s | 77,0 s | 1011 Internal error |
| `b1-nerdearla-en-intento2-quota.jsonl` | `full/nerdearla-en-booch.m4a` | 300 s | 449,8 s | 1011 Resource exhausted |
| `b1-nerdearla-es-intento1-1006.jsonl` | `full/nerdearla-es-paez.m4a` | 300 s | 118,7 s | 1006 sin frame de cierre |
| `b1-nerdearla-es-intento2-cancelled.jsonl` | `full/nerdearla-es-paez.m4a` | 300 s | 536,5 s | 1000 cancelled |

El perfil de RMS por 10 s de los primeros 15 min no mostró silencios largos en ninguna de las dos charlas.

## Cómo reproducir (desde la raíz, venv)

```bash
# bajar la pista ORIGINAL una sola vez (Avast: usar el almacén de Windows, no certifi)
.venv/Scripts/python -m yt_dlp --compat-options no-certifi -F "https://www.youtube.com/watch?v=cPaqkFCqWeg"
.venv/Scripts/python -m yt_dlp --compat-options no-certifi -f 140 -o "fixtures/audio/full/nerdearla-en-booch.%(ext)s" "https://www.youtube.com/watch?v=cPaqkFCqWeg"
.venv/Scripts/python -m yt_dlp --compat-options no-certifi -f 140-1 -o "fixtures/audio/full/nerdearla-es-paez.%(ext)s" "https://www.youtube.com/watch?v=V2YxvP-XXEc"
# recortar los clips
ffmpeg -ss 300 -t 60 -i fixtures/audio/full/nerdearla-en-booch.m4a -vn -ac 1 -ar 16000 -acodec pcm_s16le fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav
ffmpeg -ss 300 -t 60 -i fixtures/audio/full/nerdearla-es-paez.m4a -vn -ac 1 -ar 16000 -acodec pcm_s16le fixtures/audio/clips/nerdearla-es-paez-300s-60s.wav
```
