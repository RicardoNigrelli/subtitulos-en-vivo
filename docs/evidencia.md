# Evidencia de las corridas reales

Qué muestra este documento: las mejores corridas del sistema con ASR real, las tomas que se usaron en el
video, y las corridas que salieron de la media, con su causa medida. Regla de la casa: **ningún número sin
un comando que lo reproduzca** y un archivo donde quedó la salida. La única evidencia de "el servicio dijo
X" es el casete JSONL crudo de la corrida (`fixtures/casetes/`), nunca una cita en prosa.

## 1. Mejores corridas

| Qué | Resultado | Comando y salida |
|---|---|---|
| **R21: dos sesiones con ASR real en simultáneo**, 2 × 580 s de charla (24/09 16:50 AR; inglés, Grady Booch; español, Nicolás Páez) | Cobertura de texto **1,0 y 1,0** (ningún tramo con voz y sin texto). Latencia percibida **p50 0,443 / 0,429 s**, **p95 0,615 / 0,585 s**. 5 rotaciones de sesión, todas transparentes para la audiencia (`seq` continuo). | `qa/smoke.py --sesiones 2 --duracion 580 --inicio 300 --api --hub-externo --puerto 8100 --tope-envio-s 708 --clips fixtures/audio/full/nerdearla-en-booch.m4a,fixtures/audio/full/nerdearla-es-paez.m4a --langs en,es --sids qa-b5r-en,qa-b5r-es --tag b5r` → `qa/out/smoke-b5r.log` (README, "Reproducir los números de R21") |
| **R19: traducción en vivo**, lotes de 2 ventanas / 4 s, 2 × 60 s | Cobertura traducida **EN→ES 26/26, ES→EN 22/22**. Atraso de la traducción respecto del texto original **p50 2,64 / 2,68 s**, **p95 6,35 / 7,12 s**. | `.venv/Scripts/python -m worker.medir_traduccion fixtures/casetes/b8-trad-vivo-en.jsonl fixtures/casetes/b8-trad-vivo-es.jsonl` |
| **Tomas del video** (25/09 05:06–05:35 AR, una sala por vez, ASR real sobre clips de 60–90 s de las mismas charlas) | **0 reaperturas** en las 6 corridas de una sala por vez; primer texto entre **+5,2 s y +11,0 s** desde el arranque; 6 a 26 textos por toma (tabla de abajo). | `.venv/Scripts/python docs/resumen_casetes.py fixtures/casetes/evidencia-25-09/*.jsonl` |

Tomas de una sala por vez (salida del comando anterior, 25/09):

| Casete | Idioma | Audio enviado | Textos | Primer texto | Reaperturas |
|---|---|---|---|---|---|
| `video-vivo-en-050655` | en | 36,8 s | 6 | +5,5 s | 0 |
| `video-vivo-en-050903` | en | 71,6 s | 19 | +11,0 s | 0 |
| `video-vivo-es-051144` | es | 63,4 s | 21 | +10,4 s | 0 |
| `video-vivo-overlay-051351` | es | 63,4 s | 22 | +6,5 s | 0 |
| `sala-a-051845` | en | 87,0 s | 26 | +5,5 s | 0 |
| `simple-en-053454` | en | 69,2 s | 24 | +5,2 s | 0 |

## 1b. Cinco salas reales en simultáneo (25/09 08:02 AR)

Cinco `worker.run` a la vez contra un hub, 60 s de clip cada uno, arranques escalonados 5 s, tres salas con la key
principal y dos con una segunda key de otro proyecto (`--key GEMINI_API_KEY_RESERVA`). Las cinco conexiones a la
Live API abrieron y transcribieron; ninguna falló por cupo. Lo que sí se notó es el tope del traductor de texto del
nivel gratuito: las tres salas que compartían key tradujeron 78 %, 74 % y 50 % de sus líneas (8 lotes sin cupo), las
dos con key propia el 100 %. Es la medición que respalda la regla de "dos salas por key en nivel gratuito, o nivel
pago" del README.

| Casete | Key | Idioma | Audio enviado | Textos | Primer texto | Reaperturas | Traducido | Atraso traducción p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| `cinco-1-080158` | principal | en | 70,1 s | 27 | +5,1 s | 0 | 78 % | 4,7 s / 11,5 s |
| `cinco-2-080158` | principal | es | 68,8 s | 23 | +6,7 s | 0 | 74 % | 4,3 s / 21,4 s |
| `cinco-3-080158` | principal | en | 68,4 s | 10 | +6,9 s | 0 | 50 % | 7,2 s / 10,2 s |
| `cinco-4-080158` | reserva | en | 74,4 s | 13 | +6,7 s | 1 (atasco, 0 s perdidos) | 100 % | 4,2 s / 11,0 s |
| `cinco-5-080158` | reserva | es | 68,8 s | 23 | +7,0 s | 0 | 100 % | 3,5 s / 7,7 s |

Comandos: `.venv/Scripts/python docs/resumen_casetes.py fixtures/casetes/evidencia-25-09/cinco-*.jsonl` y
`.venv/Scripts/python -m worker.medir_traduccion fixtures/casetes/evidencia-25-09/cinco-*.jsonl`. Detalle y
comando de la corrida en `reportes/audio-pipeline-final.md` (no versionado) y en `worker/README.md`, "Cómo llegar a
5 y 10 salas". Sin investigar: la sala 3 dio 10 textos con el mismo clip que la sala 1 dio 27.

## 1c. Latencia: qué la fija y cómo bajarla (25/09 08:33–08:40)

Una sala, mismo clip (Grady Booch), cuatro configuraciones. "Primera palabra" es el tiempo desde que el
orador empieza un bloque de voz hasta que su texto está en pantalla; la traducción se suma encima.

| Config | Primera palabra → texto (p50 / p95) | Texto → traducción (p50 / p95) | Traducidas |
|---|---|---|---|
| A: ventana 3 s, traducción por lotes (config anterior) | 3,06 / 3,81 s | 3,16 / 5,86 s | 27/27 |
| B: ventana 3 s, traducción inmediata (hubo un atasco del server) | — | 2,38 / 6,18 s | 15/15 |
| D: ventana 2 s, traducción inmediata | 2,32 / 2,74 s | 5,26 / 11,80 s | 33/36 |

Qué dicen los números:

- **El original ya va a la par de un intérprete humano** (~3 s; la literatura de interpretación
  simultánea mide ~2–3 s de décalage). Con ventana de 2 s baja a 2,3 s sin perder texto.
- **La traducción la fija el cupo de llamadas del modelo de texto, no el modelo.** Con el tope del nivel
  gratuito (12 llamadas por minuto por modelo y por proyecto), la ventana de 2 s genera más líneas que
  llamadas disponibles y el traducido empeora; por eso el default quedó en ventana 3 s y traducción
  inmediata cuando hay margen, lotes cuando no.
- **En nivel pago ese tope desaparece**: se configuran los límites del proyecto
  (`TRADUCTOR_RPM`, `TRADUCTOR_TOPE_RPM`) y la ventana de 2 s (`VENTANA_S=2`), y cada línea se traduce
  sola al instante. Estimación con lo medido (no corrida en nivel pago): 2,3 s del original + 1,2–2,4 s
  de la llamada al modelo (p50 medido de `gemini-3.5-flash-lite`) ≈ **3,5–4,7 s** para el traducido,
  contra ~6 s de hoy en nivel gratuito.

Comandos: `.venv/Scripts/python -m worker.medir_latencia fixtures/casetes/evidencia-25-09/lat-*.jsonl` y
`.venv/Scripts/python -m worker.medir_traduccion fixtures/casetes/evidencia-25-09/lat-*.jsonl`.

## 2. Corridas fuera de la media: qué pasó y cómo lo cubre el sistema

No todas las corridas salieron como las de arriba. En la toma doble de las 05:16 (dos salas arrancadas en el
mismo segundo, misma key), las dos conexiones iniciales no devolvieron texto durante varios segundos:

| Casete | Idioma | Audio enviado | Textos | Primer texto | Reaperturas | Audio sin texto |
|---|---|---|---|---|---|---|
| `sala-a-051619` | en | 101,5 s | 21 | +32,3 s | 1 (atasco) | 12,1 s |
| `sala-b-051619` | es | 117,7 s | 9 | +51,0 s | 2 (atasco) | 52,1 s |

Lo que se midió sobre esos casetes (`reportes/audio-pipeline-doble.md`; comando abajo):

- **El envío de audio estaba a tiempo.** `python -m worker.cadencia <casete>` da un atraso máximo de envío
  de 0,016 s antes de cada atasco: el worker no se trabó.
- **La conexión dejó de devolver texto.** En esas conexiones el servicio no entregó ningún resultado y su
  `audioOffset` quedó en "0s"; es un comportamiento intermitente del servicio en la ventana medida, que se
  documenta sin pretender explicarlo.
- **El sistema lo cubrió solo.** El watchdog detectó el atasco, reabrió la conexión con solape y reenvió el
  audio en espera (hasta 15 s); la audiencia no tuvo que recargar nada, y el panel lo mostró como
  "atasco · audio perdido" con los segundos acumulados. La pérdida quedó acotada a lo que dice la tabla.
- **Con las mismas condiciones, a los 14 minutos, no volvió a pasar.** Toma doble de las 05:30 tras los
  arreglos del worker (nada bloqueante en el loop de envío): 0 reaperturas en las dos salas
  (`doble-en-053010`, `doble-es-053010`: 18 y 20 textos, primer texto +21,1 s y +9,7 s). La corrida larga de
  2 × 580 s tampoco tuvo ninguno.

Lo mismo lo midió un tercero, de forma independiente, y lo publicó en el foro oficial de desarrolladores de
Gemini ([hilo](https://discuss.ai.google.dev/t/gemini-3-5-live-translate-preview-in-production-paid-tier-measured-concurrency-dashboard-409s-vs-real-409s-silent-stalls-and-session-birth-degradation-data-6-questions/180489)):
congelamientos de 8 a 51 s con el socket abierto y sin error, en proyectos gratuitos y pagos, y sesiones creadas a
~1 s de distancia que tardaron 10–12 s en la primera respuesta contra 4–5 s cuando se espaciaron 12 s o más. Es
el mismo patrón que nuestra toma doble arrancada en el mismo segundo, y la misma mitigación: escalonar.

Cómo se opera para que pase lo menos posible (README, "Qué pasa si…" y "Cómo escalar"): arrancar las salas
escalonadas 20–30 s, una key o proyecto por sala (`--key`), y dejar activo el watchdog, que ya lo está.

## 3. Reproducir y mirar

```bash
# Resumen de cualquier casete: audio, textos, primer texto, reaperturas
.venv/Scripts/python docs/resumen_casetes.py fixtures/casetes/evidencia-25-09/*.jsonl

# ¿El worker envió el audio a tiempo? (atraso máximo entre ventanas)
.venv/Scripts/python -m worker.cadencia fixtures/casetes/evidencia-25-09/sala-a-051619.jsonl

# Ver una toma en la web, con sus tiempos originales, sin gastar cuota
.venv/Scripts/python -m worker.replay fixtures/casetes/evidencia-25-09/video-vivo-es-051144.jsonl --hub ws://localhost:8100/ingest --sesion demo-es
```

Los casetes de `fixtures/casetes/evidencia-25-09/` son las grabaciones crudas de las tomas del 25/09 (tres
capas: lo que se envió, lo que devolvió el servicio, lo que se emitió a la audiencia). Formato en
`worker/README.md`.
