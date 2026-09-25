# panel/ — Panel de producción (monitor, R8e)

Dos vistas con un switch arriba, persistido en `localStorage` (clave `panelVista`), **Informativa por
defecto**:

- **Informativa**: una tarjeta por sala, en lenguaje llano, ORDENADA POR URGENCIA (lo que hay que
  atender primero). Pensada para alguien que no conoce la implementación.
- **Técnica**: la tabla de siempre (p50/p95, `seq`, `t_emit − t_captured`, `ok:false`, etc.), sin
  cambios de fondo respecto de los bloques anteriores. Pensada para quien mira `worker.log`/`hub`.

Pedido directo de Ricardo (bloque final), a partir de los hallazgos M8/M9/"¿se ve hecho con IA?" de
`reportes/adversario-final-ux.md` y M1 de `reportes/adversario-final-seguridad.md`.

## Reglas de urgencia de la vista Informativa (código: `panel/app.js`, función `estadoInformativo`)

Las tarjetas se ordenan por un número de urgencia (menor = más urgente). Un estado más urgente
**siempre** gana sobre uno menos urgente, salvo `terminada` que es definitivo:

| # | Estado mostrado | Cuándo | Umbral |
|---|---|---|---|
| 6 (visible primero: `hubState==='ended'`, chequeo ANTES que todo) | **Terminada** | La sesión ya cerró (`session_end`). No hay nada que hacer. | — |
| 0 | **Sin texto todavía (hace N s)** | La sala está en vivo desde hace más de 30 s y TODAVÍA no mandó ningún `type=text`. Es el caso que antes (M8) quedaba en verde para siempre ("en vivo (sin texto todavía)"). | `UMBRAL_NUNCA_TEXTO_S = 30` |
| 0 | **Sin texto hace N s** | La sala tuvo texto y dejó de mandarlo hace más de 20 s. | `UMBRAL_SIN_TEXTO_ALARMA_S = 20` |
| 1 | **Se trabó y se reabrió (N s perdidos)** | Hubo una `rotation` con motivo `atasco` o `cierre` en los últimos 60 s. El motivo `preventiva` y `goaway` NO alarman: son rotaciones de rutina del propio diseño ("rotación transparente de sesiones"), no una falla. | `VENTANA_ROTACION_RECIENTE_S = 60` |
| 2 | **Traducción fallando (F de N)** | De las últimas 20 traducciones (`type=translation`, item por item), al menos 15 % falló (`ok:false`), con un mínimo de 5 muestras para no alarmar con 1 de 1. | `UMBRAL_TRAD_MUESTRAS_MIN = 5`, `UMBRAL_TRAD_TASA = 0.15`, ventana 20 |
| 3 | **Reconectando** | La conexión WS propia del panel a esa sala (no la del espectador) está reintentando tras haberse cortado. | — |
| 4 | **Al día** / **Replay** | Todo lo de arriba no aplica. "Replay" si `replay:true`, "Al día" si `replay:false` (ASR real). | — |
| 5 | **Inactiva** | El hub marca la sesión `idle` y NO se cumple ninguna de las dos alarmas de "sin texto" de arriba. | — |

**Orden de los chequeos, a propósito:** "sin texto" (0) se evalúa ANTES que "Inactiva" (5). Al cortar
el worker de una sesión, el hub dejar de recibir mensajes del productor y la marca `idle` bastante
antes de los 20/30 s de estos umbrales; si `idle` se mirara primero, la tarjeta diría "Inactiva" (sin
urgencia) justo en el caso que más hay que atender. Esto se encontró y se corrigió durante la
verificación de este mismo bloque (ver `reportes/monitor-final.md`, "cortar el worker").

## Resumen de arriba

`N en vivo` (sesiones con `hubState==='live'`) · `N a atender` (urgencia ≤ 3: las dos de "sin texto",
"se trabó", "traducción fallando" y "reconectando") · `N sin problemas` (urgencia 4: "Al día"/"Replay")
· hora del último dato (el mayor entre el último `t_hub` de un `text` y el último `t_hub` de
`/api/sesiones` de cada sala).

## Aviso con voz (M8)

Las dos alarmas de "sin texto" (30 s sin texto nunca, 20 s de silencio) además dicen una frase en voz
alta (`SpeechSynthesisUtterance`, `.lang` = `es-AR` o `en-US` según el idioma elegido en el selector
ES/EN — ver más abajo), UNA vez por episodio (no se repite en cada re-render de 1 s; se vuelve a armar
si la sala se recupera y se calla otra vez). Se activa/desactiva y se ajusta el volumen (0–100 %,
**default 70 %**) desde **Configuración → Avisos** (persistidos en `localStorage`, claves
`panelSonido` y `panelVolumen`); apagarlo NO oculta la alarma visual (texto + color + ícono siguen
ahí, skill `ui-subtitulos`: el estado nunca depende de un solo canal). Botón "probar aviso" para
escuchar el volumen/idioma actual sin esperar una alarma real. Si el navegador no tiene
`speechSynthesis`, se degrada en silencio.

## Ayuda "?" flotante (pedido Ricardo, bloque config/idioma)

El botón "?" (uno por vista, sólo el de la vista activa está visible) ya NO despliega el texto en
línea (empujaba el layout, `<details>` nativo): abre un **popover flotante** (`position: fixed`,
`panel/app.js::crearFlotante()`), anclado debajo del botón, ancho máx. 420 px, sombra sobria
(`--sombra-flotante`), `z-index` sobre todo el panel. Cierra con **Esc**, **clic afuera** o el
**mismo botón** (toggle); maneja foco (al abrir, foco al botón de cerrar `×`; al cerrar, foco vuelve
al botón "?"); `role="dialog"` + `aria-modal="false"` + `aria-labelledby`, `aria-expanded` en el
botón. En pantallas ≤480 px ocupa el ancho con 16 px de margen a cada lado (CSS, no JS).

La ayuda de la vista **Informativa** es una **lista escaneable** (pedido explícito: un párrafo corrido
era difícil de leer), no un párrafo: una fila por estado con el **mismo color e ícono** que la franja
de la tarjeta correspondiente (`--color-exito` verde, `--color-error` rojo, `--color-atencion` y
`--color-aviso` ámbar, `--color-texto-secundario` gris, `--color-texto-silenciado` neutro — mismos
tokens de `tokens.css`, sin paleta nueva) y una línea de explicación al lado (el color es sólo
clasificador; el texto es la fuente real). Debajo, 3 bullets (orden por urgencia, "Qué hacer" es
sugerencia, dónde se configura el aviso con voz); al pie, en gris, la referencia a la vista Técnica.
Construida en JS (`construirAyudaInformativa()` en `panel/app.js`) a partir de claves de
`panel/i18n.js` (`ayuda_fila_*`, `ayuda_bullet_*`, `ayuda_pie_tecnico`), para poder traducirla sin
tocar HTML. La ayuda de la vista **Técnica** sigue siendo un párrafo (jerga con `<code>`/`<b>`, no
pedido a rediseñar).

## Configuración (drawer, pedido Ricardo)

Botón de engranaje "Configuración" en la cabecera abre un **drawer lateral** (`position: fixed;
right:0`, ancho 360 px, overlay semitransparente para detectar clic afuera; a ≤480 px ocupa toda la
pantalla). Cierra con Esc, clic en el overlay o el botón `×`. Reemplaza la fila que antes vivía suelta
en la cabecera (token, mostrar pruebas, aviso con voz):

- **Acceso**: token de `/api/metricas` (input, guardar, borrar) + estado "métricas: con
  token"/"sin token"/error. El token sigue en **sessionStorage** (nunca en la URL), como antes.
- **Vista**: "mostrar pruebas" + conteo de sesiones TEST. Ahora persistido en **localStorage**
  (clave `panelMostrarPruebas`; antes NO persistía entre recargas).
- **Avisos**: aviso con voz on/off + volumen (0–100 %, default 70 %) + botón "probar aviso".

El chip de estado del hub y "actualizado" quedan visibles en la cabecera (no se movieron).

## Idioma ES/EN (`panel/i18n.js`, pedido Ricardo)

Botón "ES | EN" en la cabecera cambia TODOS los textos visibles (cabecera, switch, resumen, tarjetas,
"qué hacer", ayuda, configuración, tabla técnica + encabezados, pie, título de la pestaña) vía un
diccionario plano `panel/i18n.js` (**sin dependencias**, `window.PanelI18n`). Persistido en
`localStorage` (clave `panelLocale`); por defecto según `navigator.language` (`es*` → `es`, resto →
`en`); `?locale=en|es` en la URL fuerza el valor para esa carga (tiene prioridad incluso sobre lo
guardado). `<html lang>` se actualiza al cambiar. Los nombres de idioma de las salas también se
traducen (`PanelI18n.nombreIdioma()`, "inglés → español" / "English → Spanish"). Los números
(contadores, `n=`, segundos) usan `toLocaleString('es-AR'|'en-US')` — separador decimal coma en ES,
punto en EN (ej. `p50 0,442 s` vs `p50 0.442 s`).

**Decisión documentada, NO se traducen** (jerga/valores literales del contrato, iguales en los dos
idiomas): `LIVE`/`REPLAY`/`TEST` (badges), los motivos de rotación `cierre`/`atasco`/`preventiva`/
`goaway`, `ok:true`/`ok:false`, `type=text`, nombres de campo (`t_emit`, `t_captured`, `seq`), rutas
de archivo, `?hub=host:puerto`, y la notación estadística `p50`/`p95`/`n=`.

Verificado sin texto visible sin traducir en EN (recorrida manual de las 4 vistas + captura, ver
`reportes/monitor-config.md`); se encontraron y corrigieron dos strings que habían quedado en español
fuera del diccionario (`trad:` en la columna Idioma de la tabla técnica, el `<title>` de la pestaña).

## Qué NO tiene jerga en la Informativa

Nada de `t_emit`, `t_captured`, `seq` ni `ok:false` en las tarjetas ni en su "?": eso vive sólo en la
vista Técnica. Verificado con un chequeo automático (`qa/out/panel-final/chequeo-jerga.json`,
`jerga_en_informativa: []`).

## Seguridad: `?hub=` (M1 de `reportes/adversario-final-seguridad.md`)

Antes de este bloque, `?hub=localhost:8100@evil.example` pasaba el chequeo de host porque
`hostnameDe()` corta en el último `:` y nunca mira el `@`: con el parser WHATWG del `WebSocket`, esa
cadena entera es userinfo y el socket termina abriéndose contra `evil.example`. Ahora
(`hubParamSeguro()` en `panel/app.js`) se rechaza el valor si:

1. Contiene `@`, `/`, `\`, `?` o `#` (los separadores que esconden un host detrás de otro), **o**
2. `new URL('http://' + hub)` termina con `username`/`password` no vacíos, **o**
3. El host no es `localhost`/`127.0.0.1`/`[::1]`/el mismo `location.hostname` (chequeo que ya existía).

Cualquier rechazo se avisa en la cabecera con texto ("hub externo ignorado por seguridad: …"), nunca
sólo en consola. Mismo hallazgo (M1) afecta a `web/app.js`/`web/index.html`, que son de `frontend`
(pedido cruzado, ver reporte).

## Cómo probarlo (reproducible)

Hub propio (nunca 8100/8101/8102/8105/8106/8107):

```
HUB_TOKEN=tok-panel HUB_PORT=8195 HUB_HOST=127.0.0.1 .venv/Scripts/python -m hub
```

4–6 salas con `worker.replay` contra ese hub (casetes de `fixtures/casetes/evidencia-25-09/`, uno con
atascos reales: `sala-b-051619.jsonl`):

```
HUB_TOKEN=tok-panel .venv/Scripts/python -m worker.replay fixtures/casetes/evidencia-25-09/sala-b-051619.jsonl --hub ws://127.0.0.1:8195/ingest --sesion sala-atascos --velocidad 0.5
```

Panel propio: `.venv/Scripts/python panel/servir.py --puerto 8198`, abrir
`http://127.0.0.1:8198/?hub=127.0.0.1:8195`.

Recalcular p50/p95 desde el casete en disco y comparar con la fila de la vista Técnica:

```
.venv/Scripts/python panel/recalcular.py fixtures/casetes/evidencia-25-09/sala-a-051619.jsonl
```
