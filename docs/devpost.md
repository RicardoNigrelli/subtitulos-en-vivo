# Texto para Devpost (listo para pegar) — bilingüe (dos jurados no hablan español)

Fuente de cada cifra usada: `ESTADO.md` (fila citada entre paréntesis) o un reporte propio del
24/09/2026, nunca un número inventado (skill `anti-alucinacion`). Evita las frases que el skill
`entrega` marca como prohibidas (costo presentado como medido, una cifra de error de transcripción
como si fuera absoluta, generalizar cada cuántos segundos aparece un subtítulo, o cualquier número
sin comando). Verificación de esto último: `reportes/demo-b4.md` §2 (`grep` sobre este archivo).

Reemplazar antes de enviar: `https://github.com/RicardoNigrelli/subtitulos-en-vivo` (repo público, lo crea ops/Ricardo, B6-B7) y
`<YOUTUBE_URL>` (video subido por Ricardo, B6/B10).

---

## Title / Título

**EN:** Nerdearla Live Captions
**ES:** Subtítulos en vivo para Nerdearla

## Tagline (≤ 60 caracteres)

**EN (54 chars):** Live transcription & EN-ES translation for conferences
**ES (59 caracteres):** Transcripción y traducción en vivo para charlas, con Gemini

## Links

- Repo: `https://github.com/RicardoNigrelli/subtitulos-en-vivo`
- Video: `<YOUTUBE_URL>`
- How to try it / connect it to your services: [`docs/integracion.md`](integracion.md)

## Built With

`python` · `aiohttp` · `websockets` · `google-genai` (Gemini Live API) ·
`gemini-3.5-transcribe-live` · `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` · `jsonschema` ·
`pytest` · `ffmpeg` · `yt-dlp` · `html` · `css` · `javascript` (vanilla, sin framework) · `docker` ·
`qrcode-generator` (MIT, vendorizado en `web/vendor/` para los QR del índice, sin llamada externa)

(Lista cruzada contra `requirements.txt` y los modelos citados en `ESTADO.md`, fila "`.env` con
`GEMINI_API_KEY`..." y fila "Modelo y config exactos" de `reportes/audio-pipeline-b1.md`.)

---

## Description — English (primary; paste this for the two non-Spanish-speaking judges)

### Inspiration

Nerdearla runs several talks in parallel, in Spanish and English, for an audience that doesn't all
share one language. The Vibeathon challenge asks for exactly the accessibility layer a real
multi-track, multi-language conference needs: live captions, live translation, and a way for each
attendee to pick their own room and language. Built solo (Ricardo), remote, during the 24-hour
window, using a small team of specialized coding agents (see `PROMPTS.md` in the repo root for the
full breakdown of who built what).

### What it does

- Takes in live audio from a file, a stream, or a microphone (at least one real source is required
  and works today).
- Transcribes it in real time, in the original language (Spanish or English).
- Translates it in real time from English to Spanish (and, offline-verified, Spanish to English).
- Shows the captions on a web page where every viewer independently picks the session (room) and
  the language they want to read; the view renders ONLY the chosen language (an animated "…"
  placeholder holds the line's place while its translation is in flight, `reportes/frontend-final.md`).
- Runs at least two sessions at the same time, each with its own real speech-to-text pipeline —
  measured with **5 real rooms at once across 2 Google Cloud projects** on 09/25
  (`docs/evidencia.md`, section 1b), and a standalone supervisor (`ops/salas.py`) that staggers
  startup, restarts a crashed room on its own, and was load-tested with **10 rooms with no API
  calls** while measuring real RSS/CPU (`reportes/ops-salas.md`).
- Ships a monitoring panel (latency percentiles, connection state, automatic session-rotation
  counter, and a plain-language "what needs attention first" view sorted by urgency, with a settings drawer, spoken alerts with volume control and a Spanish/English
  switch, `panel/README.md`)
  and a SRT/VTT/TXT exporter for any session's saved history.
- Requires a token for anything other than localhost (hub, panel and worker→hub traffic), caps
  message size/connections/known sessions, and sets basic security headers; stream credentials are
  never published (`reportes/backend-seguridad.md`).

### How we built it

- Contract-first: a single frozen JSON message schema (`contracts/`), agreed on in the first 15
  minutes and only ever extended (fields added, never renamed or removed), so every other piece
  (ingestion, hub, web view, monitoring panel, exporter) could be built in parallel against the same
  wire format and validated automatically (`contracts.validate`, JSON Schema 2020-12).
  All 12+ recorded fixtures still validate against it. The schema was only ever extended (blocks 2
  and 4, additive fields); no field was renamed or removed.
  All commits and their timestamps are public in the repo's own git history.
- A Python worker turns raw audio into fixed-size, gap-aligned windows and streams them to Gemini
  Live (`gemini-3.5-transcribe-live`) for transcription, with a self-healing "reopen with overlap"
  mechanism: any Gemini Live session eventually needs to be replaced (idle timeout, provider-side
  close, or a scheduled refresh) — the worker opens the next one early, overlaps it with the old
  one, and stitches sequence numbers so viewers never see a gap. We frame this as **transparent
  Live-session rotation**, because from the viewer's side that's exactly what it is: continuity, not
  a visible failure.
- Translation runs as a separate, batched pipeline (Gemini Flash-Lite models, rotated across two
  models to spread rate limits) so it never blocks transcription; a WebSocket hub does fan-out per
  session and language, merges translations into the saved history idempotently, and was load
  tested with 200 and 500 concurrent synthetic viewers on a single session without touching the
  transcription/translation API at all — because fan-out is a hub problem, not a Gemini quota
  problem.
- Every "Gemini said X" claim in our own internal reports is backed by a recorded raw JSONL
  transcript of the actual server messages (a "casette"), which also lets the team replay real
  sessions for development and demos without spending API quota.
- Development happened in short, timed blocks, each closed by an independent "adversarial" agent
  that re-runs a sample of the block's claims before they're accepted — the same discipline this
  document itself follows.

### Challenges we ran into

- Gemini Live's real behavior under sustained load didn't match what short demos suggested: instead
  of respecting our own end-of-turn boundaries, it started fusing consecutive turns together, and
  none of our long test sessions ever reached the documented ~10-minute idle disconnect — they were
  closed earlier by the server instead (observed closes at roughly 77 s, 119 s, 450 s and 536 s of
  sent audio, around 283 s of accumulated audio in the fused turn). That single finding is why the
  rotation mechanism above exists at all.
- The free tier's text-model limits (15 requests/minute and 500/day per model) meant translation
  had to be batched and load-balanced across two models from day one, or it would simply stop
  working under two concurrent sessions.
- Live translation latency is bound by the free tier's text-model call quota, not by the model
  itself: on 09/25 we ran an A/B/C/D test on the same room and found that shrinking the audio window
  from 3 s to 2 s dropped original-language latency (p50 3.06 s → 2.32 s) but, on the free tier,
  generated more lines than the quota could translate immediately, so translated coverage got
  *worse*, not better (`docs/evidencia.md`, section 1c). That's an honest, counter-intuitive result:
  the default shipped is the one that measured best on our own tier (3 s window, immediate
  translation when there's headroom, batching when there isn't). On a paid tier, where that call cap
  goes away, the same 2 s window is estimated (not measured) at 3.5–4.7 s end to end for the
  translated line, versus ~6 s today on the free tier.
- A handful of very Windows-specific traps: a taken port doesn't fail to bind, it silently steals
  traffic from the process that's already listening; a local antivirus intercepts HTTPS and trips
  Python 3.13's stricter certificate checks; Git Bash rewrites paths that start with `/`.

### Accomplishments that we're proud of

- Two independent, fully real (non-replay) speech-to-text sessions running at the same time end to
  end, feeding the same shared hub, with continuous sequence numbers and no gaps in a live run —
  and, on 09/25, **5 real rooms at once across 2 Google Cloud projects**, none failing on quota
  (`docs/evidencia.md`, section 1b), plus a supervisor script tested with 10 rooms with no API calls.
- An adversarial security pass (17 findings) and a UX pass (23 findings) the morning of the
  deadline, most fixed same-day: a required token outside localhost, message-size/connection/session
  caps, security headers, and a caption-view redesign that stopped mixing the original language into
  a translated view.
- A hub that fanned out one session's captions to 200 and to 500 simultaneous synthetic viewers, in
  order, without ever calling Gemini for it.
- A translation-merge path (batched items applied by sequence number to already-saved lines) that we
  verified is idempotent and order-independent by replaying the same real, recorded sessions through
  it more than once.
- Treating "Gemini said X" as evidence only when it's a saved raw transcript, and having a
  dedicated role whose only job is to re-run other people's claims before they count as done.

### What we learned

- A live, multi-hour, real-time ASR provider behaves differently than a 30-second demo call: plan
  for turn-fusion, early closes, and reconnection from the very first design, not as an
  afterthought.
- Freezing a message contract on day one — and only ever adding fields to it — is what let audio
  capture, the hub, the web view, and the monitoring panel get built the same afternoon by separate
  agents without merge conflicts in behavior, only in code.
- Recorded replay ("casettes") is what makes aggressive API-quota discipline compatible with fast,
  parallel iteration: almost all of the watchdog, reconnection, and rotation logic was built and
  tested against a handful of real recordings, not against the live API.

### What's next

- Move the default deployment target from the free tier to a paid Gemini/Vertex tier: our own
  measurements show the free tier's per-minute call cap, not the model, is what limits how fast
  translation can keep up (`docs/evidencia.md`, section 1c); `docker compose up` with real
  credentials is already a one-command deploy, no code changes needed for that switch.
- Promote Spanish-to-English translation (already verified offline) to a first-class, always-on
  second target language, and add more target languages.
- A per-event glossary of proper nouns (speaker names, product names) fed into the transcription
  model as vocabulary hints — the current sessions already do this manually per talk.
- Document and, where useful, automate the three scaling levers we identified: more concurrent
  Gemini Live sessions (paid tier / multiple projects), more viewers per session (already
  demonstrated on the hub alone), and a no-API-key "replay mode" that can show a full multi-room
  deployment for a demo or a sales conversation.

---

## Descripción — Español (referencia; los dos jurados que no hablan español leen la versión en inglés de arriba)

### Inspiración

Nerdearla tiene varias charlas en paralelo, en español e inglés, para un público que no comparte un
único idioma. El desafío de la Vibeathon pide exactamente la capa de accesibilidad que necesita una
conferencia real con varias salas e idiomas: subtítulos en vivo, traducción en vivo, y que cada
persona elija su sala y su idioma. Construido en solitario (Ricardo), en modalidad remota, dentro de
la ventana de 24 horas, con un equipo chico de agentes de código especializados (el detalle completo
de quién construyó qué está en `PROMPTS.md`, en la raíz del repo).

### Qué hace

- Recibe audio en vivo desde un archivo, un stream o un micrófono (al menos una fuente real
  funciona hoy).
- Lo transcribe en tiempo real, en el idioma original (español o inglés).
- Lo traduce en tiempo real de inglés a español (y, verificado offline, de español a inglés).
- Muestra los subtítulos en una página web donde cada persona elige, de forma independiente, la
  sesión (sala) y el idioma que quiere leer; la vista renderiza SÓLO el idioma elegido (mientras la
  traducción de una línea está en camino se ve un placeholder animado "…" que le guarda el lugar,
  `reportes/frontend-final.md`).
- Procesa al menos dos sesiones al mismo tiempo, cada una con su propio pipeline real de
  reconocimiento de voz — medido con **5 salas reales al mismo tiempo, en 2 proyectos de Google
  Cloud** el 25/09 (`docs/evidencia.md`, sección 1b), y un supervisor propio (`ops/salas.py`) que
  escalona el arranque, reinicia solo una sala caída, y se probó con **10 salas sin gastar cuota de
  API** midiendo RSS/CPU reales (`reportes/ops-salas.md`).
- Incluye un panel de monitoreo (percentiles de latencia, estado de conexión, contador de
  reaperturas automáticas de sesión, y una vista en lenguaje llano ordenada por urgencia para saber
  qué atender primero, con configuración, avisos por voz con volumen y cambio español/inglés,
  `panel/README.md`) y un exportador a SRT/VTT/TXT del historial guardado de
  cualquier sesión.
- Exige un token fuera de `localhost` (hub, panel y el tráfico worker→hub), pone topes de tamaño de
  mensaje/conexiones/sesiones conocidas y cabeceras de seguridad básicas; las credenciales del stream
  nunca se publican (`reportes/backend-seguridad.md`).

### Cómo lo construimos

- Contrato primero: un único esquema JSON de mensajes, congelado (`contracts/`), acordado en los
  primeros 15 minutos y sólo extendido después (se agregan campos, nunca se renombran ni se quitan),
  así el resto de las piezas (ingesta, hub, vista web, panel, exportador) se construyeron en paralelo
  contra el mismo formato y se validan automáticamente (`contracts.validate`, JSON Schema 2020-12).
  Los más de 12 casetes grabados siguen validando contra el esquema. El esquema sólo se amplió
  (bloques 2 y 4, campos aditivos); ningún campo se renombró ni se quitó.
  Todos los commits y sus horarios son públicos en el propio historial de git del repo.
- Un worker en Python convierte el audio crudo en ventanas de tamaño fijo alineadas por silencios y
  las manda a Gemini Live (`gemini-3.5-transcribe-live`) para transcribir, con un mecanismo propio de
  "reabrir con solape": toda sesión de Gemini Live eventualmente hay que reemplazarla (por
  inactividad, por un cierre del lado del proveedor, o por un refresco preventivo programado); el
  worker abre la siguiente ANTES, la solapa con la vieja, y empalma los números de secuencia para que
  quien mira nunca vea un corte. Lo presentamos como **rotación transparente de sesiones Live**,
  porque desde el lado de quien mira es exactamente eso: continuidad, no una falla visible.
- La traducción corre como un pipeline separado y por lotes (modelos Gemini Flash-Lite, rotando
  entre dos para repartir los límites de uso) para no bloquear nunca la transcripción; un hub por
  WebSocket reparte por sesión e idioma, mergea las traducciones en el historial guardado de forma
  idempotente, y se probó con 200 y 500 personas sintéticas mirando la misma sesión a la vez sin
  tocar para nada la API de transcripción/traducción — repartir a muchos espectadores es un problema
  del hub, no de la cuota de Gemini.
- Cada afirmación interna del tipo "Gemini dijo X" está respaldada por una grabación cruda en JSONL
  de los mensajes reales del servidor (un "casete"), que además permite reproducir sesiones reales
  para desarrollar y hacer demos sin gastar cuota de API.
- El desarrollo se organizó en bloques cortos y cronometrados, cada uno cerrado por un agente
  "adversario" independiente que vuelve a correr una muestra de las afirmaciones del bloque antes de
  darlas por buenas — la misma disciplina que sigue este propio documento.

### Dificultades que encontramos

- El comportamiento real de Gemini Live bajo carga sostenida no fue el que sugerían las pruebas
  cortas: en vez de respetar nuestros propios límites de turno, empezó a fusionar turnos
  consecutivos, y ninguna de nuestras sesiones largas de prueba llegó al desconexión por inactividad
  documentada de ~10 minutos: el servidor las cerró antes (cierres observados a unos 77 s, 119 s,
  450 s y 536 s de audio enviado, con el turno fusionado acumulando unos 283 s de audio). Ese
  hallazgo, solo, es la razón de ser del mecanismo de rotación de arriba.
- Los límites del nivel gratuito para los modelos de texto (15 solicitudes por minuto y 500 por día,
  por modelo) obligaron a que la traducción fuera por lotes y repartida entre dos modelos desde el
  primer día, o directamente dejaba de funcionar con sólo dos sesiones simultáneas.
- La latencia de la traducción en vivo la fija el cupo de llamadas del modelo de texto del nivel
  gratuito, no el modelo en sí: el 25/09 corrimos una prueba A/B/C/D sobre la misma sala y encontramos
  que bajar la ventana de audio de 3 s a 2 s mejoró la latencia del original (p50 3,06 s → 2,32 s)
  pero, en nivel gratuito, generó más líneas de las que el cupo podía traducir de inmediato, así que
  la cobertura traducida empeoró en vez de mejorar (`docs/evidencia.md`, sección 1c). Es un resultado
  honesto y contraintuitivo: el default que quedó es el que mejor midió en nuestro propio nivel
  (ventana de 3 s, traducción inmediata cuando hay margen, lotes cuando no). En nivel pago, donde ese
  tope de llamadas desaparece, la misma ventana de 2 s se estima (no se midió) en 3,5–4,7 s de punta a
  punta para la línea traducida, contra ~6 s de hoy en nivel gratuito.
- Un puñado de trampas bien de Windows: un puerto ocupado no falla al bindear, directamente le roba
  el tráfico al proceso que ya estaba escuchando; un antivirus local intercepta HTTPS y activa las
  validaciones de certificado más estrictas de Python 3.13; Git Bash reescribe las rutas que
  empiezan con `/`.

### Logros de los que estamos orgullosos

- Dos sesiones de reconocimiento de voz real (no replay) funcionando al mismo tiempo, de punta a
  punta, alimentando el mismo hub compartido, con números de secuencia continuos y sin cortes en una
  corrida en vivo — y, el 25/09, **5 salas reales al mismo tiempo en 2 proyectos de Google Cloud**,
  ninguna falló por cupo (`docs/evidencia.md`, sección 1b), más un supervisor propio probado con 10
  salas sin gastar cuota de API.
- Una revisión adversarial de seguridad (17 hallazgos) y de UX (23 hallazgos) la mañana de la
  entrega, la mayoría arreglados el mismo día: token obligatorio fuera de localhost, topes de
  tamaño de mensaje/conexiones/sesiones, cabeceras de seguridad, y un rediseño de la vista de
  subtítulos que dejó de mezclar el idioma original dentro de una vista traducida.
- Un hub que repartió los subtítulos de una sesión a 200 y a 500 espectadores sintéticos
  simultáneos, en orden, sin llamar a Gemini para eso ni una vez.
- Un camino de merge de traducciones (ítems por lotes aplicados por número de secuencia a líneas ya
  guardadas) que verificamos idempotente e independiente del orden, reproduciendo más de una vez las
  mismas sesiones reales grabadas.
- Tratar "Gemini dijo X" como evidencia sólo cuando es una transcripción cruda guardada, y tener un
  rol dedicado cuyo único trabajo es volver a correr las afirmaciones de los demás antes de darlas
  por terminadas.

### Qué aprendimos

- Un proveedor de reconocimiento de voz en vivo, real, de varias horas, se comporta distinto que una
  llamada de demo de 30 segundos: hay que diseñar desde el principio para fusión de turnos, cierres
  tempranos y reconexión, no agregarlo después.
- Congelar un contrato de mensajes desde el primer día —y sólo agregarle campos— es lo que permitió
  que la captura de audio, el hub, la vista web y el panel de monitoreo se construyeran la misma
  tarde por agentes separados sin conflictos de comportamiento, sólo de código.
- El replay grabado ("casetes") es lo que hace compatible una disciplina agresiva de cuota de API con
  iterar rápido en paralelo: casi toda la lógica de watchdog, reconexión y rotación se construyó y
  probó contra un puñado de grabaciones reales, no contra la API en vivo.

### Qué sigue

- Mover el despliegue por defecto del nivel gratuito a un nivel pago de Gemini/Vertex: lo que
  medimos muestra que el tope de llamadas por minuto del nivel gratuito, no el modelo, es lo que
  limita qué tan rápido llega la traducción (`docs/evidencia.md`, sección 1c); `docker compose up`
  con credenciales reales ya es un despliegue de un solo comando, sin cambios de código para ese
  cambio de nivel.
- Promover la traducción de español a inglés (ya verificada offline) a segundo idioma de destino de
  primera clase y agregar más idiomas.
- Un glosario de nombres propios por evento (oradores, productos) que alimente al modelo de
  transcripción como pista de vocabulario — las sesiones actuales ya lo hacen a mano por charla.
- Documentar y, donde sirva, automatizar los tres ejes de escalabilidad que identificamos: más
  sesiones simultáneas contra Gemini (nivel pago / varios proyectos), más espectadores por sesión (ya
  demostrado a nivel del hub solo), y un "modo replay" sin API key que puede mostrar un despliegue
  completo de varias salas para una demo o una conversación comercial.

---

## How it fits in a room / Cómo encaja en una sala

**EN:** Matches what Nerdearla staff described for the actual venue this week: a sound card feeds a
3.5 mm cable into a small PC by the stage, which either opens the browser locally or forwards the
audio over the network. Two ways to get audio in: on that PC, `worker.run --fuente mic --dispositivo
"<dshow name>"` (**verified end to end on 09/25** with a headset microphone: Spanish speech transcribed and
translated to English, `fixtures/casetes/evidencia-25-09/prueba-mic-20260925-090921.jsonl`), or send the audio over the network to the server running the worker with `--fuente
url` (**verified with a real 30 s UDP run on 09/24**, `reportes/audio-pipeline-b8.md`), e.g. `ffmpeg
-f dshow -i audio="<device>" -ac 1 -ar 16000 -f mpegts udp://SERVER:9000` from the mini PC. Screens in
front of the stage open `http://SERVER:8080/s/<room>?lang=es&modo=proyeccion` (verified in a browser
on 09/24); phones scan a per-session QR code from the index, with room and language living in the
URL — no app, no login. For the stream, an OBS browser source or a vMix Web Browser input pointed at
`.../s/<room>?lang=en&modo=obs` renders a transparent background with two lines at the bottom —
**verified over real video in OBS on 09/25** (the browser source shows the speaker's video through it
with the caption lines on top, `reportes/obs-transparencia.md`, screenshot
`reportes/obs-transparencia.png`). Capture (`worker`) and
screens (`web`) are separate processes: the worker reopens its own Gemini session on a server-side
close, a stall, or every 240 s of sent audio (`rotation` in the panel, with accumulated
`audio_lost_s`), and the viewer's page reconnects on its own and recovers what it missed from history
(69/69 messages recovered in the reconnection test, `qa/out/reconexion-b2.log`) — that a screen reload
doesn't interrupt transcription follows from that split-process design, it is not a measured figure.
If translation falls behind, an animated "…" placeholder holds the line's place; after 120 s without
a translation it falls back to showing the original text with a language tag (e.g. "EN") instead
(`reportes/frontend-final.md`). Outside `localhost`, the hub, the panel and the worker→hub traffic
all require a token (`reportes/backend-seguridad.md`) — no stream credentials are ever published.

**ES:** Coincide con lo que describió el staff de Nerdearla para la sala real de esta semana: una
placa de audio manda un cable de 3,5 mm a una mini PC junto al escenario, que abre el navegador ahí
mismo o reenvía el audio por red. Dos formas de meter el audio: en esa PC, `worker.run --fuente mic
--dispositivo "<nombre dshow>"` (**verificado de punta a punta el 25/09** con el micrófono de un
auricular: voz en castellano transcripta y traducida al inglés, `fixtures/casetes/evidencia-25-09/prueba-mic-20260925-090921.jsonl`), o mandar el audio por red al servidor que
corre el worker con `--fuente url` (**verificado con una corrida real de 30 s por UDP el 24/09**,
`reportes/audio-pipeline-b8.md`), por ejemplo `ffmpeg -f dshow -i audio="<dispositivo>" -ac 1 -ar
16000 -f mpegts udp://SERVIDOR:9000` desde la mini PC. Las pantallas frente al escenario abren
`http://SERVIDOR:8080/s/<sala>?lang=es&modo=proyeccion` (verificado en navegador el 24/09); los
celulares escanean el QR de cada sesión desde el índice, con la sala y el idioma en la URL, sin
instalar nada ni loguearse. Para el stream, una fuente de navegador de OBS o una entrada Web Browser
de vMix apuntando a `.../s/<sala>?lang=en&modo=obs` da fondo transparente con dos líneas abajo —
**verificado sobre video real en OBS el 25/09** (la fuente de navegador deja ver el video del orador
con las líneas de subtítulo encima, `reportes/obs-transparencia.md`, captura
`reportes/obs-transparencia.png`). La captura (`worker`) y las
pantallas (`web`) son procesos separados: el worker reabre sola la sesión con Gemini ante un cierre
del servidor, un atasco o cada 240 s de audio enviado (`rotation` en el panel, con `audio_lost_s`
acumulado), y la vista se reconecta sola y recupera por historial lo perdido (69/69 mensajes
recuperados en la prueba de reconexión, `qa/out/reconexion-b2.log`) — que recargar la pantalla no
corte la transcripción es consecuencia de ese diseño con procesos separados, no una cifra medida. Si
la traducción se atrasa, se ve un placeholder animado "…" que le guarda el lugar a la línea; a los
120 s sin traducción cae al original con una marca de idioma (por ejemplo "EN") en vez de mezclarse
con el idioma elegido (`reportes/frontend-final.md`). Fuera de `localhost`, el hub, el panel y el
tráfico worker→hub exigen un token (`reportes/backend-seguridad.md`) — las credenciales del stream
nunca se publican.

---

## What happens when… / Qué pasa si…

**EN:** Questions the Nerdearla staff raised about live-talk deal breakers (same Discord thread).
Translation lag doesn't stop transcription: an animated "…" placeholder holds the line's place and,
after 120 s without a translation, falls back to the original text with a language tag instead of
mixing languages in the chosen-language view (`reportes/frontend-final.md`); the panel counts failed
translations. If Gemini closes the session or stalls, the worker reopens it with overlap on its own —
close / stall / a 240 s preventive refresh / a stuck send — with no F5 and no remote desktop
(`qa/out/smoke-b5r.log`: 5 rotations, 0 silent gaps over a real 2×11 min run; the same stall pattern,
more frequent with two rooms sharing one project key, is documented with its mitigation — staggered
startup, one key per room — in `docs/evidencia.md`, section 2, and matches an independent report on
Google's own developer forum). If the hub restarts or the network between worker and hub drops, the
worker reconnects and resends what's pending, and the viewer's page recovers the missed lines from
history (`qa/out/reconexion-b2.log`: 69/69 recovered). A screen reload doesn't interrupt capture, by
design (worker and web are separate processes) — not a measured figure. A few lost seconds of audio
inside a rotation show up as `audio_lost_s` on the panel and as "[gap: N s]" in the transcript
(`reportes/frontend-b4-huecos.txt`). If the quota runs out or the key is missing, the worker stops
with a clear message instead of failing silently, and the same deployment can start in a labeled
replay mode for tests (`docs/costos.md` for the paid-tier / multiple-projects path). For a vMix-style
stream with no built-in translation, one transparent browser source per language (`?modo=obs&lang=xx`)
covers it, verified against real video in OBS on 09/25 (`reportes/obs-transparencia.md`). Numbers for
scale, about our own runs only: perceived transcription latency was p50 ≈ 0.44 s in a real 2-session
run (`qa/out/final/`, `04-latencia.log`), and on 09/25, original-language latency measured p50 ≈ 2.3–3.1
s depending on window size, with a paid-tier estimate (not measured) of 3.5–4.7 s end to end including
translation (`docs/evidencia.md`, section 1c) — we make no claim about any other team's setup.

**ES:** Preguntas que hizo el staff de Nerdearla sobre qué sería un "deal breaker" en una charla en
vivo (mismo hilo de Discord). El atraso en la traducción no frena la transcripción: un placeholder
animado "…" le guarda el lugar a la línea y, a los 120 s sin traducción, cae al original con una marca
de idioma en vez de mezclar idiomas en la vista de un solo idioma elegido (`reportes/frontend-final.md`);
el panel cuenta las traducciones fallidas. Si Gemini cierra la sesión o se atasca, el worker la
reabre solo, con solape — cierre / atasco / preventiva a los 240 s / un envío trabado — sin F5 ni
escritorio remoto (`qa/out/smoke-b5r.log`: 5 rotaciones, 0 tramos mudos en una corrida real de 2×11
min; el mismo patrón de atasco, más frecuente con dos salas que comparten la key de un proyecto, está
documentado con su mitigación —arranque escalonado, una key por sala— en `docs/evidencia.md`, sección
2, y coincide con un reporte independiente en el foro oficial de desarrolladores de Google). Si el hub
se reinicia o se cae la red entre el worker y el hub, el worker reconecta y reenvía lo
pendiente, y la vista recupera por historial lo que se perdió (`qa/out/reconexion-b2.log`: 69/69
recuperados). Recargar la pantalla no corta la captura, por diseño (worker y web son procesos
separados) — no es una cifra medida. Si se pierden unos segundos de audio en una rotación, el panel
lo muestra como `audio_lost_s` y la vista marca "[tramo sin texto: N s]"
(`reportes/frontend-b4-huecos.txt`). Si se acaba la cuota o falta la key, el worker corta con un
mensaje claro en vez de fallar en silencio, y el mismo despliegue puede arrancar en modo replay
rotulado para pruebas (`docs/costos.md` para el camino de nivel pago / varios proyectos). Para un
stream tipo vMix sin traducción propia, una fuente de navegador transparente por idioma
(`?modo=obs&lang=xx`) alcanza, verificado sobre video real en OBS el 25/09
(`reportes/obs-transparencia.md`). Cifras de referencia, sólo sobre nuestras propias corridas: la
latencia percibida de transcripción fue p50 ≈ 0,44 s en una corrida real de 2 sesiones
(`qa/out/final/`, `04-latencia.log`), y el 25/09 la latencia del idioma original midió p50 ≈ 2,3–3,1 s
según el tamaño de ventana, con una estimación (no medida) de 3,5–4,7 s de punta a punta incluyendo la
traducción en nivel pago (`docs/evidencia.md`, sección 1c) — sin afirmar nada sobre el equipo de nadie
más.

---

## Antes de enviar / Before submitting

- [ ] Reemplazar `<YOUTUBE_URL>` (dos veces: `## Links` arriba y en Devpost) por el link real del
  video subido a YouTube.
- [ ] Probar ese link en una ventana privada/incógnito (sin sesión de Google logueada) y confirmar
  que reproduce y tiene subtítulos en inglés disponibles (R14).
- [ ] Confirmar que el repositorio de `## Links` es público y que la licencia (Apache 2.0, R15) se ve
  en la página de GitHub, no sólo en el archivo `LICENSE`.
- [ ] Confirmar que `README.md` enlaza `PROMPTS.md` y que ambos están en la raíz del repo público.
- [ ] Recargar la página del proyecto en Devpost después de enviar y confirmar que figura como
  "submitted" (R22, antes de las 12:00 AR / 15:00 UTC del 25/09).
