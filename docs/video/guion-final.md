# Guion final del video — Guion A "benefit-first"

Aprobado por Ricardo tal cual (fuente: `reportes/video-referencias.md` sección 3; ritmo sección 4;
reglas de narración sección 5; qué no hacer sección 6). Datos maestros en
`docs/video/guion-final.json` (lo lee `docs/video/componer.py`). Total: **58 s** (dentro de 60–70 s
permitido; no hizo falta estirar planos 1/10). Voz: Cartesia **Iria**, `--velocidad fast` (default
de `docs/video/narrar_cartesia.py`), narración en español. Todo el guion es **en vivo** narrativamente;
las tomas de interfaz se graban sobre sesiones **REPLAY** (ver nota de rotulado más abajo) —no hay
tramos que necesiten un banner tipo "REPLAY" grande: alcanza con el chip nativo de `web/sesion.html`.

| # | Tiempo | Qué se ve | Fuente | Narración ES | Subtítulo EN | Cita |
|---|---|---|---|---|---|---|
| 1 | 0:00–0:03 | Orador real, sin narración (ambiente) | `reportes/video/orador-en-booch-295-405.mp4` (existente, start=40s, crop de franja ES quemada) | *(silencio)* | *(sin texto — audio real)* | R13a |
| 2 | 0:03–0:07 | Mockup teléfono, subtítulos ES apareciendo | `reportes/video/tomas/plano-02.mkv` (pendiente) | "Ella no entiende inglés." | *pendiente del pipeline* | R20 |
| 3 | 0:07–0:11 | Zoom al teléfono, texto ES creciendo | `plano-03.mkv` (pendiente) | "Pero sigue la charla, en su idioma." | *pendiente del pipeline* | R19, R20 |
| 4 | 0:11–0:14 | QR en pantalla grande | `plano-04.mkv` (pendiente) | "Escaneó un QR y eligió su sala." | *pendiente del pipeline* | R6 |
| 5 | 0:14–0:18 | Índice de sesiones | `plano-05.mkv` (pendiente) | "Cada sala, su charla." | *pendiente del pipeline* | R6, R21 |
| 6 | 0:18–0:22 | Split: orador real + transcripción EN en vivo | orador existente (start=66s) + `plano-06.mkv` (pendiente) | "Esto es transcripción real, mientras él habla." | *pendiente del pipeline* | R18, R13a |
| 7 | 0:22–0:26 | Traducción ES apareciendo junto a la transcripción | `plano-07.mkv` (pendiente) | "Y traducción, al instante." | *pendiente del pipeline* | R19 |
| 8 | 0:26–0:30 | Terminal: se tipea el comando | `reportes/video/terminal-render.mp4` [0–4s] (generado hoy) | "Todo esto, con un solo comando." | *pendiente del pipeline* | R16 |
| 9 | 0:30–0:34 | `docker compose up` corriendo, logs reales | `terminal-render.mp4` [4–8s] (generado hoy) | *(sin narración)* | "docker compose up" (fijo, no sale del pipeline) | R16 |
| 10 | 0:34–0:39 | Dos teléfonos lado a lado, salas/idiomas distintos | `plano-10.mkv` (pendiente) | "Dos salas. Al mismo tiempo. En vivo." | *pendiente del pipeline* | R21 |
| 11 | 0:39–0:43 | Panel de monitoreo | `plano-11.mkv` (pendiente) | "Y alguien, del otro lado, cuidando que funcione." | *pendiente del pipeline* | R8e |
| 12 | 0:43–0:47 | Vuelve al teléfono | `plano-12.mkv` (pendiente) | "Ella no se pierde nada." | *pendiente del pipeline* | R20 |
| 13 | 0:47–0:52 | Selector de idioma / chip de conexión | `plano-13.mkv` (pendiente) | "Abierto. Gratis. Para cualquier conferencia." | *pendiente del pipeline* | R15, R5 |
| 14 | 0:52–0:58 | Tarjeta final: nombre + open source + repo | `reportes/video/cards/card_final_guionA.png` (pendiente de render, método de `reportes/demo-video-v2.md`) | "Entender una charla, en cualquier idioma." | *pendiente del pipeline* | R13b, R15 |

**Subtítulo EN:** ninguno se escribió a mano como definitivo. Salen de
`docs/video/narracion-final-en.srt`, generado con el pipeline real (ASR+traducción ES→EN, R14) sobre
la narración concatenada — ver "Estado de la corrida" abajo. La única excepción fija es el plano 9
("docker compose up"), que es el propio comando tecleado, no una traducción.

## Medición del crop (orador real)

Tres fotogramas de muestra del propio `orador-en-booch-295-405.mp4` (t=20s, 45s, 70s locales):
en 45s y 70s el subtítulo ES quemado está abajo (banda ~y590–672 de 720, confirmado a ojo); en 20s
apareció arriba en vez de abajo (caso no explicado, no confirmé si es frecuente). Filtro elegido con
margen: `crop=1280:580:0:0,scale=2384:1080,crop=1920:1080:232:0` (recorta banda inferior con margen,
reescala a cubrir 1920x1080). Los `start_s` de los planos 1 y 6 (40s y 66s) se eligieron cerca de los
dos tramos confirmados con subtítulo abajo.

## Rotulado de REPLAY (decisión marcada, no elegida en silencio)

Las tomas de interfaz (planos 2,3,5,6,7,10,11,12,13) se graban sobre sesiones **replay** (casetes
reales ya grabados, sin gastar cuota de Gemini — ver `docs/video/tomas.md`). La skill `entrega`
exige "todo tramo replay rotulado en pantalla". En vez de un banner grande (lo que
`reportes/video-referencias.md` sección 6 pidió NO repetir de `corte-v2.mp4`), se usa el chip nativo
`chip-replay` que ya trae `web/sesion.html` — visible en todas las URLs de `tomas.md` porque ninguna
usa `?modo=obs` (ese modo esconde toda la cabecera, incluido el chip; confirmado en
`web/estilo.css`). **Si Ricardo prefiere un rótulo más visible, avisar antes de componer.**

## Hallazgo reportado (no es mío para arreglar, es de `ops`)

Al correr `MODO=replay docker compose -f ops/docker-compose.yml up` para el plano 8–9,
`ops/entrypoint-worker.sh` usa el glob `fixtures/casetes/*-trad*.jsonl`, que también matchea
`b5r-en-traducciones-defectuosas.jsonl` (contiene "trad" dentro de "traducciones" pero NO es un
casete v1 válido) → ese subproceso crashea y reinicia cada 3 s en loop indefinido (7 de los 8
casetes matcheados sí sirven bien). Las líneas de este plano se eligieron SIN ese crash (recorte
honesto de la corrida real, ver `docs/video/terminal-lineas.json`), pero el bug es real y afecta a
quien corra el mismo comando hoy. Reportado al orquestador/ops, no lo arreglé (no es mi carpeta).

## Estado de la corrida (para el reporte completo, ver `reportes/demo-video-final.md`)

Narración: 12 WAV con Cartesia Iria fast + 2 silencios (planos 1 y 9, sin locución por guion) en
`reportes/video/narracion_final_wav/`. Concatenación + pipeline real (`worker.run --lang es
--sesion narracion-final`) + export de `docs/video/narracion-final-en.srt`: **ver reporte** — sujeto
al cupo de Gemini autorizado (≤ 1,5 min) y al tiempo restante del bloque (prioridad 6 de 6 según el
orquestador).
