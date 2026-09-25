# Tomas pendientes — Guion A (grabación del orquestador con OBS)

Lista exacta y en orden para que la sesión de grabación dure **menos de 10 minutos**. Referencia:
`docs/video/guion-final.md` (planos con narración y cita R#) y `docs/video/guion-final.json`
(tiempos que usa `componer.py`). Cada toma es un archivo **separado**: `reportes/video/tomas/plano-NN.mkv`.
`componer.py` falla claro si falta alguno (ver ese script).

## Preparación (una sola vez, antes de la primera toma)

1. Verificar que están arriba (no relanzar si ya responden): hub `curl localhost:8100/health`,
   web `curl -o /dev/null -w "%{http_code}" localhost:8101/` (200), panel `curl -o /dev/null -w
   "%{http_code}" localhost:8102/` (200).
2. Servir el mockup de teléfono (si el proceso de este bloque ya no está vivo):
   `.venv/Scripts/python -m http.server 8106 --directory docs/video` (verificar antes con
   `netstat -ano | findstr :8106`; si NO está libre, usar otro puerto y ajustar las URLs de abajo).
3. Chrome maximizado a 1920x1080 en un display/ventana limpio (mismo método que bloques anteriores,
   `reportes/video/obs_ctl.py status` para confirmar la conexión WebSocket a OBS).
4. Silenciar notificaciones/actualizaciones de Windows (marca de agua "Activar Windows" se tapa en
   post con `drawbox`, no hace falta esconderla ahora).

Patrón por toma: `python reportes/video/obs_ctl.py start` → esperar ~1 s → navegar/preparar la
vista → sostener **toma_s** segundos (ya incluye 1–2 s de margen para recortar en post) → `python
reportes/video/obs_ctl.py stop` → renombrar el `.mkv` que OBS acaba de escribir a
`reportes/video/tomas/plano-NN.mkv`.

## Sesiones replay a levantar (SIN gastar cuota de Gemini)

Reusan casetes reales ya grabados (contenido real de charlas, no inventado). Reiniciar el comando
de replay **justo antes** de cada toma que dependa de "captions creciendo desde el principio"
(planos 2, 3, 12): así no hay que cronometrar contra un replay que ya terminó.

```
# Sala A: charla en inglés (Booch), con traducción a español ya presente en el casete
.venv/Scripts/python -m worker.replay fixtures/casetes/b8-trad-vivo-en.jsonl \
    --hub ws://localhost:8100/ingest --sesion video-final-en

# Sala B: charla en español, con traducción a inglés (sólo para el plano 10, dos salas)
.venv/Scripts/python -m worker.replay fixtures/casetes/b8-trad-vivo-es.jsonl \
    --hub ws://localhost:8100/ingest --sesion video-final-es
```

**Pedido de Ricardo (23:55): en CADA toma de teléfono (planos 2, 3, 10, 12) el replay tiene que
estar ACTIVAMENTE reproduciendo en ese momento** — el chip de estado debe decir "en vivo" (punto
verde), nunca "sin texto hace N s". Si aparece ese aviso, el replay ya terminó: reiniciá el comando
de esa sala (mismo `--sesion`, se puede repetir) y esperá 1–2 s antes de `obs_ctl.py start`. Por eso
la columna "Comando previo" de la tabla dice "reiniciar" en cada toma de teléfono, incluida la 10
(las dos salas).

Nota de honestidad (R-anti-alucinación): son sesiones **REPLAY**, no ASR en vivo. El chip nativo
`REPLAY` del propio `web/sesion.html` (`chip-replay`) ya queda visible en todas las vistas de abajo
que NO usan `?modo=obs` — por eso ninguna toma de esta lista usa `?modo=obs` (ese modo esconde toda
la cabecera, incluido el chip). Esto reemplaza al banner amarillo grande que `corte-v2.mp4` usaba y
que `reportes/video-referencias.md` (sección 6, punto 5) pidió no repetir. **Decisión marcada para
el orquestador/Ricardo: si prefieren un rótulo más explícito que el chip chico, avisar antes de
componer** (no lo decidí yo solo por el peso de la regla "reportar, no elegir en silencio").

## Lista de tomas (en orden)

| Plano | Archivo esperado | Comando previo | URL a abrir | Vista/zoom | toma_s |
|---|---|---|---|---|---|
| 2 | `plano-02.mkv` | reiniciar replay Sala A | `http://localhost:8106/telefono.html?url=http://localhost:8101/s/video-final-en?lang=es` | tal cual (1920x1080) | 6 |
| 3 | `plano-03.mkv` | (deja correr el mismo replay de la toma anterior) | igual que plano 2 | tal cual, seguir grabando ~3 s más tarde en el mismo scroll | 6 |
| 4 | `plano-04.mkv` | — | `http://localhost:8101/` | zoom del navegador ~175% centrado en un card con QR | 5 |
| 5 | `plano-05.mkv` | — | `http://localhost:8101/` | zoom 100%, se vean 2+ sesiones listadas | 6 |
| 6 | `plano-06.mkv` | replay Sala A si no está corriendo | `http://localhost:8101/s/video-final-en?lang=en&modo=proyeccion` | tal cual (esto es SÓLO la mitad "interfaz"; el orador ya existe en `reportes/video/orador-en-booch-295-405.mp4` y se combina en `componer.py`, tipo `split`) | 6 |
| 7 | `plano-07.mkv` | (mismo replay) | `http://localhost:8101/s/video-final-en?lang=es&modo=proyeccion` | tal cual | 6 |
| 10 | `plano-10.mkv` | reiniciar replay Sala A **y** Sala B | `http://localhost:8106/telefono.html?url=http://localhost:8101/s/video-final-en?lang=es&url2=http://localhost:8101/s/video-final-es?lang=en` | tal cual, dos teléfonos | 7 |
| 11 | `plano-11.mkv` | — | `http://localhost:8102/` | tal cual (con las 2 sesiones replay corriendo de fondo para que se vea actividad) | 6 |
| 12 | `plano-12.mkv` | reiniciar replay Sala A | igual que plano 2 | tal cual | 6 |
| 13 | `plano-13.mkv` | — | `http://localhost:8101/s/video-final-en?lang=es` | tal cual, encuadrar la cabecera (selector de idioma + chip) | 6 |

Total estimado: preparación ~2 min + 9 tomas × (≈10 s de maniobra + toma_s) ≈ 3–4 min. Margen amplio
contra el límite de 10 min.

## Qué NO grabar (ya existe o lo genera `demo` con script, sin OBS)

- Plano 1 (orador real): `reportes/video/orador-en-booch-295-405.mp4`, ya recortado en `componer.py`
  (crop de la franja de subtítulos ES quemados; medido en dos fotogramas de muestra t=45s/70s del
  propio clip — ver `docs/video/guion-final.md`).
- Planos 8–9 (terminal): `docs/video/terminal.html` con salida real de
  `MODO=replay docker compose up`, sin cámara ni grabación de pantalla (animación CSS/JS con las
  líneas reales).
- Plano 14 (tarjeta final): PNG renderizado desde `reportes/video/rotulos.html`.

## Después de grabar

1. Matar los dos `worker.replay` (Ctrl+C o `taskkill`).
2. Confirmar los 9 archivos: `ls reportes/video/tomas/plano-{02,03,04,05,06,07,10,11,12}.mkv`.
3. Correr `python docs/video/componer.py --guion docs/video/guion-final.json --salida
   reportes/video/final.mp4` (falla con un mensaje puntual si falta un archivo).
