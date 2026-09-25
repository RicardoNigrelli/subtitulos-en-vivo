# Guion de narración — corte v2 (pipeline reproducible)

Decisión de Ricardo (24/09, 20:55 AR, aplicada a mitad de bloque): este bloque **no sube el video
hoy**. El entregable es el **pipeline reproducible** (`docs/video/componer.py` +
`docs/video/narrar_cartesia.py` + `docs/video/generar_offset_map.py` + este guion) más un corte de
prueba (`reportes/video/corte-v2.mp4`) que mañana se vuelve a correr con la grabación final. Por eso:

- La **narración está en español rioplatense** (voz de Cartesia; ver "Voz" abajo), no en inglés.
- Los **subtítulos quemados están en inglés y salen del propio proyecto (R14)**, no los escribió a
  mano un agente: se generaron pasando la narración por `worker.run` (ASR + traducción ES→EN reales,
  con cuota de Gemini autorizada una vez por el orquestador) y exportando con
  `docs/export/exportar.py`. Ver "Subtítulos EN (R14)" abajo para los comandos exactos.
- Ningún número de este guion se inventó: cada uno cita el reporte/comando de origen. Los números NO
  se muestran como texto en pantalla, sólo se dicen en la narración (regla `entrega`).
- Duración final del corte de prueba: **94,06 s** (`reportes/video/corte-v2-timeline.json`), dentro
  del rango 90–110 s pedido.
- **Hallazgo de infraestructura (no de contenido), causa raíz encontrada y arreglada:** un filtro
  `apad` sin acotar (`amix...,apad,atrim=0:N,alimiter=...`) colgaba el proceso de ffmpeg al cerrar en
  esta máquina (probado y descartado antes: no era `drawtext`, no era sólo contención de CPU de otros
  agentes en paralelo — ver el detalle completo, con las pruebas de aislamiento, en
  `reportes/demo-video-v2.md`). Se arregló acotando el filtro mismo (`apad=whole_dur=N`). Con eso, la
  corrida completa (9 tramos + concat + subtítulos quemados) tardó ~20 s reales. `componer.py` además
  guarda, como colchón, `-nostdin`, MP4 fragmentado y una validación de duración por `ffprobe` antes
  de aceptar cualquier tramo cuyo proceso no cierre solo.

## Texto por tramo (español, tal cual locutado)

Los tiempos de la columna "video" son los del corte de PRUEBA `reportes/video/corte-v2.mp4`
(`reportes/video/corte-v2-timeline.json`, tramos `n*_*`); van a cambiar mañana con la grabación
final porque `componer.py` calcula la duración de cada tramo a partir del WAV de narración real.

### n1_portada — INTRO (tarjeta) · video 0,00–12,27 s

> Subtítulos en vivo para conferencias, con Gemini. Audio real de una charla de Nerdearla, dos salas
> en paralelo, desde cualquier celular, sin instalar nada.

Cita: R13a (audio real de una charla), R21 (dos sesiones en simultáneo).

### n2_vivo — LIVE, pantalla dividida (tarjeta 2,5 s + metraje sincronizado 16 s) · video 12,27–30,77 s

> Cada sala envía su audio a un worker que habla con la API Live de Gemini. En pantalla, la sesión
> en inglés: los subtítulos aparecen con una demora mediana de punta a punta de medio segundo en
> nuestra prueba con dos sesiones.

Cita: `qa/smoke.py` → `qa/out/smoke-b5r.log`: percibida p50 = 0,443 s (`video-en`) / p50 = 0,429 s
(`video-es`) — la narración redondea a "medio segundo"; el número exacto es éste, no otro.
Sincronía: charla (talk) t=300 s = orador t=5 s = borrador t≈7,5 s (dato del brief, verificado con
fotogramas); pantalla dividida usa orador [24,5–40,5] s y vista EN del borrador [27–43] s (mismo
instante de la charla en las dos mitades de pantalla).

### n3_indice — ÍNDICE (metraje, tramo 16–27 del borrador) · video 30,77–41,77 s

> Los asistentes escanean un código QR y eligen su sala y su idioma desde un índice simple. Sin
> login, sin instalar nada: funciona en cualquier navegador.

Cita: R6 (índice de sesiones + idioma en la URL), R20 (mostrar los subtítulos en la web).

### n4_replay — REPLAY (tarjeta 3 s + vista ES replay, tramo 46–62 del borrador) · video 41,77–62,32 s

> Esta pista en español es un replay: grabado antes con el mismo pipeline exacto, no es
> reconocimiento de voz en vivo, siempre rotulado en pantalla. La traducción corre sobre el texto
> final en inglés con Gemini Flash-Lite, en lotes pequeños, y queda varios segundos detrás de los
> subtítulos en vivo en nuestras pruebas.

Cita: rótulo "REPLAY" superpuesto en pantalla (PNG generado por `docs/video/generar_banners.py` +
filtro `overlay` en `docs/video/componer.py`; no `drawtext`, ver nota de infraestructura arriba) +
rótulo propio de la vista (`REPLAY ·`, ya generado por el frontend al grabar el borrador). Atraso de traducción:
ESTADO.md, fila "R19 EN VIVO, segunda medición" → `worker.medir_traduccion
qa/out/smoke-b5r-qa-b5r-*.casete.jsonl`: atraso p50 9,6 s (en) / 8,8 s (es), p95 20,6–21,8 s — la
narración dice "varios segundos", nunca un número que no esté en este comando.

### n5_panel — PANEL (tarjeta 3 s + vista panel, tramo 65–79 del borrador) · video 62,32–79,32 s

> Un panel de operación mide latencia p50 y p95 por sala, nunca un promedio simple, además de
> rotaciones de sesión mostradas como eventos transparentes y esperables, y alertas cuando una sala
> queda en silencio.

Cita: texto propio del panel, `panel/index.html:29` ("p50/p95 = nearest-rank, nunca promedio");
"rotación transparente, no falla del proveedor" es la decisión explícita de Ricardo (`ESTADO.md`,
sección "Decisiones y porqué", 14:55 AR) — el guion la respeta al pie de la letra.

### n6_cierre — CIERRE (tarjeta, cómo se usa + licencia) · video 79,32–94,06 s (final)

> Un solo comando, con tu propia clave de API de Gemini, levanta todo el stack. Sin clave, arranca
> en modo replay, claramente rotulado. Apache 2.0, código abierto, en GitHub.

Cita: R16 (README cómo levantar), R15 (licencia OSI); modo replay sin key verificado en
`reportes/ops-b6-compose.log` (`MODO=replay docker compose ... up`, exit 0).

## Voz (Cartesia)

- Probada con una llamada corta antes de generar los 6 tramos: primero `GET /voices` → HTTP 200
  (988 voces); una `POST /tts/bytes` de prueba en inglés (antes del pivot a narración en español) →
  HTTP 200, `audio/wav`, 217166 bytes; después del pivot, `--solo n1_portada` (ya en español, voz
  definitiva) fue la primera llamada real → HTTP 200, 1011790 bytes, y recién ahí se generaron los
  5 tramos restantes.
- `Cartesia-Version: 2024-06-10`, `model_id: sonic-2`, `language: es`.
- Voz elegida: `4853bafa-52cc-48c8-86a1-1edf8c76e429` ("Alonso - Podcast Explainer", catálogo de
  Cartesia como idioma `es` genérico). **Aviso honesto:** el catálogo de voces de Cartesia (988
  voces descargadas con `GET /voices`) no trae ninguna marcada explícitamente como
  "Argentina"/"rioplatense" (se buscó por texto en nombre/descripción, sin resultados); se eligió la
  voz `es` con la descripción más neutra ("clear... narration"), no se pudo verificar el acento por
  oído porque este agente no reproduce audio. Ricardo puede escuchar los WAV en
  `reportes/video/narracion_wav/*.wav` y pedir otra voz del catálogo si no convence.
- Si `CARTESIA_API_KEY` faltara, el reemplazo es la voz local de Windows
  (`Microsoft Helena Desktop`, `es-ES`, única voz en español instalada; ver
  `docs/video/narrar_cartesia.py` para el detalle de por qué no se usó: si llegó la key, se usa
  Cartesia siempre, según el brief).

## Subtítulos EN (R14): comandos exactos, en orden

Objetivo: subtítulos en inglés "hechos con el propio proyecto", no traducidos a mano, para los dos
jurados que no hablan español. Se corrió UNA vez, con cuota de Gemini autorizada por el orquestador
(hasta 2 minutos, una sola vez).

```
# 1) Concatenar los 6 WAV de narración (ES) en uno solo, 16 kHz mono (formato que espera worker.run)
ffmpeg -y -i reportes/video/narracion_wav/n1_portada.wav -i reportes/video/narracion_wav/n2_vivo.wav \
  -i reportes/video/narracion_wav/n3_indice.wav -i reportes/video/narracion_wav/n4_replay.wav \
  -i reportes/video/narracion_wav/n5_panel.wav -i reportes/video/narracion_wav/n6_cierre.wav \
  -filter_complex "[0:a][1:a][2:a][3:a][4:a][5:a]concat=n=6:v=0:a=1[a]" \
  -map "[a]" -ac 1 -ar 16000 reportes/video/narracion-es.wav
# duración real: 85,96 s (ffprobe) -> bien dentro de los 2 min autorizados

# 2) ASR (es) + traducción (es->en) reales, vía el hub, con la ventana de cuota que dio el orquestador
CUOTA_BLOQUE_DESDE="2026-09-24 20:50:00" CUOTA_BLOQUE_S=600 \
  .venv/Scripts/python -m worker.run --archivo reportes/video/narracion-es.wav \
  --sesion narracion-v2 --lang es --duracion 86 \
  --casete fixtures/casetes/narracion-v2-es.jsonl --hub ws://localhost:8100/ingest

# 3) Confirmar que las 34 líneas tienen translations.en con ok:true (ninguna quedó "[sin traducir]")
curl -sS "http://localhost:8100/api/sesiones/narracion-v2/historial?desde=0"

# 4) Mapa de offsets: de "tiempo en el WAV de trabajo" a "tiempo en el video final" (por tramo)
.venv/Scripts/python docs/video/generar_offset_map.py \
  --timeline reportes/video/corte-v2-timeline.json \
  --salida reportes/video/narracion-offset-map.json

# 5) Exportar el SRT en inglés, YA alineado al video final
.venv/Scripts/python docs/export/exportar.py --hub http://localhost:8100 --sesion narracion-v2 \
  --formato srt --lang en --offset-map reportes/video/narracion-offset-map.json \
  --salida docs/video/narracion-en.srt

# 6) Validar el SRT con ffmpeg (exit 0 = subtítulos bien formados; un .srt no tiene audio/video
#    propio, hace falta -map 0:s explícito o este build dice "Output file does not contain any stream")
ffmpeg -v error -i docs/video/narracion-en.srt -map 0:s -c:s copy -f null -
```

Resultado de la corrida real (24/09, ~21:01 AR): 34 líneas `type=text`, las 34 con
`translations.en.ok = true` (0 líneas `[sin traducir]`; si hubiera quedado alguna, este documento lo
diría en vez de corregirla a mano, como pide el brief). Traductor: 18 llamadas, 17 lotes, 0 lotes con
`ok:false`, repartidas entre `gemini-3.5-flash-lite` (10) y `gemini-3.1-flash-lite` (8) — un 5xx
transitorio de `gemini-3.1-flash-lite` (18,7 s) fue reintentado por el propio worker y no dejó
ninguna línea sin traducir. Un artefacto real de ASR (no corregido a mano): la línea 4 transcribió
"Nerdearla" duplicado ("de NerdearlaNerdearla, dos salas...") — imperfección auténtica del
reconocimiento de voz de Gemini sobre la voz sintética, documentada tal cual salió.

El mismo SRT (`docs/video/narracion-en.srt`) se quema en el video con `docs/video/componer.py
--subs docs/video/narracion-en.srt` (default) y queda además como archivo aparte para cargar en
YouTube como pista de closed captions en inglés. Copia con el nombre del entregable original del
brief: `docs/video/narracion.srt` (mismo contenido).
