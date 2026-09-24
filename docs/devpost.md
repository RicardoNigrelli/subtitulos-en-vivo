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

## Built With

`python` · `aiohttp` · `websockets` · `google-genai` (Gemini Live API) ·
`gemini-3.5-transcribe-live` · `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` · `jsonschema` ·
`pytest` · `ffmpeg` · `yt-dlp` · `html` · `css` · `javascript` (vanilla, sin framework) · `docker`

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
  the language they want to read.
- Runs at least two sessions at the same time, each with its own real speech-to-text pipeline.
- Ships a monitoring panel (latency percentiles, connection state, automatic session-rotation
  counter) and a SRT/VTT/TXT exporter for any session's saved history.

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
- Getting translation to consistently keep up in real time, live, across two simultaneous sessions
  is still a work in progress as of this write-up; the pipeline, the batching, and the replay path
  are all verified end to end, and we're actively tuning the live latency (parallel dispatch,
  shorter per-call timeouts) rather than shipping something we haven't measured.
- A handful of very Windows-specific traps: a taken port doesn't fail to bind, it silently steals
  traffic from the process that's already listening; a local antivirus intercepts HTTPS and trips
  Python 3.13's stricter certificate checks; Git Bash rewrites paths that start with `/`.

### Accomplishments that we're proud of

- Two independent, fully real (non-replay) speech-to-text sessions running at the same time end to
  end, feeding the same shared hub, with continuous sequence numbers and no gaps in a live run.
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

- Close the remaining gap between "translation pipeline is correct" and "translation keeps up live,
  every time, on two-plus sessions" — batching and dispatch changes are already in progress.
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
  sesión (sala) y el idioma que quiere leer.
- Procesa al menos dos sesiones al mismo tiempo, cada una con su propio pipeline real de
  reconocimiento de voz.
- Incluye un panel de monitoreo (percentiles de latencia, estado de conexión, contador de
  reaperturas automáticas de sesión) y un exportador a SRT/VTT/TXT del historial guardado de
  cualquier sesión.

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
- Que la traducción llegue de forma consistente y a tiempo, en vivo, con dos sesiones simultáneas,
  todavía es un trabajo en curso al momento de escribir esto: el pipeline, el armado de lotes y el
  camino de replay están verificados de punta a punta, y estamos ajustando activamente la latencia en
  vivo (despacho en paralelo, timeouts más cortos por llamada) en lugar de mostrar algo que no
  medimos.
- Un puñado de trampas bien de Windows: un puerto ocupado no falla al bindear, directamente le roba
  el tráfico al proceso que ya estaba escuchando; un antivirus local intercepta HTTPS y activa las
  validaciones de certificado más estrictas de Python 3.13; Git Bash reescribe las rutas que
  empiezan con `/`.

### Logros de los que estamos orgullosos

- Dos sesiones de reconocimiento de voz real (no replay) funcionando al mismo tiempo, de punta a
  punta, alimentando el mismo hub compartido, con números de secuencia continuos y sin cortes en una
  corrida en vivo.
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

- Cerrar la brecha que queda entre "el pipeline de traducción es correcto" y "la traducción llega a
  tiempo en vivo, siempre, con dos o más sesiones": los cambios de armado de lotes y despacho ya
  están en marcha.
- Promover la traducción de español a inglés (ya verificada offline) a segundo idioma de destino de
  primera clase y agregar más idiomas.
- Un glosario de nombres propios por evento (oradores, productos) que alimente al modelo de
  transcripción como pista de vocabulario — las sesiones actuales ya lo hacen a mano por charla.
- Documentar y, donde sirva, automatizar los tres ejes de escalabilidad que identificamos: más
  sesiones simultáneas contra Gemini (nivel pago / varios proyectos), más espectadores por sesión (ya
  demostrado a nivel del hub solo), y un "modo replay" sin API key que puede mostrar un despliegue
  completo de varias salas para una demo o una conversación comercial.
