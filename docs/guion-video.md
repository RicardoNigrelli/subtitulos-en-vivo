# Guion del video (R13a, R13b, R13c)

Fuente única de números: `ESTADO.md` (sección "Hechos verificados" y tabla MVP). Cada afirmación de
este guion cita la fila de `ESTADO.md` que la respalda, o el R#/C# de `REGLAS.md` cuando es una
descripción de alcance (no un número). Nada de lo que sigue inventa una cifra: donde el número
depende de la corrida en vivo del día de grabación (no reproducible hoy 24/09), se cita la fila que
verifica el MECANISMO, no un resultado puntual.

Pensado para **dos jurados que no hablan español (R13a-c)**: cada rótulo en pantalla va en
ES + EN. La narración puede ser en español (Ricardo); lo que hay que entender sin audio queda en el
rótulo. El SRT en inglés del propio video (R14) es el Bloque 10 de mañana (`reportes/plan.md`, fila
B10) y cierra la parte que dependa del audio hablado.

Este guion evita las frases que el skill `entrega` marca como prohibidas (afirmar un costo como si
estuviera medido cuando en realidad `usage_metadata` no llega, dar una cifra de error de
transcripción como si fuera absoluta, generalizar el intervalo de aparición de los subtítulos, o
citar cualquier número sin comando). Verificación exacta en `reportes/demo-b4.md` §2.

---

## Parte A — BORRADOR de hoy, grabación 18:50 AR (60–90 s)

### 0. Antes de grabar (checklist operativo, no es guion)

1. **Puertos libres antes de levantar nada** (regla Windows de `CLAUDE.md`: el bind no falla si el
   puerto está tomado):
   ```
   netstat -ano | findstr :8100
   netstat -ano | findstr :8101
   netstat -ano | findstr :8102
   ```
2. **Hub limpio para la grabación.** El hub de desarrollo (PID 30224 a las 15:40 AR, ver
   `ESTADO.md` "Qué está a medias") acumuló ~27 sesiones de prueba de B1–B3
   (`curl localhost:8100/api/sesiones`, corrido 24/09 15:5x AR: `adv-b2-*`, `b2-*`, `b3-*`, `qa-*`,
   `qa-wd-*`). El índice web (`web/index.html`) todavía NO filtra por el campo `test` que backend
   agregó este bloque (`grep -n '"test"' hub/nucleo.py` → B4; `grep -n '\.test\b' web/app.js` → sin
   coincidencias de filtro, corrido 24/09 15:57 AR) — **pedido cruzado a frontend/orquestador**, no
   lo resuelvo yo (no escribo `web/`). Mitigación operativa para hoy: reiniciar el hub justo antes
   de grabar para que el índice sólo muestre las sesiones nuevas:
   ```
   taskkill //PID <pid-del-hub-viejo> //F
   .venv\Scripts\python -m hub
   ```
3. **Servidores estáticos** (cada uno verifica su puerto libre primero, paso 1):
   ```
   .venv\Scripts\python web\servir.py --puerto 8101
   .venv\Scripts\python panel\servir.py --puerto 8102
   ```
4. **Preparar (sin correrlo todavía) el tramo REPLAY** que se muestra en 0:35–0:50 — ver §2. Se
   dispara EN VIVO durante la grabación, un rato antes de mostrarlo en pantalla, para que ya haya
   líneas cuando se corte a esa ventana:
   ```
   .venv\Scripts\python -m worker.replay fixtures\casetes\b1-en-60s-rederivado-trad.jsonl --hub ws://localhost:8100/ingest --sesion video-replay-es
   ```
   **VERIFICADO hoy** (no gasta cuota, no toca Gemini): corrida real de este comando (con
   `--sesion demo-guion-replay-es`, mismo casete) → `exit=0`, `{"mensajes": 30, "seq_creciente":
   true, "todos_replay": true, "exit": 0}`; `GET /api/sesiones` muestra
   `"title": "REPLAY · The Third Golden Age - Grady Booch (clip 300s)"` (el prefijo "REPLAY ·" lo
   pone `worker/replay.py:35,62-63` solo); `GET .../historial` trae `translations.es.text` ya
   mergeada por el hub (ej. seq 2: "era la función. Ahora, puede que digan", `ok:true`). Corrido
   24/09 15:5x AR, ver `reportes/demo-b4.md` §1.
5. **Audio real durante la grabación (R13a):** el `worker.run`/`worker.replay` NO reproduce audio
   por los parlantes, sólo lee el archivo y lo manda a Gemini o al hub. Para que el VIDEO tenga
   audio real de la charla, reproducir el mismo clip por los parlantes (o como fuente de audio en
   OBS) mientras se muestra esa sesión en pantalla:
   `fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav` (0:20–0:35) y, si entra,
   `fixtures/audio/clips/nerdearla-es-paez-300s-60s.wav`. Fuente y minuto exacto de cada clip:
   `fixtures/audio/FUENTES.md` (tabla "Clips en el repo").
6. **Dos sesiones REALES en simultáneo (R21, R17a), arrancadas ~5 s antes de grabar** para que ya
   figuren en el índice cuando empieza OBS. Comandos verificados por audio-pipeline (mismos flags
   que `reportes/audio-pipeline-b1.md` §2 y §3, sólo cambia `--sesion`/`--casete` para no pisar la
   evidencia de B1):
   ```
   .venv\Scripts\python -m worker.run --archivo fixtures\audio\clips\nerdearla-en-booch-300s-60s.wav --sesion video-en --lang en --duracion 60 --casete fixtures\casetes\video-en-60s.jsonl --hub ws://localhost:8100/ingest --titulo "The Third Golden Age - Grady Booch (clip 300s)" --url "https://www.youtube.com/watch?v=cPaqkFCqWeg" --vocab "Nerdearla,Grady Booch"
   .venv\Scripts\python -m worker.run --archivo fixtures\audio\clips\nerdearla-es-paez-300s-60s.wav --sesion video-es --lang es --duracion 60 --casete fixtures\casetes\video-es-60s.jsonl --hub ws://localhost:8100/ingest --titulo "Brownfield Engineering - Nicolas Paez (clip 300s)" --url "https://www.youtube.com/watch?v=V2YxvP-XXEc" --vocab "Nerdearla,Nicolás Páez,brownfield" --tope-envio-s 80
   ```
   Consumen ~2 min de audio real de los ~5 min de cuota reservados para el envío borrador
   (`reportes/plan.md`, tabla de bloques, fila B6 "Cuota min audio: 5"). **Esto lo corre Ricardo (o
   audio-pipeline con su autorización) al momento de grabar; yo (demo) no llamo a Gemini este
   bloque** (instrucción del bloque B4, "Sin API de Gemini").

### 1. Minutado

| Tiempo | En pantalla | Se dice / rótulo (ES · EN) | Tipo | Cita |
|---|---|---|---|---|
| 0:00–0:07 | Título del proyecto sobre fondo simple | ES: "Subtítulos en vivo para conferencias, con Gemini." · EN: "Live captions for conferences, powered by Gemini." | — | R13b (qué hace); R3/R4 (alcance del desafío) |
| 0:07–0:20 | Índice `localhost:8101/` con **2 sesiones reales** (`video-en`, `video-es`) recién arrancadas | ES: "Recibe audio real y arranca varias sesiones en paralelo." · Rótulo: "2 SESIONES EN VIVO · 2 LIVE SESSIONS" | VIVO (ASR real) | R17a, R21; mecanismo verificado en fila ESTADO.md "R21 con ASR REAL, smoke N=2" (cobertura 1,0, `seq` continuo, sin rotaciones) — los NÚMEROS de esa fila son de la corrida de qa del 24/09 15:15 AR, no de esta grabación; lo que se reutiliza es el mecanismo, no el resultado |
| 0:20–0:35 | Clic en `video-en` → `localhost:8101/s/video-en`, texto en inglés apareciendo en vivo, audio real de fondo (parlantes, ver §0.5) | ES: "Transcribe en el idioma original en tiempo real." · Rótulo: "TRANSCRIPCIÓN EN VIVO · LIVE TRANSCRIPTION" | VIVO (ASR real) | R18; fila ESTADO.md "R17a + R18 con ASR REAL" (mecanismo; misma línea de comando que corre el video, con `--sesion`/`--casete` distintos, ver §0.6) |
| 0:35–0:50 | Corte a `localhost:8101/s/video-replay-es?lang=es`: el título de la página muestra **"REPLAY · The Third Golden Age…"**, subtítulos en ESPAÑOL scrolleando | ES: "Y traduce de inglés a español." · Rótulo: "REPLAY — grabado antes, no es ASR en vivo · REPLAY — pre-recorded, not live ASR" | **REPLAY, rotulado en pantalla por el propio título** | R19 (existe la traducción EN→ES, con el contrato `translations`/`items` mergeado por el hub); fila ESTADO.md "Hub 8100… merge de traducciones 11/11 y 14/14" (el hub aplicó las 11 traducciones del casete `b1-en-60s-rederivado-trad.jsonl` a sus líneas); comando y verificación de HOY en §0.4. Se usa replay a propósito: la fila ESTADO.md "R19 EN VIVO NO CUMPLE (smoke 2 sesiones)" documenta que la traducción en vivo todavía llega tarde; mostrar el replay (rotulado) evita afirmar en el video algo que hoy no se sostiene en vivo |
| 0:50–1:00 | `localhost:8102/` panel: filas de las sesiones, columnas p50/p95, contador de reaperturas con motivo | ES: "Un panel monitorea latencia y reconexiones." · Rótulo: "PANEL DE MONITOREO · MONITORING PANEL — rotación transparente de sesiones Live · transparent Live-session rotation" | VIVO | R8e; fila ESTADO.md "Panel (R8e) en 8102…" (cálculo de p50/p95 verificado dígito a dígito contra `panel/recalcular.py`) y fila "Reabrir con solape…" (el mecanismo que el contador de reaperturas refleja). Frase de Ricardo (14:55, `ESTADO.md` "Decisiones y porqué"): la rotación se presenta como mecanismo transparente, nunca como falla del proveedor |
| 1:00–1:10 | Vuelta al índice, zoom al selector de idioma de una sesión | ES: "Cada persona elige sesión e idioma. Código abierto, con licencia Apache 2.0." · Rótulo: "REPO: <REPO_URL>" | VIVO | R6 (selector); R15 (licencia) — el marcador `<REPO_URL>` se reemplaza cuando ops confirme el repo (B6/B7) |

Total: ~70 s (dentro de 60–90 s, R13a).

### 2. Casete/clip por tramo (para quien re-grabe o re-edite)

| Tramo | Fuente | Detalle |
|---|---|---|
| 0:07–0:35 (vivo, EN) | `fixtures/audio/clips/nerdearla-en-booch-300s-60s.wav` | 05:00–06:00 de https://www.youtube.com/watch?v=cPaqkFCqWeg (Grady Booch, "The Third Golden Age"), citado en `fixtures/audio/FUENTES.md` |
| 0:07–0:20 (vivo, ES, de fondo en el índice) | `fixtures/audio/clips/nerdearla-es-paez-300s-60s.wav` | 05:00–06:00 de https://www.youtube.com/watch?v=V2YxvP-XXEc (Nicolás Páez, "Brownfield Engineering"), citado en `fixtures/audio/FUENTES.md` |
| 0:35–0:50 (REPLAY, ES) | `fixtures/casetes/b1-en-60s-rederivado-trad.jsonl` | Casete de B2 (`reportes/audio-pipeline-b2.md`): mismo clip de Booch, ASR real + traducción real offline, 11/11 líneas con `ok:true` (fila ESTADO.md "Hub 8100…merge de traducciones 11/11 y 14/14") |

### 3. Verificación de este bloque (evidencia de cierre)

Ver `reportes/demo-b4.md` para las 5 líneas (afirmación · comando · salida · exit · hora) de cada
punto de arriba, incluida la corrida real de `worker.replay` y el `grep` de frases prohibidas.

---

## Parte B — ESQUELETO del final, grabación 06:15 AR de mañana (1–2 min)

Mismo esquema de la Parte A (setup, minutado, tabla de fuentes), más:

1. **R14 — Subtítulos en inglés del propio video.** Después de grabar el final, correr el pipeline
   ES→EN del proyecto sobre la pista de narración del video (no sobre una charla de Nerdearla) y
   exportar a SRT con `docs/export/exportar.py` (este bloque, B4). Comando exacto y verificación:
   Bloque 10 (`reportes/plan.md`, fila B10 "06:45 SRT EN con el propio proyecto"). Requiere minutos
   de la reserva de Gemini (`GEMINI_API_KEY_RESERVA`, `.claude/skills/cuota-gemini/SKILL.md`).
2. **ES→EN, si entra en 1–2 min.** Mismo patrón que 0:35–0:50 pero con
   `fixtures/casetes/b1-es-60s-trad.jsonl` (charla de Nicolás Páez, ES) y `--lang en`:
   ```
   .venv\Scripts\python -m worker.replay fixtures\casetes\b1-es-60s-trad.jsonl --hub ws://localhost:8100/ingest --sesion video-replay-en
   ```
   luego `localhost:8101/s/video-replay-en?lang=en`. Fila ESTADO.md a citar: la misma de merge
   (11/11 y 14/14) — el casete ES tiene 14 líneas de texto, y de esas, según
   `reportes/audio-pipeline-b2.md` §1 (no es una fila de `ESTADO.md`; citar el reporte, no un R#),
   11 tienen traducción `ok:true` y 3 quedan `ok:false` (marcadas "[sin traducir]" por
   `docs/export/exportar.py`, mismo criterio que `web/app.js`). Verificado hoy con
   `docs/export/exportar.py --casete fixtures/casetes/b1-es-60s-trad.jsonl --formato srt --lang en`:
   3 de 14 bloques con la marca (`reportes/demo-b4.md` §1).
3. **Nuevo minutado 1–2 min**, agregando entre 1:00 y el cierre: tramo ES→EN (arriba) y, si el
   checkpoint de B5 lo permite, 2–3 s de un evento de reapertura real en el panel (contador subiendo
   de 0 a 1 con motivo) para mostrar la "rotación transparente" en vivo y no sólo nombrarla. Si no
   hay tiempo de disparar una reapertura real durante la grabación, se mantiene el rótulo estático
   de la Parte A (no se inventa un evento que no ocurrió en cámara).
4. **README y `PROMPTS.md`** ya enlazados y con contenido final (B6/B7/B9): el cierre del video
   apunta a `<REPO_URL>` y menciona "cómo escalar" (README, tres ejes de `contexto`) sin citar un
   número de techo de sesiones que no esté en `ESTADO.md` al momento de grabar.
5. Repetir el chequeo de frases prohibidas y el `ffmpeg` sobre el SRT final antes de subir a
   YouTube (R14) — mismo comando que en `reportes/demo-b4.md`, adaptado al ffmpeg build final;
   ver hallazgo de compatibilidad de `ffmpeg -f null -` en ese reporte §1.
