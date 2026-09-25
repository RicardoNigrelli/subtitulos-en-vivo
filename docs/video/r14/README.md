# R14: subtítulos del video hechos con el propio proyecto

Los subtítulos de los dos videos no los escribió nadie a mano: salen de `worker.traductor.Traductor`, el
mismo traductor que corre en vivo en cada sala, aplicado al texto exacto de cada línea narrada, con los
tiempos medidos sobre el audio de la narración. Si una línea vuelve `ok:false`, el script falla y no se
escribe el SRT (nada se corrige a mano).

| Carpeta | Narración | Subtítulos | Script | Entradas en el repo |
|---|---|---|---|---|
| `video1-narracion-es/` | español | inglés (`narracion-en-102s.srt`) | `srt_por_traductor.py` | `textos.json`, `offsets.json` |
| `video2-narracion-en/` | inglés | español (`narracion-es.srt`) | `armar.py` | `textos.json`, `offsets.json` |

Reproducir (desde la raíz, con `.venv` y `GEMINI_API_KEY` en `.env`; gasta cuota del modelo de texto, 0 min de audio):

```bash
.venv/Scripts/python docs/video/r14/video1-narracion-es/srt_por_traductor.py
```

`armar.py` además mezcla los WAV de la voz (`wav/<clave>.wav`, no versionados) en `pista.wav`; sin ellos, la
parte reproducible es la traducción, que es la que importa para R14. Los `.json` junto a cada SRT guardan el
par original/traducción por línea tal como lo devolvió el modelo (`modelo`, `ms` e `intentos` se imprimen al
correr). Los subtítulos de la CHARLA dentro del video (no de la narración) son los que emitió el pipeline en
vivo durante las tomas: casetes en `fixtures/casetes/evidencia-25-09/` (ver `docs/evidencia.md`).

Herramientas de composición del video: `docs/video/componer.py` (ffmpeg). No se usa Remotion ni ninguna
herramienta con licencia no OSI en el repo.
