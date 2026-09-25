# web/ — vista de audiencia (R6, R20)

Estático puro: HTML/CSS/JS servible sin build. Contrato de mensajes: [`contracts/README.md`](../contracts/README.md)
(congelado; esta carpeta sólo lee, nunca escribe el bus). Decisiones de diseño: skill `ui-subtitulos`
(fondo `#111`, texto `#E8E8E8`, parcial gris ≥4,5:1, fuente `clamp(24px, 4.4vh, 28px)`, append puro,
scroll que se rinde, chip de 4 estados, hueco visible, sesión+idioma en la URL).

## Archivos

| Archivo | Qué es |
|---|---|
| `index.html` | Índice de sesiones (R6): lista lo que reporta `GET /api/sesiones` cada 5s, con badge de estado, idiomas disponibles, QR + "copiar enlace" por sesión (ver abajo). Oculta sesiones `test:true` salvo `?test=1`; ordena por urgencia (`live` > `idle` > `ended`). |
| `sesion.html` | Cabecera (volver al índice, título, selector de idioma como segmented control, chips, botón de modo proyección) + contenedor de líneas + botón "volver al vivo". Sin lógica: todo el comportamiento vive en `app.js`. |
| `app.js` | Vista de UNA sesión: conecta `ws://<host>/ws/<id>?lang=<xx>`, pinta `text`/`partial`/`translation` con append puro, arma el chip de conexión (heartbeat + tolerancia 250ms), el scroll que se rinde, los huecos visibles (por `seq` y por audio), el modo proyección y el modo OBS (ver abajo). Sesión e idioma salen de la URL (`location.pathname` / `?lang=`). |
| `tokens.css` | Copia literal de `docs/design/tokens.css` (sistema de diseño, Bloque 10): variables de color/tipografía/espaciado/movimiento. Se enlaza ANTES de `estilo.css` en los dos HTML. No se edita acá: si cambia, se vuelve a copiar desde `docs/design/`. |
| `estilo.css` | Toda la hoja de estilos (índice + sesión + modo proyección + modo OBS), sobre los tokens de arriba. Un solo archivo: no hay preprocesador ni build. |
| `servir.py` | Servidor estático de DEV (`.venv/Scripts/python web/servir.py --puerto 8101`): sirve esta carpeta y reescribe `/s/<lo-que-sea>` a `sesion.html` (el ruteo de sesión/idioma es 100% del lado cliente). No sirve `/api` ni `/ws`: eso lo da el hub en `:8100` (dev) o el mismo origen si lo sirve `hub/estaticos.py` (ver "Cómo se sirve"). Bindea `127.0.0.1` por default (revisión de seguridad B10); `--host 0.0.0.0` para exponerlo a la LAN a propósito (p. ej. para que un celular escanee el QR del índice sin pasar por el hub). |
| `vendor/qrcode-generator/` | Librería de terceros vendorizada (ver "Vendor" abajo). |

## Cómo se sirve

Dos modos, mismo HTML/JS/CSS, sin cambiar código (`app.js`/`index.html` detectan el origen solos,
ver comentario `calcularOrigen` / "B4: mismo origen" en cada archivo):

1. **Dev, dos procesos** (lo que usa este agente): `web/servir.py` sirve estos archivos está en
   `:8101`; el hub corre aparte en `:8100`. El front habla al hub por `:8100` porque detecta que su
   propio puerto es `8101`.
2. **Same-origin, un solo proceso**: el propio hub, levantado con `--web web` (y opcional
   `--panel panel`), sirve esta carpeta y `/api`, `/ws` en el MISMO puerto (por ejemplo `:8080`).
   El front detecta que no está en `:8101` y habla a `location.host` directo. `?hub=<host:puerto>`
   pisa esta detección en cualquiera de los dos modos (para probar contra un hub que no es ninguno
   de los dos anteriores).

## Vendor

`vendor/qrcode-generator/`: [**qrcode-generator**](https://github.com/kazuhikoarase/qrcode-generator)
de Kazuhiko Arase, **licencia MIT** (`vendor/qrcode-generator/LICENSE`, copiada del repo oficial).
Versión **1.4.4**, bajada una sola vez (sin CDN en runtime, sin `npm install` en el repo) desde
jsDelivr:

```
curl -o web/vendor/qrcode-generator/qrcode.js     https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js
curl -o web/vendor/qrcode-generator/qrcode.min.js https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js
curl -o web/vendor/qrcode-generator/LICENSE       https://raw.githubusercontent.com/kazuhikoarase/qrcode-generator/master/LICENSE
```

`index.html` carga sólo `qrcode.min.js` (20,8 KB, minificado por jsDelivr con Terser a partir del
mismo `qrcode.js@1.4.4`, no built acá). `qrcode.js` (56,7 KB, el original sin minificar, con el
encabezado de copyright MIT) se deja al lado para auditar que el `.min.js` corresponde a la misma
versión, sin tener que confiar en el CDN. Uso en `index.html`: `qrcode(0, 'M').addData(url); .make();
.createSvgTag({cellSize:4, margin:8, scalable:true})` → un `<svg>` inline por sesión, sin `<canvas>`
ni dependencias.

## QR por sesión (R6, "del QR al texto en 2 toques")

Cada tarjeta del índice trae un QR que apunta a la URL ABSOLUTA de la vista en el idioma ORIGINAL de
esa sesión (`http(s)://<host>/s/<id>?lang=<original>`, `location.origin` del propio índice: mismo
origen en los dos modos de servido de arriba) más un botón "copiar enlace" (con fallback
`execCommand('copy')` para cuando la página se sirve por `http` plano en la LAN de un evento, donde
`navigator.clipboard` no está disponible por no ser contexto seguro). Toque 1 = escanear con la
cámara; toque 2 = abrir el link que ofrece el sistema operativo.

## `?hub=` sólo admite hosts propios (seguridad, Bloque 10)

`index.html` y `app.js` validan el override `?hub=<host[:puerto]>` (pensado para desarrollo: apuntar
a un hub propio, distinto del que sirve o del `:8100` de dev) contra una lista fija: `localhost` ·
`127.0.0.1` · `[::1]` · el propio `location.hostname` de la página. Cualquier otro host se IGNORA
(se sigue la resolución normal, como si no hubiera `?hub=`) y queda avisado, nunca en silencio:
consola (`console.warn`) + un chip de texto (`#chip-hub-invalido` en `sesion.html`, párrafo
`#aviso-hub-ignorado` en `index.html`). La vista de audiencia no maneja ningún token, así que esto
no filtra un secreto; el motivo es no dejar que un enlace armado por un tercero (por ejemplo
compartido como si fuera el QR de una sesión) haga que la pestaña de otra persona mande fetch/WS a
un host que no elegimos nosotros.

## Modo proyección (`?modo=proyeccion`, tecla `p`, C5)

Pensado para proyectar la sala: tipografía enorme (`--tam-subtitulo-proyeccion`), cabecera reducida
a una franja mínima (sólo chip + el propio botón), y sólo se ven las últimas líneas (recorte por
CSS, `nth-last-child`; el DOM con el historial completo sigue intacto, append puro sin cambios). Se
activa con el botón de la cabecera (`#boton-proyeccion`, `aria-pressed`), la tecla `p` (si el foco no
está en un campo de texto) o `?modo=proyeccion` en la URL; la preferencia se guarda en
`localStorage` por origen (best-effort: si falla, no bloquea nada). Salir deja la vista exactamente
como estaba: no se toca el DOM, sólo la clase `body.modo-proyeccion`.

## Modo OBS / vMix (`?modo=obs`, R8a — integración con producción de video)

`?modo=obs` sirve esta misma vista como **fuente de navegador** para quemar los subtítulos sobre un
stream, sin tocar el pipeline ni el contrato: fondo transparente, sin cabecera/chip/selector, sin
scroll ni animación de entrada, sólo las últimas 2 líneas confirmadas + la parcial, ancladas abajo
con una caja semitransparente detrás del texto (legible sobre cualquier video). No es interactivo
(no hay botón ni atajo: nadie "usa" una fuente de navegador). Es un modo aparte de "modo proyección"
(no se combinan); gana si está presente en la URL.

**OBS Studio** — Fuentes → `+` → *Navegador* (Browser Source):
- URL: `http://<host-del-hub>:<puerto>/s/<sesion>?lang=<xx>&modo=obs` (mismo origen: el hub
  levantado con `--web web`, ver `hub/README.md`; en dev sin same-origin hace falta además
  `&hub=<host:8100>`, sujeto a la validación de la sección anterior).
  Ejemplo con el hub sirviendo todo en `:8080`: `http://localhost:8080/s/sala-1?lang=es&modo=obs`.
- Ancho/alto: 1920x1080 (o el tamaño del canvas). Tildar "Shutdown source when not visible" OFF
  (si no, se corta el WebSocket cada vez que se cambia de escena) y "Refresh browser when scene
  becomes active" según se prefiera.
- Fondo transparente: no hace falta ninguna opción extra de OBS, `body.modo-obs` ya pinta
  `background: transparent` (el navegador embebido de OBS soporta alpha por defecto).

**vMix** — Input → `+ Add Input` → *Web Browser*: misma URL de arriba
(`http://<host>:<puerto>/s/<sesion>?lang=<xx>&modo=obs`), resolución 1920x1080. vMix compone sobre
el fondo transparente igual que un input con canal alfa.
