# PROMPTS.md — cómo se construyó este proyecto con agentes

Este documento existe para responder, antes de que la organización lo pregunte, a **X2** de las
reglas de Devpost: *"El organizador puede pedirte ejecutar el proyecto y demostrar tu rol en
construirlo. No responder puede descalificar."* (`REGLAS.md`). Es honesto a propósito: dice qué se
investigó antes de la Vibeathon (permitido por R12), qué se escribió durante la Vibeathon (obligado
por R11a/R11b), y cómo se comprueba la diferencia con el propio `git log` del repositorio.

## La regla que separa "investigar antes" de "programar antes" (R11a, R11b, R12)

> R11a: "El proyecto debe ser construido durante el período en el que transcurre la Vibeathon (24 y
> 25 de septiembre, 2026)." R11b: "Los proyectos que hayan sido comenzados previo a estas fechas no
> serán tenidos en cuenta." R12: "Podés usar librerías, modelos y servicios existentes... pero la
> solución en sí tiene que ser tuya." (`REGLAS.md`)

Antes del 24/09, Ricardo hizo pruebas exploratorias contra la cuenta real de Gemini (llamadas
sueltas, sin repositorio ni commits) para no llegar a ciegas a las 24 horas. Esas pruebas se
convirtieron en **cuatro documentos de referencia** ("skills") que los agentes cargan al empezar,
citados tal cual son, sin inventar nada nuevo acá:

- **`gemini-live`**: comportamiento medido de la Gemini Live API — modelo `gemini-3.5-transcribe-live`,
  formato de audio (PCM s16le 16 kHz mono), el hallazgo de que los turnos manuales con un gap de
  0,7 s bajan la latencia a ~0,4 s, que sin `NO_INTERRUPTION` se pierde ~80 % del contenido, que no
  hay parciales reales, que el free tier midió 7 sesiones concurrentes, un bug reconocido de sesiones
  que se quedan mudas, y que la traducción necesita dos pasos (transcribir y después traducir el
  texto con un modelo aparte).
- **`ui-subtitulos`**: un benchmark propio de 8 productos comerciales y 7 open source de subtítulos
  en vivo, con las decisiones que salieron de ahí (append puro, 3 líneas + 1 parcial, sesión e idioma
  en la URL, chip de conexión de 4 estados, p50/p95 nunca promedio, latencia tolerable 1–2 s).
- **`ingesta`**: cómo bajar audio de YouTube con `yt-dlp` en Windows sin perder la pista de idioma
  "original", y un problema puntual de certificados (Avast + Python 3.13).
- **`cuota-gemini`**: que la cuota de Gemini es finita y no se conoce el número exacto, que un solo
  agente debía tocar la API real y los demás trabajar contra grabaciones, y un presupuesto en
  minutos por bloque.

**Nada de código nació de esas pruebas.** El primer commit del repositorio es de HOY, después de las
12:00 AR, y se verifica así (re-corrido por mí, `demo`, además de estar en `ESTADO.md`):

```
git log --format='%h %aI %an %s'
bf3f6e0 2026-09-24T12:32:06-03:00 RicardoNigrelli Arranque: licencia, reglas, CLAUDE.md, ...
0ba3e51 2026-09-24T13:56:52-03:00 RicardoNigrelli Bloque 1: contrato congelado, hub WebSocket, ...
86d4f85 2026-09-24T14:42:34-03:00 RicardoNigrelli Bloque 2: traductor por lotes, parciales, ...
23dbafe 2026-09-24T15:36:11-03:00 RicardoNigrelli Bloque 3: reabrir con solape, smoke real, ...
```

Los cuatro commits son de hoy, después de las 12:00 AR (R11a), autor y committer `RicardoNigrelli`
(único autor, ver más abajo), sin ningún mensaje anterior a esa hora. Además, ninguno lleva mención a
IA: `git log -1 --format=%B <hash> | grep -icE '^co-authored-by|generated with|anthropic|claude code'`
da `0` para los cuatro hashes (corrido por mí, 24/09 16:00 AR aprox.; la única vez que aparece la
palabra "claude" en el historial es como parte del nombre de archivo `CLAUDE.md`, no como atribución).
El **código en sí** —el worker, el hub, la vista web, el panel, los tests— se escribió hoy, con los
agentes de abajo, contra esos cuatro documentos de referencia y contra lo que la API realmente
devolvió (cada hallazgo de los skills se re-verifica contra un casete real antes de darse por
bueno; ver "Cómo se verificó").

## Qué se construyó, bloque por bloque

Plan aprobado por Ricardo el 24/09 a las 12:55 AR, en `reportes/plan.md` (archivo de trabajo interno,
**no está en el repositorio**: lo ignora `.gitignore` a propósito, junto con `ESTADO.md` y
`reportes/`, porque son bitácora operativa, no parte de la solución — R12). Se cita su contenido acá
en vez de enlazarlo:

- **Bloque 1 (13:00–15:00):** contrato de mensajes congelado (`contracts/`), hub WebSocket con
  fan-out (`hub/`), worker con ASR real contra Gemini Live (`worker/`), primeros casetes grabados,
  vista web mínima (`web/`), base de `ops/` (requirements, `.env.example`, Dockerfile). Cierra con
  commit `0ba3e51`.
- **Bloque 2 (15:00–15:45 aprox.):** traductor EN→ES por lotes con reintento, merge de traducciones
  en el hub (idempotente, por número de secuencia), subtítulos parciales, vista bilingüe en `web/`,
  primera medición de qa (latencia, cobertura). Cierra con `86d4f85`.
- **Bloque 3 (15:45–16:45 aprox.):** "reabrir con solape" en el worker (cierre, atasco, refresco
  preventivo a los 240 s, `GoAway`), panel de monitoreo (`panel/`), smoke real de dos sesiones
  simultáneas, backfill al reconectar en la vista web. Cierra con `23dbafe`.
- **Bloque 4 (24/09 15:40–16:40 AR, este bloque):** de-duplicación de costuras y traductor en
  paralelo (`audio-pipeline`), contrato y hub sirviendo la web y el panel (`backend`), chip de
  conexión y scroll (`frontend`), y — lo que escribe este documento — preparación del video, export
  SRT/VTT/TXT, y este mismo `PROMPTS.md` (`demo`).
- **Bloques 5 en adelante (planificados):** checkpoint duro del MVP con el agente adversario, README
  y `docker compose`, envío borrador a Devpost, glosario de nombres propios, subtítulos en inglés del
  propio video (R14), envío final antes de las 12:00 AR del 25/09 (R22).

## Los 8 agentes (carpeta exclusiva, modelo, qué hicieron)

Cada agente tiene su propia carpeta y **nadie escribe fuera de la suya**; los archivos compartidos
(raíz, `.env.example`, README, Dockerfile) son de `ops`. Máximo 4 agentes en paralelo por bloque.
Fuente: `.claude/agents/*.md` (briefs reales, no reconstruidos de memoria) y `CLAUDE.md`.

| Agente | Modelo | Carpeta | Qué construyó |
|---|---|---|---|
| `audio-pipeline` | Opus | `worker/`, `fixtures/` | Ingesta de audio (ffmpeg→PCM), ventanas con gap, `SessionWorker`, rotación "reabrir con solape", dedup de costuras, traducción en dos pasos. Único agente que llama a la API real de Gemini; contabiliza cada minuto gastado. |
| `backend` | Opus | `contracts/`, `hub/` | El contrato de mensajes (congelado desde el bloque 1, sólo se le agregan campos), el hub WebSocket con fan-out por sesión e idioma, merge de traducciones, backlog/reconexión. |
| `frontend` | Sonnet | `web/` | La vista de audiencia: índice de sesiones, sesión e idioma en la URL, texto en vivo con append puro, selector de idioma, chip de conexión, backfill al reconectar. |
| `monitor` | Sonnet | `panel/` | El panel de monitoreo (R8e): p50/p95 de latencia, estado por sesión, contador de reaperturas con motivo, todo contra el hub real. |
| `ops` | Sonnet | raíz, `ops/`, Dockerfile, `.env.example`, README, `docs/costos.md` | Empaquetado (Docker), variables de entorno, licencia, y la documentación de cómo desplegar y escalar. |
| `qa` | Opus | `qa/` | Mide latencia percibida y cobertura, prueba el watchdog y la rotación, corre el smoke de dos sesiones reales. No escribe código de la solución, sólo lo pone a prueba. |
| `adversario` | Opus | (ninguna; sólo lee) | Al cerrar cada bloque, vuelve a correr 3 afirmaciones elegidas por él de los reportes de ese bloque y cruza `git diff --stat` contra lo que cada agente dijo haber tocado. Si algo no reproduce, el bloque no cierra. |
| `demo` (yo) | Sonnet | `docs/`, y `PROMPTS.md` en la raíz por acuerdo explícito con `ops` | El guion del video (`docs/guion-video.md`), el exportador SRT/VTT/TXT (`docs/export/exportar.py`, R8d), el texto para Devpost (`docs/devpost.md`), y este documento. |

## Cómo se verificó (para que "confiar" no sea la respuesta)

- **Ninguna afirmación sin un comando re-ejecutable.** El formato mínimo, en todos los reportes, es
  afirmación · comando · archivo de salida · exit code · hora (skill `anti-alucinacion`). "Andaba
  bien" no es una afirmación válida en este proyecto.
- **`ESTADO.md` de tres columnas.** Es la bitácora operativa (no se commitea, está en
  `.gitignore`) y no acepta una fila sin las tres: afirmación, comando que la verifica, y la última
  vez que se corrió. Cualquier sesión nueva puede retomar el trabajo leyendo sólo ese archivo.
- **Los casetes son la única evidencia de "Gemini dijo X".** Un agente puede escribir en su reporte
  que Gemini devolvió tal texto; lo único que lo hace verificable es el JSONL crudo grabado en
  `fixtures/casetes/`, con los mensajes tal cual los mandó el servidor. Está prohibido fabricar
  mensajes de Gemini fuera del módulo de replay (que además queda rotulado como replay en pantalla,
  nunca como transcripción en vivo).
- **Un agente adversario, no sólo el orquestador.** Al cierre de cada bloque, `adversario` elige 3
  afirmaciones de los reportes del bloque y las vuelve a correr por su cuenta, más un `git diff
  --stat` contra el commit anterior para comprobar que lo que cada agente dice haber tocado coincide
  con lo que git realmente registra. Una afirmación que no reproduce invalida el bloque (ver, por
  ejemplo, `reportes/adversario-b3.md`, que encontró y dejó abierto un hallazgo real sobre el
  rotulado de sesiones de prueba, corregido este mismo bloque).
- **Yo (`demo`) también sigo esta regla en este documento**: los cuatro commits y su verificación de
  autoría de arriba los corrí de nuevo hoy, no los copié de un reporte ajeno sin chequear.

## El rol de Ricardo

Los agentes no deciden solos lo que afecta al proyecto entero; eso lo aprueba o lo decide Ricardo,
por escrito, y queda registrado en `ESTADO.md` ("Decisiones y porqué") y en `reportes/plan.md`:

- Aprobó el plan de bloques completo el 24/09 a las 12:55 AR, incluida la pausa nocturna 23:00–06:00
  (el reset de cuota de Gemini es a las 04:00, así el video final sale con cupo fresco).
- Decidió cómo se resuelven los audios de prueba (clips cortos citados con URL y minuto, más un
  importador) y que el video se graba con OBS, en dos pasadas (borrador hoy, final mañana).
- A las 14:55 AR **descartó explícitamente un traductor local de respaldo**: la solución se muestra a
  gente de Google y va a producción, así que es Gemini de punta a punta; si hace falta escalar, se
  documenta con nivel pago o varios proyectos, no con un camino alternativo fuera de Gemini. También
  decidió cómo se presenta la rotación de sesiones: como mecanismo transparente, nunca como una falla
  del proveedor.
- Cargó las credenciales (`GEMINI_API_KEY` y una segunda de reserva, `GEMINI_API_KEY_RESERVA`, para
  emergencias del video) en el `.env` local; ningún agente las vio ni las imprimió.
- Es quien graba el video con OBS, quien hace el único commit al cierre de cada bloque (los agentes
  no tocan git), y quien va a subir el video a YouTube y a enviar el proyecto en Devpost — ambas
  cosas con confirmación explícita antes de hacerlas, nunca en automático.

## Enlace desde el README

Este archivo lo agrega al `README.md` el agente `ops` en el Bloque 6 (dueño de la raíz y del
README; `demo` no escribe `README.md`). Pedido cruzado registrado en `reportes/demo-b4.md`.


## Noche del 24/09 (bloques 5 a 10): qué pasó de verdad

Escrito por el orquestador al cierre, para responder a X2 sin maquillaje.

- **Bloque 5 (checkpoint del MVP):** `qa` corrió dos sesiones reales de 11 minutos en paralelo (cobertura 1,0, `seq`
  continuo, cinco rotaciones de sesión, latencia percibida p50 ≈ 0,44 s; `qa/out/smoke-b5r.log`). Los cuatro agentes
  del bloque murieron a las 16:45 por el límite de uso de la cuenta de Claude, sin escribir sus reportes; el orquestador
  reconstruyó la evidencia desde disco (`reportes/orquestador-b5.md`) y el adversario la re-ejecutó.
- **Bloques 6 y 7:** remoto público creado a las 18:46 y push con OK de Ricardo; video borrador grabado por el
  orquestador manejando OBS por WebSocket (Ricardo lo rechazó: captura plana sin narración). Clon limpio siguiendo sólo
  el README y `docker compose` con key real dentro de Docker, los dos con exit 0.
- **Bloques 8 y 9:** fuentes por URL y micrófono, glosario automático desde la agenda, reservas de llamadas de
  traducción entre procesos, panel con token, QR en el índice, README consolidado.
- **Decisiones de Ricardo, textuales:** "Gemini de punta a punta, sin traductor local de respaldo" (14:55); "no se sube
  nada hasta que el desarrollo esté completo" (20:47); "el sistema de diseño de live-worship es de Urban, no
  parecerse; inspirarse en Figma y Behance" (21:50); "el panel es muy AI-like" (23:25); voz Iria de Cartesia, más
  rápida (22:50); guion A "beneficio primero" (23:20).
- **Rediseño de vista y panel** sobre un sistema de diseño con referencias de Figma Community y Behance
  (`docs/design/sistema.md`), implementado por `frontend` y `monitor`; el orquestador ancló las líneas abajo.
- **Seguridad:** revisión completa (`reportes/seguridad.md`); arreglados el proxy de métricas que filtraba el token
  y el override `?hub=` sin validar, Docker sin root, servidores de desarrollo en 127.0.0.1, `maxLength` en el
  contrato. **Quedaron pendientes** los topes de clientes por IP y de sesiones por productor en el hub: `backend` los
  estaba escribiendo cuando el segundo límite de uso (23:05) lo cortó; sus cambios a medias colgaban los tests y el
  orquestador los revirtió. Está documentado como limitación conocida en el README.
- **Tests:** la suite del worker pasa (76). Del hub pasan 39 en cuatro archivos; `hub/tests/test_fanout.py` (200 y
  500 clientes) colgó en este entorno esa noche y no se investigó a fondo; el fan-out fue re-ejecutado por el
  adversario en los bloques 1 y 2.
- **Video final:** guion "beneficio primero" derivado de los guiones reales de videos de Wordly, Interprefy, KUDO,
  Google, Microsoft, Zoom, Samsung y Apple (`reportes/video-referencias.md`, transcripciones bajadas con yt-dlp);
  narración en español con Cartesia (voz Iria, velocidad rápida); subtítulos en inglés generados pasando la narración
  por el propio `worker.run` y `docs/export/exportar.py` (R14); composición con `docs/video/componer.py`; tomas de la
  interfaz grabadas con OBS controlado por WebSocket. Ricardo sube a YouTube y envía en Devpost.
