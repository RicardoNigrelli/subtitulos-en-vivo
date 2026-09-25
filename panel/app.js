// app.js — panel de producción (monitor, Bloque 3, R8e; same-origin + Bearer client-side: B5/B8)
//
// Fuentes de datos (contracts/README.md, hub/README.md; skill ui-subtitulos "Panel de producción"):
//   - GET /api/sesiones cada 2 s: session_id, lang, title, replay, last_seq, last_t_emit, last_t_hub,
//     state (live/idle/ended), translations_langs, source, test, viewers, viewers_por_idioma. Público,
//     sin token (source/test: B4, rótulo de sesiones de prueba, ver más abajo).
//   - GET /api/sesiones/<id>/historial?desde=<seq>&tipos=todos cada 2 s: backfill de TODO lo que el
//     hub todavía tiene en memoria (text, rotation, watchdog, error, session_start, session_end).
//     `translation` y `partial` son EFÍMEROS (contracts: TIPOS_EFIMEROS): el hub NUNCA los guarda,
//     sólo se ven pasando por el WS en vivo mientras el panel está conectado.
//   - WS público /ws/<session_id> (sin token, igual que cualquier espectador): stream en vivo de
//     TODOS los tipos salvo el heartbeat del worker (el hub no lo reenvía a audiencia). Es la única
//     fuente para `partial`, `translation` y para la latencia PERCIBIDA (t_receive - t_captured).
//   - GET /api/metricas: requiere `Authorization: Bearer <HUB_TOKEN>` (hub/README.md). El panel
//     SIEMPRE la pide directo a `HTTP_BASE` con el Bearer que tipeó el operador (sessionStorage,
//     NUNCA en la query string ni en la URL: skill ui-subtitulos "Token"); sin token no se llama al
//     endpoint y las celdas que dependen de él (audio estimado) y el chip dicen "sin token". Hasta
//     B9 `panel/servir.py` (8102) tenía un proxy `/panel-api/metricas` que agregaba el Bearer real
//     del lado del servidor: ELIMINADO en B10 (hallazgo ALTO, `reportes/seguridad.md` #1: SSRF +
//     fuga del token vía `?hub=` sin validar). Ahora 8102 y el hub sirviendo el panel directo
//     (`--panel`, 8080/8195/etc.) se comportan IGUAL: el operador tipea el token una vez. El resto
//     del panel (rotaciones, watchdog, errores, ok:false, parciales/min, viewers) sale de
//     /api/sesiones y del WS público, sin token.
//
// B5: resolución de origen del hub — MISMO PATRÓN que web/app.js (frontend), mismo razonamiento: la
// única señal confiable del lado cliente es el puerto. `panel/servir.py` (dev, este agente) siempre
// corre en 8102; cualquier otro origen (el hub sirviendo `--panel panel` en su propio puerto, p. ej.
// 8080 o uno de prueba) se interpreta como "me sirve el hub" y habla a `location.host` directo.
// `?hub=host:puerto` pisa esto — DESDE B10 sólo si el host es localhost/127.0.0.1/[::1] o el mismo
// `location.hostname` (hallazgo ALTO #2 de `reportes/seguridad.md`: un link `?hub=atacante.example`
// hacía que el navegador del operador mandara su Bearer real a un host arbitrario). Cualquier otro
// valor se IGNORA (se sigue la resolución same-origin/8100 de siempre) y la cabecera muestra un
// aviso con texto (`#chip-hub-seguridad`), nunca sólo en consola. [SUPUESTO: monitor, siguiendo la
// convención ya usada por frontend en B4] no hay forma de saber con certeza el origen sin una vuelta
// previa; la validación de abajo es la que decide, no una adivinanza.
//
// B5/B8: sesiones `test:true` (fixtures `contracts/ejemplos/`, transporte de casete sin API — NO son
// ASR en vivo) llevan el rótulo TEST junto al idioma y están ocultas por defecto (fila-oculta, CSS);
// toggle "mostrar pruebas" las trae de vuelta y el chip de al lado cuenta cuántas hay (B8: cableado
// del toggle e input de token, que en B5 quedaron escritos en el HTML pero sin listener en JS).
// B5/B8: `rotation.meta.audio_lost_s`, cuando viene, se acumula por sesión y se muestra junto al
// detalle de "Rotaciones" (B8: en B5 se acumulaba pero ninguna celda lo mostraba).
//
// Reglas de la skill ui-subtitulos ("Panel de producción"): p50/p95 SIEMPRE (nearest-rank), NUNCA
// promedio; estado nunca sólo por color; rotaciones/reaperturas/errores como contadores con
// timestamp del último.
//
// Bloque "config/idioma" (pedido directo de Ricardo, 25/09): "?" pasa a ser un popover FLOTANTE
// anclado al botón (nunca en línea, nunca empuja el layout); token/mostrar pruebas/aviso con voz se
// mudan de la cabecera a un drawer de Configuración (botón de engranaje); se agrega un selector de
// idioma ES/EN (`panel/i18n.js`, sin dependencias) que traduce TODO el texto visible, persistido en
// localStorage, con `?locale=` forzando el valor de esta carga. El aviso con voz ahora usa la voz y
// el volumen (0–100 %, default 70 %) del idioma elegido.
'use strict';

(function () {
  var I18N = window.PanelI18n;
  function t(key, vars) { return I18N ? I18N.t(key, vars) : key; }

  // ---------------------------------------------------------------- configuración
  var params = new URLSearchParams(location.search);
  var hubParamCrudo = params.get('hub');

  // B10 (seguridad, reportes/seguridad.md hallazgo ALTO #2): `?hub=` sólo se acepta si apunta a
  // localhost/127.0.0.1/[::1] o al mismo host que sirve esta página. Cualquier otro valor se
  // ignora COMPLETO (nunca se arma HTTP_BASE/WS_BASE con él, nunca se le manda el Bearer) y queda
  // registrado en `hubIgnoradoPorSeguridad` para avisar en la cabecera con texto.
  function hostnameDe(hostPuerto) {
    if (!hostPuerto) return '';
    var m = String(hostPuerto).match(/^\[([^\]]+)\](?::\d+)?$/); // [::1]:puerto
    if (m) return m[1].toLowerCase();
    var idx = String(hostPuerto).lastIndexOf(':');
    return (idx > -1 ? String(hostPuerto).slice(0, idx) : String(hostPuerto)).toLowerCase();
  }
  function hostnamePermitido(hostPuerto) {
    var hn = hostnameDe(hostPuerto);
    if (!hn) return false;
    if (hn === 'localhost' || hn === '127.0.0.1' || hn === '::1') return true;
    return hn === String(location.hostname).toLowerCase();
  }
  // M1 (reportes/adversario-final-seguridad.md): "?hub=localhost:8100@evil.example" pasaba el
  // chequeo de arriba porque hostnameDe() corta en el ":" y nunca mira el "@": con el parser WHATWG
  // del WebSocket, esa cadena entera es userinfo y el socket se abre contra evil.example. Se
  // rechaza ACA, antes de mirar el host: cualquier "@ / \ ? #" (los separadores que un atacante usa
  // para esconder un host detrás de uno permitido) o un userinfo que new URL() logre extraer.
  function hubParamSeguro(hostPuerto) {
    if (!hostPuerto) return false;
    if (/[@/\\?#]/.test(hostPuerto)) return false;
    var u;
    try { u = new URL('http://' + hostPuerto); } catch (e) { return false; }
    if (u.username || u.password) return false;
    return hostnamePermitido(hostPuerto);
  }
  var hubIgnoradoPorSeguridad = !!hubParamCrudo && !hubParamSeguro(hubParamCrudo);
  var hubParam = (hubParamCrudo && hubParamSeguro(hubParamCrudo)) ? hubParamCrudo : null;

  // Columnas secundarias (parciales/60s, audio estimado): ocultas por defecto para densidad de
  // control room; ?todas=1 las trae (sistema.md, corrección de dirección bloque 10).
  var mostrarTodas = params.get('todas') === '1';
  if (mostrarTodas) document.documentElement.classList.add('mostrar-todas');

  var PUERTO_DEV_PANEL = '8102';
  var mismoOrigen = !hubParam && location.protocol !== 'file:' && location.port !== PUERTO_DEV_PANEL;
  var httpScheme = (mismoOrigen && location.protocol === 'https:') ? 'https' : 'http';
  var wsScheme = (mismoOrigen && location.protocol === 'https:') ? 'wss' : 'ws';
  var hubHost = hubParam || (mismoOrigen ? location.host : (location.hostname + ':8100'));
  var HTTP_BASE = httpScheme + '://' + hubHost;
  var WS_BASE = wsScheme + '://' + hubHost;

  var TOKEN_KEY = 'panelHubToken'; // sessionStorage; NUNCA query string (skill ui-subtitulos "Token")
  var PRUEBAS_KEY = 'panelMostrarPruebas'; // localStorage (pedido Ricardo: "todo persistido")
  var VOLUMEN_KEY = 'panelVolumen'; // localStorage, default 70

  var POLL_MS = 2000;           // /api/sesiones, historial de reconciliación y /api/metricas
  var TICK_MS = 1000;           // re-render (para que "sin texto hace N s" y parciales/60s avancen)
  var SIN_TEXTO_S = 15;         // umbral documentado (brief B3): live + > 15 s sin `text` => "sin texto"
  var MIN_N_PERCENTIL = 10;     // por debajo: "n insuficiente" (brief B3), nunca se inventa un p95
  var VENTANA_PARCIALES_S = 60; // ventana deslizante de "parciales / 60 s" (no es promedio de sesión)
  var WS_BACKOFF_MAX_MS = 10000;

  // Vista informativa (brief Ricardo, M8 de reportes/adversario-final-ux.md): umbrales propios,
  // documentados también en panel/README.md. NO tocan la vista técnica (SIN_TEXTO_S arriba sigue
  // siendo el umbral de esa tabla).
  var UMBRAL_NUNCA_TEXTO_S = 30;      // M8: sala en vivo que jamás mandó `text` -> alarma a los 30 s
  var UMBRAL_SIN_TEXTO_ALARMA_S = 20; // M8: sala que tenía texto y se calló -> alarma a los 20 s
  var VENTANA_ROTACION_RECIENTE_S = 60; // "se trabó y se reabrió" se muestra 60 s tras la rotación
  var UMBRAL_TRAD_MUESTRAS_MIN = 5;     // bajo esto no se alarma por traducción (ruido con pocas muestras)
  var UMBRAL_TRAD_TASA = 0.15;          // 15 % de fallos en la ventana de 20 traducciones -> alarma
  var VENTANA_TRAD_N = 20;

  // ---------------------------------------------------------------- token (Bearer, client-side, B5)
  function leerToken() {
    try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }
  function guardarToken(valor) {
    try {
      if (valor) sessionStorage.setItem(TOKEN_KEY, valor);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) {
      console.warn('panel: sessionStorage no disponible, el token sólo dura mientras esté escrito en el campo', e);
    }
  }

  // B10 (seguridad): SIEMPRE pedimos GET /api/metricas nosotros mismos con Authorization: Bearer y
  // el token que tipeó el operador (leerToken(), sessionStorage; NUNCA en la query string, skill
  // ui-subtitulos "Token"). Antes, si esta página la servía panel/servir.py (8102), existía un
  // proxy server-side `/panel-api/metricas` que agregaba el HUB_TOKEN real sin que el operador lo
  // tipeara: ELIMINADO (reportes/seguridad.md hallazgo ALTO #1, SSRF + fuga de token vía `?hub=`
  // sin validar). Mismo camino sirva quien sirva el panel.
  function fetchMetricas() {
    var tok = leerToken();
    if (!tok) return Promise.reject(new Error('SIN_TOKEN'));
    return fetch(HTTP_BASE + '/api/metricas', { headers: { Authorization: 'Bearer ' + tok } });
  }

  // ---------------------------------------------------------------- utilidades
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function pad2(n) { return n < 10 ? '0' + n : '' + n; }

  function fmtHoraMs(ms) {
    if (ms == null || !isFinite(ms)) return '—';
    var d = new Date(ms);
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  // Números con toLocaleString del locale (pedido Ricardo: separadores es-AR/en-US según idioma).
  function num(n) {
    return I18N ? I18N.numero(n) : String(n);
  }

  function fmtSeg(x) {
    if (x == null || !isFinite(x)) return '—';
    var texto = I18N ? I18N.numero(x, { minimumFractionDigits: 3, maximumFractionDigits: 3 }) : x.toFixed(3);
    return texto + ' s';
  }

  // nearest-rank: valor en la posición ceil(q/100*n) de la lista YA ordenada (1-indexado).
  // Misma fórmula que panel/recalcular.py y que qa/comun.py:pct (verificado por lectura de código;
  // panel no importa qa/, sólo repite la misma definición estándar de nearest-rank).
  function pct(valoresOrdenados, q) {
    var n = valoresOrdenados.length;
    if (!n) return null;
    var r = Math.max(1, Math.ceil((q / 100) * n));
    return valoresOrdenados[r - 1];
  }

  function resumenLatencia(vals) {
    var v = vals.slice().sort(function (a, b) { return a - b; });
    return { n: v.length, p50: pct(v, 50), p95: pct(v, 95) };
  }

  function fmtResumenLatencia(r, sparkHtml) {
    var spark = sparkHtml || '';
    if (r.n === 0) return '<span class="n-insuf">' + esc(t('sin_muestras')) + '</span>';
    if (r.n < MIN_N_PERCENTIL) {
      return '<span class="lat-par"><span class="n-insuf">' + esc(t('n_insuficiente', { n: num(r.n) })) + '</span>' + spark + '</span>';
    }
    return '<span class="lat-par"><span class="num">p50 ' + fmtSeg(r.p50) + ' · p95 ' + fmtSeg(r.p95) + '</span>' +
           spark + '</span>' +
           '<span class="num-sub">n=' + num(r.n) + '</span>';
  }

  // Innovación (sistema.md §4): sparkline de latencia por sesión, SVG inline sin librerías, con los
  // últimos N valores YA guardados por aplicarMensaje (s.latGrabada / s.latPercibida, orden
  // cronológico). Es un ADORNO adicional: la regla "p50/p95 siempre, nunca promedio" la sigue
  // cumpliendo fmtResumenLatencia arriba; esto no reemplaza esos números, sólo agrega la forma.
  var SPARK_N = 20, SPARK_W = 60, SPARK_H = 16, SPARK_PAD = 2;
  function sparklineSvg(valoresCronologicos) {
    var vals = (valoresCronologicos || []).filter(function (v) { return typeof v === 'number' && isFinite(v); });
    vals = vals.slice(-SPARK_N);
    if (vals.length < 2) return '';
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var rango = (max - min) || 1;
    var pts = vals.map(function (v, i) {
      var x = (i / (vals.length - 1)) * SPARK_W;
      var y = SPARK_H - SPARK_PAD - ((v - min) / rango) * (SPARK_H - SPARK_PAD * 2);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var titulo = t('sparkline_titulo', { n: num(vals.length), min: fmtSeg(min), max: fmtSeg(max) });
    return '<svg class="sparkline" width="' + SPARK_W + '" height="' + SPARK_H + '" viewBox="0 0 ' +
           SPARK_W + ' ' + SPARK_H + '" aria-hidden="true" focusable="false"><title>' + esc(titulo) +
           '</title><polyline points="' + pts + '"></polyline></svg>';
  }

  // ---------------------------------------------------------------- estado en memoria (por sesión)
  var sesiones = {}; // session_id -> S
  var ordenFilas = []; // session_id, orden estable de aparición en la tabla

  function nuevaSesion(id) {
    return {
      id: id,
      lang: null, title: null, replay: null, test: null,
      translationsLangs: [],
      hubState: 'waiting', lastSeqHub: null, lastTEmitHub: null,
      viewers: 0, viewersPorIdioma: {},

      wsEstado: 'conectando', wsBackoff: 1000, wsReconnTimer: null, wsConnId: 0, ws: null,

      seenSeqs: {},          // seq -> true (dedupe ws vs historial de reconciliación)
      maxSeqAplicado: -1,    // cursor para GET historial?desde=

      textos: 0,
      ultimoTextoEn: null,     // ms (desde t_hub) del último type=text aplicado
      latGrabada: [],          // t_emit - t_captured (todas las sesiones; contrato: "latencia del worker")
      latPercibida: [],        // t_receive - t_captured (sólo mensajes EN VIVO de sesiones no-replay)

      rotaciones: 0, rotacionesPorMotivo: {}, ultimaRotacionEn: null, audioLostS: 0,
      ultimaRotacionMotivo: null, ultimaRotacionAudioLostS: 0, // vista informativa: sólo la ÚLTIMA rotación
      watchdogEventos: 0, watchdogReaperturas: 0, ultimoWatchdogEn: null,
      errores: 0, ultimoErrorEn: null, ultimoErrorDetalle: null,
      traduccionesOkFalse: 0, traduccionesOkTrue: 0, ultimaTraduccionEn: null,
      traduccionesVentana: [],  // últimos VENTANA_TRAD_N booleanos `ok`, para "traducción fallando (F de N)"
      parcialesTs: [],         // ms de cada `partial` visto en vivo (ventana deslizante)

      primeraVezLive: null,       // Date.now() de cuando esta sesión se vio `live` por primera vez (M8)
      alarmaSonadaNunca: false,   // edge-trigger del aviso de voz "nunca produjo texto"
      alarmaSonadaSilencio: false,// edge-trigger del aviso de voz "sin texto hace más de N s"

      metricas: null,          // último objeto de /panel-api/metricas para esta sesión (o null)
    };
  }

  function sesionDe(id) {
    var s = sesiones[id];
    if (!s) {
      s = sesiones[id] = nuevaSesion(id);
      ordenFilas.push(id);
      conectarSesion(s);
    }
    return s;
  }

  // ---------------------------------------------------------------- aplicar mensajes (ws o historial)
  // Un único camino para no duplicar reglas: la reconciliación por HTTP y el WS en vivo llaman a la
  // misma función. `origen` sólo cambia si se suma latencia PERCIBIDA (eso exige t_receive real).
  function aplicarMensaje(s, msg, origen) {
    if (!msg || typeof msg !== 'object') return;
    var seq = msg.seq;
    if (seq !== null && seq !== undefined) {
      if (s.seenSeqs[seq]) return; // ya contado (ws y reconciliación se solapan a propósito)
      s.seenSeqs[seq] = true;
      if (seq > s.maxSeqAplicado) s.maxSeqAplicado = seq;
    }
    if (msg.replay !== undefined && msg.replay !== null) s.replay = msg.replay;
    if (msg.lang) s.lang = s.lang || msg.lang;
    var tHubMs = (typeof msg.t_hub === 'number') ? msg.t_hub * 1000 : Date.now();

    switch (msg.type) {
      case 'text':
        s.textos += 1;
        s.ultimoTextoEn = tHubMs;
        if (typeof msg.t_emit === 'number' && typeof msg.t_captured === 'number') {
          s.latGrabada.push(msg.t_emit - msg.t_captured);
          if (origen === 'ws' && msg.replay === false) {
            s.latPercibida.push((Date.now() / 1000) - msg.t_captured);
          }
        }
        break;
      case 'rotation': {
        s.rotaciones += 1;
        s.ultimaRotacionEn = tHubMs;
        var motivo = (msg.meta && msg.meta.reason) || 'sin motivo';
        s.rotacionesPorMotivo[motivo] = (s.rotacionesPorMotivo[motivo] || 0) + 1;
        s.ultimaRotacionMotivo = motivo; // vista informativa: sólo la de ESTA rotación, no acumulado
        s.ultimaRotacionAudioLostS = (msg.meta && typeof msg.meta.audio_lost_s === 'number') ? msg.meta.audio_lost_s : 0;
        // B5: rotation.meta.audio_lost_s (audio que la conexión vieja no cubrió y la nueva no
        // reenvió del todo; sólo motivos atasco/cierre lo traen > 0, ver worker/session.py). Se
        // ACUMULA por sesión, no se reemplaza: cada rotación puede sumar audio perdido distinto.
        if (msg.meta && typeof msg.meta.audio_lost_s === 'number') {
          s.audioLostS += msg.meta.audio_lost_s;
        }
        break;
      }
      case 'watchdog':
        s.watchdogEventos += 1;
        s.ultimoWatchdogEn = tHubMs;
        if (msg.meta && msg.meta.reopened) s.watchdogReaperturas += 1;
        break;
      case 'error':
        s.errores += 1;
        s.ultimoErrorEn = tHubMs;
        if (msg.meta) s.ultimoErrorDetalle = (msg.meta.code || '?') + (msg.meta.message ? ': ' + msg.meta.message : '');
        break;
      case 'session_start':
        if (msg.meta && msg.meta.title) s.title = msg.meta.title;
        break;
      default:
        break; // session_end: el estado autoritativo viene de /api/sesiones (state=ended)
    }
  }

  function aplicarTraduccion(s, msg) {
    var items = Array.isArray(msg.items) ? msg.items : [];
    if (!items.length) return;
    var tHubMs = (typeof msg.t_hub === 'number') ? msg.t_hub * 1000 : Date.now();
    for (var i = 0; i < items.length; i++) {
      if (!items[i]) continue;
      if (items[i].ok === false) s.traduccionesOkFalse += 1;
      else if (items[i].ok === true) s.traduccionesOkTrue += 1;
      if (items[i].ok === true || items[i].ok === false) {
        // vista informativa: ventana de las últimas VENTANA_TRAD_N para "traducción fallando (F de N)"
        s.traduccionesVentana.push(items[i].ok === true);
        if (s.traduccionesVentana.length > VENTANA_TRAD_N) s.traduccionesVentana.shift();
      }
    }
    s.ultimaTraduccionEn = tHubMs;
  }

  function aplicarParcial(s) {
    s.parcialesTs.push(Date.now());
  }

  // ---------------------------------------------------------------- WS propio por sesión (audiencia pública)
  function conectarSesion(s) {
    s.wsConnId += 1;
    var miConn = s.wsConnId;
    s.wsEstado = 'conectando';
    var url = WS_BASE + '/ws/' + encodeURIComponent(s.id);
    var socket;
    try {
      socket = new WebSocket(url);
    } catch (e) {
      s.wsEstado = 'error: ' + e;
      programarReconexionWs(s);
      return;
    }
    s.ws = socket;

    socket.addEventListener('open', function () {
      if (miConn !== s.wsConnId) return;
      s.wsBackoff = 1000;
      s.wsEstado = 'en vivo';
    });

    socket.addEventListener('message', function (evt) {
      if (miConn !== s.wsConnId) return;
      var msg;
      try { msg = JSON.parse(evt.data); } catch (e) {
        console.error('panel: mensaje no-JSON del hub para', s.id, e);
        return;
      }
      if (msg.type === 'init') {
        if (msg.title) s.title = msg.title;
        if (msg.session_lang) s.lang = msg.session_lang;
        if (msg.replay !== undefined && msg.replay !== null) s.replay = msg.replay;
        if (msg.test !== undefined && msg.test !== null) s.test = msg.test === true; // B4/B5
        if (Array.isArray(msg.translations_langs)) s.translationsLangs = msg.translations_langs;
        (msg.lines || []).forEach(function (linea) { aplicarMensaje(s, linea, 'historial'); });
        return;
      }
      if (msg.type === 'heartbeat') return; // el heartbeat de AUDIENCIA no trae meta; nada que sumar
      if (msg.type === 'partial') { aplicarParcial(s); return; }
      if (msg.type === 'translation') { aplicarTraduccion(s, msg); return; }
      aplicarMensaje(s, msg, 'ws');
    });

    socket.addEventListener('close', function () {
      if (miConn !== s.wsConnId) return;
      s.wsEstado = 'reconectando';
      programarReconexionWs(s);
    });

    socket.addEventListener('error', function () {
      if (miConn !== s.wsConnId) return;
      // 'close' se dispara después de 'error': el reintento se agenda una sola vez desde 'close'
      // (mismo patrón que web/app.js).
    });
  }

  function programarReconexionWs(s) {
    if (s.wsReconnTimer) return;
    s.wsReconnTimer = setTimeout(function () {
      s.wsReconnTimer = null;
      conectarSesion(s);
    }, s.wsBackoff);
    s.wsBackoff = Math.min(s.wsBackoff * 2, WS_BACKOFF_MAX_MS);
  }

  // ---------------------------------------------------------------- reconciliación por HTTP (backfill)
  function reconciliarHistorial(s) {
    var url = HTTP_BASE + '/api/sesiones/' + encodeURIComponent(s.id) + '/historial?desde=' +
              s.maxSeqAplicado + '&tipos=todos';
    fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (lista) {
      lista.forEach(function (m) { aplicarMensaje(s, m, 'historial'); });
    }).catch(function (e) {
      console.warn('panel: no se pudo reconciliar historial de', s.id, String(e));
    });
  }

  // ---------------------------------------------------------------- /api/sesiones (2 s)
  var elChipHub = document.getElementById('chip-hub');
  var elChipMetricas = document.getElementById('chip-metricas');
  var elUltimaActualizacion = document.getElementById('ultima-actualizacion');
  var elAviso = document.getElementById('aviso-hub');
  var elPieHubUrl = document.getElementById('pie-hub-url');
  elPieHubUrl.textContent = HTTP_BASE + ' (WS ' + WS_BASE + ')';

  // B10 (seguridad): aviso PERSISTENTE en la cabecera (con texto, no sólo color) cuando `?hub=` se
  // ignoró; no usa marcarAviso() porque ese banner se limpia solo en cada poll exitoso y este aviso
  // tiene que seguir visible mientras la URL siga teniendo el `?hub=` rechazado.
  var elChipConexion = document.getElementById('chip-conexion');
  var elChipHubSeguridad = document.getElementById('chip-hub-seguridad');
  function actualizarChipSeguridad() {
    if (!elChipHubSeguridad) return;
    if (hubIgnoradoPorSeguridad) {
      elChipHubSeguridad.textContent = t('chip_hub_seguridad', { valor: hubParamCrudo });
      elChipHubSeguridad.hidden = false;
    } else {
      elChipHubSeguridad.hidden = true;
    }
  }

  var metricasGlobal = null;
  var ultimoErrorSesiones = null; // { detalle } — para poder re-renderizar el aviso al cambiar idioma

  function marcarAviso(msg) {
    if (!msg) { elAviso.className = 'aviso'; elAviso.textContent = ''; return; }
    elAviso.className = 'aviso visible';
    elAviso.textContent = msg;
  }

  function pollSesiones() {
    fetch(HTTP_BASE + '/api/sesiones').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (lista) {
      ultimoErrorSesiones = null;
      elChipHub.textContent = t('chip_hub_ok', { n: num(lista.length) });
      elChipHub.className = 'chip chip--ok';
      marcarAviso(null);
      lista.forEach(function (resumen) {
        var s = sesionDe(resumen.session_id);
        s.lang = resumen.lang;
        if (resumen.title) s.title = resumen.title;
        if (resumen.replay !== undefined && resumen.replay !== null) s.replay = resumen.replay;
        s.hubState = resumen.state;
        // M8: hora en que ESTE panel vio la sesión `live` por primera vez (aproximación por poll de
        // 2 s, no el t_emit real de session_start; suficiente para el umbral de 30 s de la vista
        // informativa). No se reinicia si el estado oscila entre polls.
        if (s.hubState === 'live' && s.primeraVezLive == null) s.primeraVezLive = Date.now();
        s.lastSeqHub = resumen.last_seq;
        s.lastTEmitHub = resumen.last_t_emit;
        s.viewers = resumen.viewers || 0;
        s.viewersPorIdioma = resumen.viewers_por_idioma || {};
        s.translationsLangs = resumen.translations_langs || [];
        reconciliarHistorial(s);
      });
      elUltimaActualizacion.textContent = t('actualizado', { hora: fmtHoraMs(Date.now()) });
      render();
    }).catch(function (e) {
      elChipHub.textContent = t('chip_hub_sin_respuesta');
      elChipHub.className = 'chip chip--mal';
      ultimoErrorSesiones = { detalle: String(e) };
      marcarAviso(t('aviso_sin_respuesta', { url: HTTP_BASE + '/api/sesiones', detalle: String(e) }));
    });

    fetchMetricas().then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (m) {
      metricasGlobal = m;
      elChipMetricas.textContent = t('config_metricas_con_token');
      elChipMetricas.className = 'drawer__estado drawer__estado--ok';
      Object.keys(sesiones).forEach(function (id) {
        sesiones[id].metricas = (m.sesiones && m.sesiones[id]) || null;
      });
    }).catch(function (e) {
      if (e && e.message === 'SIN_TOKEN') {
        metricasGlobal = null;
        elChipMetricas.textContent = t('config_metricas_sin_token');
        elChipMetricas.className = 'drawer__estado';
        return;
      }
      elChipMetricas.textContent = t('config_metricas_error', { detalle: (e && e.message) || e });
      elChipMetricas.className = 'drawer__estado drawer__estado--mal';
      console.warn('panel: /api/metricas falló:', String(e));
    });
  }

  // ---------------------------------------------------------------- render
  function estadoDe(s, ahoraMs) {
    var base = s.hubState || 'waiting';
    if (base === 'ended') return { clase: 'ended', texto: t('estado_terminada') };
    if (base === 'idle') return { clase: 'idle', texto: t('estado_inactiva') };
    if (base === 'waiting') return { clase: 'waiting', texto: t('estado_esperando') };
    // live:
    if (s.ultimoTextoEn == null) return { clase: 'live', texto: t('estado_en_vivo_sin_texto') };
    var silencioS = (ahoraMs - s.ultimoTextoEn) / 1000;
    if (silencioS > SIN_TEXTO_S) {
      return { clase: 'mudo', texto: t('estado_mudo', { s: num(Math.floor(silencioS)) }) };
    }
    return { clase: 'live', texto: t('estado_en_vivo') };
  }

  // Franja de color por motivo de rotación (sistema.md §2 ref. Master Control Room: color como
  // clasificador, siempre con texto al lado — nunca el punto solo). Motivos sin mapeo (cualquier
  // string que mande el worker) caen en el modificador neutro "otro", no se pierden. Los nombres de
  // motivo (cierre/atasco/preventiva/goaway) son valores LITERALES del contrato: no se traducen,
  // igual que `ok:false` o `type=text` (panel/README.md).
  var MOTIVO_CLASE = { cierre: 'motivo--cierre', atasco: 'motivo--atasco',
                        preventiva: 'motivo--preventiva', goaway: 'motivo--goaway' };
  function fmtDetalleMotivos(mapa) {
    var claves = Object.keys(mapa);
    if (!claves.length) return '';
    return claves.sort().map(function (k) {
      var clase = MOTIVO_CLASE[k] || 'motivo--otro';
      return '<span class="motivo ' + clase + '">' + esc(k) + ':' + num(mapa[k]) + '</span>';
    }).join(' ');
  }

  // B8: audio_lost_s acumulado (B5 ya lo sumaba en aplicarMensaje, pero no se mostraba en ninguna
  // celda: brief B8 punto 3, "verificá la fila"). Va junto al detalle de Rotaciones.
  function fmtDetalleRotacion(s) {
    var motivos = fmtDetalleMotivos(s.rotacionesPorMotivo);
    if (!(s.audioLostS > 0)) return motivos;
    var perdido = esc(t('audio_perdido', { s: fmtSeg(s.audioLostS) }));
    return motivos ? motivos + ' · ' + perdido : perdido;
  }

  // ---------------------------------------------------------------- vista INFORMATIVA (Ricardo, R8e)
  // Pedido directo (no técnico): switch arriba con dos vistas; la informativa es una tarjeta por
  // sala, ORDENADA POR URGENCIA, en lenguaje llano (nada de t_emit/t_captured/seq/ok:false: eso
  // sigue en la vista técnica de abajo, sin tocar). Reglas de urgencia documentadas en
  // panel/README.md — ese archivo es la fuente de verdad de los umbrales; si cambian, cambian ahí y
  // acá a la vez.
  function nombreIdioma(codigo) {
    return I18N ? I18N.nombreIdioma(codigo) : (codigo || '—');
  }
  function fmtIdiomaTraduccion(s) {
    var origen = nombreIdioma(s.lang);
    var destinos = (s.translationsLangs || []).filter(function (l) { return l && l !== s.lang; });
    if (!destinos.length) return origen;
    return origen + ' → ' + destinos.map(nombreIdioma).join(', ');
  }

  // Aviso hablado (Web Speech API): sólo para las dos alarmas de M8 (reportes/adversario-final-ux.md),
  // con corte por edge (dispararAlarma) para que suene UNA vez por episodio, no en cada re-render de
  // 1 s. Silenciable desde Configuración → Avisos (persistido, ver más abajo); si el navegador no
  // tiene speechSynthesis, se degrada en silencio (la tarjeta ya avisa por texto y color, nunca
  // depende sólo del audio). Usa la voz (`.lang`) y el volumen (0–100 %, default 70 %) del idioma
  // elegido en el selector ES/EN.
  function avisoDeVoz(texto) {
    if (!elToggleSonido || !elToggleSonido.checked) return;
    if (!('speechSynthesis' in window)) return;
    try {
      var u = new SpeechSynthesisUtterance(texto);
      u.lang = I18N ? I18N.vozLang() : 'es-AR';
      u.volume = leerVolumen() / 100;
      window.speechSynthesis.speak(u);
    } catch (e) {
      console.warn('panel: no se pudo reproducir el aviso de voz', e);
    }
  }
  function dispararAlarma(s, campo, mensaje) {
    if (s[campo]) return;
    s[campo] = true;
    avisoDeVoz(mensaje);
  }

  // Orden de urgencia (menor = más urgente; usado para ordenar las tarjetas y para "N a atender"):
  //   0 sin texto (nunca o silencio largo) · 1 se trabó y se reabrió · 2 traducción fallando ·
  //   3 reconectando · 4 sana (Al día / Replay) · 5 inactiva · 6 terminada.
  function estadoInformativo(s, ahoraMs) {
    if (s.hubState === 'ended') {
      return { urgencia: 6, clase: 'terminada', icono: '■', texto: t('estado_terminada'), accion: null };
    }
    var nombreSala = s.title || s.id;
    // Ojo con el orden: las alarmas de "sin texto" van ANTES del chequeo de `idle` a propósito. Al
    // cortar el worker de una sesión, el hub la marca `idle` (no manda más `type=text`, pierde la
    // conexión del productor) MUCHO antes de los 20/30 s de estos umbrales: si `idle` se mirara
    // primero, la tarjeta diría "Inactiva" (sin urgencia) justo en el caso que hay que atender.
    // Verificado a mano: `reportes/monitor-final.md` ("cortar el worker").
    var segsDesdeLive = s.primeraVezLive != null ? Math.floor((ahoraMs - s.primeraVezLive) / 1000) : null;

    if (s.ultimoTextoEn == null && segsDesdeLive != null && segsDesdeLive > UMBRAL_NUNCA_TEXTO_S) {
      dispararAlarma(s, 'alarmaSonadaNunca', t('voz_nunca_texto', { sala: nombreSala }));
      return {
        urgencia: 0, clase: 'mudo', icono: '◐',
        texto: t('estado_sin_texto_nunca', { s: num(segsDesdeLive) }),
        accion: t('accion_sin_texto_nunca')
      };
    }

    var silencioS = s.ultimoTextoEn != null ? Math.floor((ahoraMs - s.ultimoTextoEn) / 1000) : null;
    if (silencioS != null && silencioS > UMBRAL_SIN_TEXTO_ALARMA_S) {
      dispararAlarma(s, 'alarmaSonadaSilencio', t('voz_silencio', { sala: nombreSala, s: UMBRAL_SIN_TEXTO_ALARMA_S }));
      return {
        urgencia: 0, clase: 'mudo', icono: '◐',
        texto: t('estado_sin_texto_alarma', { s: num(silencioS) }),
        accion: t('accion_sin_texto_alarma')
      };
    }
    if (silencioS == null || silencioS <= UMBRAL_SIN_TEXTO_ALARMA_S) s.alarmaSonadaSilencio = false; // rearma para el próximo corte

    if (s.hubState === 'idle') {
      return { urgencia: 5, clase: 'inactiva', icono: '◌', texto: t('estado_inactiva'), accion: null };
    }

    var rotacionReciente = s.ultimaRotacionEn != null &&
      ((ahoraMs - s.ultimaRotacionEn) / 1000) <= VENTANA_ROTACION_RECIENTE_S &&
      (s.ultimaRotacionMotivo === 'atasco' || s.ultimaRotacionMotivo === 'cierre');
    if (rotacionReciente) {
      var perdido = s.ultimaRotacionAudioLostS > 0 ? t('estado_rotacion_perdido', { n: num(Math.round(s.ultimaRotacionAudioLostS)) }) : '';
      var accionRotacion = s.ultimaRotacionMotivo === 'atasco' ? t('accion_rotacion_atasco') : t('accion_rotacion_cierre');
      return { urgencia: 1, clase: 'rotacion', icono: '↺', texto: t('estado_rotacion', { perdido: perdido }), accion: accionRotacion };
    }

    var ventana = s.traduccionesVentana || [];
    var totalVentana = ventana.length;
    var fallos = ventana.reduce(function (acc, ok) { return acc + (ok === false ? 1 : 0); }, 0);
    var tasaFallo = totalVentana ? (fallos / totalVentana) : 0;
    if (totalVentana >= UMBRAL_TRAD_MUESTRAS_MIN && tasaFallo >= UMBRAL_TRAD_TASA) {
      return {
        urgencia: 2, clase: 'traduccion', icono: '▲',
        texto: t('estado_traduccion', { f: num(fallos), n: num(totalVentana) }),
        accion: t('accion_traduccion')
      };
    }

    if (s.wsEstado === 'reconectando') {
      return {
        urgencia: 3, clase: 'reconectando', icono: '◌', texto: t('estado_reconectando'),
        accion: t('accion_reconectando')
      };
    }

    if (s.replay === true) return { urgencia: 4, clase: 'sana', icono: '⟲', texto: t('estado_replay'), accion: null };
    return { urgencia: 4, clase: 'sana', icono: '●', texto: t('estado_al_dia'), accion: null };
  }

  function tarjetaHtml(s, estado) {
    var badgeReplay = s.replay === true ? '<span class="badge badge-replay">REPLAY</span>' :
                       (s.replay === false ? '<span class="badge badge-vivo">LIVE</span>' : '');
    var nombre = esc(s.title || s.id);
    var accionHtml = estado.accion ? '<p class="tarjeta__accion"><b>' + esc(t('accion_prefijo')) + '</b> ' + esc(estado.accion) + '</p>' : '';
    var espectadores = s.viewers || 0;
    var textoEspectadores = espectadores === 1 ? t('espectador_uno', { n: num(espectadores) }) : t('espectador_varios', { n: num(espectadores) });
    // Addendum Ricardo ("escuchar el original"): botón chico sólo si el drawer de Salas conoce esta
    // sesión y su fuente es un clip (salasEstado.porId, sección "control de salas" más abajo en este
    // mismo archivo — hoisted, se resuelve en tiempo de ejecución, no de parseo).
    var infoControl = (typeof salasEstado !== 'undefined' && salasEstado.porId) ? salasEstado.porId[s.id] : null;
    var escucharHtml = (infoControl && infoControl.fuente && infoControl.fuente.tipo === 'archivo')
      ? '<button type="button" class="tarjeta__escuchar" data-accion="escuchar-tarjeta" data-id="' + esc(s.id) + '">' + esc(t('salas_escuchar')) + '</button>'
      : '';
    return (
      '<article class="tarjeta tarjeta--' + estado.clase + '" data-estado="' + estado.clase + '" data-id="' + esc(s.id) + '">' +
        '<header class="tarjeta__cabecera">' +
          '<h2 class="tarjeta__nombre">' + nombre + '</h2>' +
          badgeReplay +
        '</header>' +
        '<p class="tarjeta__idioma">' + esc(fmtIdiomaTraduccion(s)) + '</p>' +
        '<p class="tarjeta__estado"><span class="tarjeta__icono" aria-hidden="true">' + estado.icono + '</span>' +
          esc(estado.texto) + '</p>' +
        accionHtml +
        '<p class="tarjeta__pie">' + esc(textoEspectadores) + escucharHtml + '</p>' +
      '</article>'
    );
  }

  var elTarjetas = document.getElementById('tarjetas');
  var elTarjetasVacio = document.getElementById('tarjetas-vacio');
  var elResumenEnVivo = document.getElementById('resumen-en-vivo');
  var elResumenAtender = document.getElementById('resumen-atender');
  var elResumenSanas = document.getElementById('resumen-sanas');
  var elResumenHora = document.getElementById('resumen-hora');
  var elToggleSonido = document.getElementById('toggle-sonido');

  function renderInformativa(ahoraMs) {
    if (!elTarjetas) return;
    var items = ordenFilas.map(function (id) {
      var s = sesiones[id];
      return { s: s, estado: estadoInformativo(s, ahoraMs) };
    });
    items.sort(function (a, b) { return a.estado.urgencia - b.estado.urgencia; });

    var enVivoN = 0, replayN = 0, atenderN = 0, sanasN = 0, ultimoDatoMs = null;
    items.forEach(function (it) {
      if (it.s.hubState === 'live') { if (it.s.replay) replayN += 1; else enVivoN += 1; }
      if (it.estado.urgencia <= 3) atenderN += 1;
      if (it.estado.urgencia === 4) sanasN += 1;
      [it.s.ultimoTextoEn, (it.s.lastTEmitHub != null ? it.s.lastTEmitHub * 1000 : null)].forEach(function (t) {
        if (t != null && (ultimoDatoMs == null || t > ultimoDatoMs)) ultimoDatoMs = t;
      });
    });

    if (elResumenEnVivo) elResumenEnVivo.textContent = num(enVivoN);
    var elResumenReplay = document.getElementById('resumen-replay');
    if (elResumenReplay) {
      elResumenReplay.textContent = num(replayN);
      elResumenReplay.parentNode.hidden = replayN === 0;
    }
    if (elResumenAtender) { elResumenAtender.textContent = num(atenderN); elResumenAtender.classList.toggle('hay', atenderN > 0); }
    if (elResumenSanas) elResumenSanas.textContent = num(sanasN);
    if (elResumenHora) elResumenHora.textContent = ultimoDatoMs != null ? fmtHoraMs(ultimoDatoMs) : '—';
    if (elTarjetasVacio) elTarjetasVacio.hidden = items.length > 0;

    elTarjetas.innerHTML = items.map(function (it) { return tarjetaHtml(it.s, it.estado); }).join('');
  }

  function fmtContador(n, ultimoMs, detalle) {
    var html = '<span class="contador' + (n === 0 ? ' contador-cero' : '') + '">' + num(n) + '</span>';
    if (ultimoMs != null) html += '<span class="contador-detalle">' + esc(t('ultimo_prefijo', { hora: fmtHoraMs(ultimoMs) })) + '</span>';
    if (detalle) html += '<span class="contador-detalle">' + detalle + '</span>';
    return html;
  }

  function fmtParcialesPorMinuto(s, ahoraMs) {
    var corte = ahoraMs - VENTANA_PARCIALES_S * 1000;
    s.parcialesTs = s.parcialesTs.filter(function (t) { return t >= corte; });
    return s.parcialesTs.length;
  }

  function fmtAudioEstimado(s) {
    var m = s.metricas;
    if (!m || !m.latido_worker || !m.latido_worker.meta ||
        typeof m.latido_worker.meta.audio_seconds_sent !== 'number') {
      return '<span class="n-insuf">' + esc(t('audio_sin_heartbeat')) + '</span>';
    }
    var seg = m.latido_worker.meta.audio_seconds_sent;
    var hace = m.latido_worker.t_hub ? ((Date.now() / 1000) - m.latido_worker.t_hub) : null;
    var segTexto = I18N ? I18N.numero(seg, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : seg.toFixed(1);
    var html = '<span class="num">' + segTexto + ' s <i>' + esc(t('audio_estimacion')) + '</i></span>';
    if (hace != null) html += '<span class="num-sub">' + esc(t('audio_latido_hace', { n: num(Math.round(hace)) })) + '</span>';
    return html;
  }

  function filaHtml(s, ahoraMs, estado) {
    var lang = s.lang || '—';
    var badgeReplay = s.replay === true ? '<span class="badge badge-replay">REPLAY</span>' :
                       (s.replay === false ? '<span class="badge badge-vivo">LIVE</span>' : '');
    var badgeTest = s.test === true ? '<span class="badge badge-test">TEST</span>' : '';
    var destinos = s.translationsLangs && s.translationsLangs.length ?
      '<span class="num-sub">' + esc(t('trad_prefijo')) + ' ' + esc(s.translationsLangs.join(', ')) + '</span>' : '';

    var etiquetaLatWorker = s.replay === true ? t('lat_grabada_sub') : t('lat_worker_sub');
    var latWorkerHtml = '<span class="num-sub">' + esc(etiquetaLatWorker) + '</span>' +
                        fmtResumenLatencia(resumenLatencia(s.latGrabada), sparklineSvg(s.latGrabada));
    var latPercibidaHtml;
    if (s.replay === true) {
      latPercibidaHtml = '<span class="n-insuf">' + esc(t('lat_no_aplica_replay')) + '</span>';
    } else {
      latPercibidaHtml = '<span class="num-sub">' + esc(t('lat_percibida_sub')) + '</span>' +
                          fmtResumenLatencia(resumenLatencia(s.latPercibida), sparklineSvg(s.latPercibida));
    }

    var viewersHtml = num(s.viewers || 0);
    var vpi = Object.keys(s.viewersPorIdioma || {});
    if (vpi.length) {
      viewersHtml += '<span class="num-sub">' +
        vpi.sort().map(function (k) { return esc(k) + ':' + num(s.viewersPorIdioma[k]); }).join(' · ') +
        '</span>';
    }

    return (
      '<td class="col-sesion"><span class="sesion-id">' + esc(s.id) + '</span>' +
        (s.title ? '<span class="sesion-titulo">' + esc(s.title) + '</span>' : '') + '</td>' +
      '<td>' + esc(lang) + ' ' + badgeReplay + badgeTest + destinos + '</td>' +
      '<td class="estado estado--' + estado.clase + '">' + esc(estado.texto) + '</td>' +
      '<td class="mono">' + (s.lastSeqHub == null ? '—' : num(s.lastSeqHub)) +
        '<span class="num-sub">' + (s.lastTEmitHub ? fmtHoraMs(s.lastTEmitHub * 1000) : '—') + '</span></td>' +
      '<td class="mono">' + num(s.textos) + '</td>' +
      '<td>' + latWorkerHtml + '</td>' +
      '<td>' + latPercibidaHtml + '</td>' +
      '<td>' + fmtContador(s.rotaciones, s.ultimaRotacionEn, fmtDetalleRotacion(s)) + '</td>' +
      '<td>' + fmtContador(s.watchdogReaperturas, s.ultimoWatchdogEn,
                 s.watchdogEventos !== s.watchdogReaperturas ? esc(t('watchdog_eventos_total', { n: num(s.watchdogEventos) })) : '') + '</td>' +
      '<td>' + fmtContador(s.errores, s.ultimoErrorEn, s.ultimoErrorDetalle ? esc(s.ultimoErrorDetalle) : '') + '</td>' +
      '<td>' + fmtContador(s.traduccionesOkFalse, s.ultimaTraduccionEn,
                 s.traduccionesOkTrue ? esc(t('ok_true_igual', { n: num(s.traduccionesOkTrue) })) : '') + '</td>' +
      '<td class="mono col-secundaria">' + num(fmtParcialesPorMinuto(s, ahoraMs)) + '</td>' +
      '<td class="col-secundaria">' + fmtAudioEstimado(s) + '</td>' +
      '<td class="mono">' + viewersHtml + '</td>'
    );
  }

  var elCuerpo = document.getElementById('cuerpo-tabla');

  function render() {
    var ahoraMs = Date.now();
    renderInformativa(ahoraMs);
    var filaVacia = document.getElementById('fila-vacia');
    if (ordenFilas.length && filaVacia) filaVacia.remove();
    var totalTest = 0;
    ordenFilas.forEach(function (id) {
      var s = sesiones[id];
      var tr = document.getElementById('fila-' + cssEscape(id));
      if (!tr) {
        tr = document.createElement('tr');
        tr.id = 'fila-' + cssEscape(id);
        elCuerpo.appendChild(tr);
      }
      var estado = estadoDe(s, ahoraMs);
      tr.innerHTML = filaHtml(s, ahoraMs, estado);
      // sistema.md §2 ref. Master Control Room + Manufacturing Dashboard: franja de color en el
      // borde izquierdo de la fila segun estado (nunca la fila entera pintada; el texto del estado
      // sigue siendo la fuente, esto es sólo agrupación visual rápida), vía CSS con data-estado.
      tr.dataset.estado = estado.clase;
      // B8: sesiones test:true (brief B5/B8, skill ui-subtitulos): ocultas por defecto, el
      // toggle "mostrar pruebas" las trae de vuelta; nunca se pierden, sólo se ocultan (CSS).
      if (s.test === true) {
        totalTest += 1;
        tr.classList.toggle('fila-oculta', !mostrarPruebas);
      } else {
        tr.classList.remove('fila-oculta');
      }
    });
    if (elChipPruebasOcultas) {
      if (totalTest === 0) {
        elChipPruebasOcultas.textContent = t('pruebas_sin');
      } else {
        var etiqueta = totalTest === 1 ? t('pruebas_singular') : t('pruebas_plural');
        var sufijo = mostrarPruebas ? t('pruebas_mostradas') : t('pruebas_ocultas');
        elChipPruebasOcultas.textContent = num(totalTest) + ' ' + etiqueta + ' ' + sufijo;
      }
    }
    // Corrección de dirección (bloque 10): la conexión WS del PANEL a cada sesión es un dato del
    // PANEL, no un estado de la sesión (mostrarlo por fila confundía "terminada" con "en vivo" en
    // la misma línea). Se agrega UNA vez en la barra de estado, agregado sobre todas las filas.
    if (elChipConexion) {
      var totalFilas = ordenFilas.length;
      var enVivo = ordenFilas.reduce(function (acc, id) {
        return acc + (sesiones[id].wsEstado === 'en vivo' ? 1 : 0);
      }, 0);
      elChipConexion.textContent = t('chip_conexion', { n: num(enVivo), total: num(totalFilas) });
    }
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) { return '_' + c.charCodeAt(0) + '_'; });
  }

  // ---------------------------------------------------------------- controles (token, toggle pruebas)
  // B8: el campo de token (index.html #input-token) y los botones existían en el HTML pero no
  // tenían ningún listener en app.js: el Bearer nunca se mandaba a /api/metricas cuando el hub
  // sirve el panel directo (sin el proxy de servir.py). Ver fetchMetricas() más arriba.
  var elInputToken = document.getElementById('input-token');
  var elBtnTokenGuardar = document.getElementById('btn-token-guardar');
  var elBtnTokenBorrar = document.getElementById('btn-token-borrar');
  var elChipPruebasOcultas = document.getElementById('chip-pruebas-ocultas');
  var elTogglePruebas = document.getElementById('toggle-pruebas');

  // Config/idioma: "mostrar pruebas" ahora persiste en localStorage (pedido: "Todo persistido en
  // localStorage", el token es la única excepción y sigue en sessionStorage).
  function leerMostrarPruebasGuardado() {
    try { return localStorage.getItem(PRUEBAS_KEY) === '1'; } catch (e) { return false; }
  }
  function guardarMostrarPruebas(v) {
    try { localStorage.setItem(PRUEBAS_KEY, v ? '1' : '0'); } catch (e) { /* no fatal */ }
  }
  var mostrarPruebas = leerMostrarPruebasGuardado();
  if (elTogglePruebas) elTogglePruebas.checked = mostrarPruebas;

  if (elInputToken) elInputToken.value = leerToken();
  if (elBtnTokenGuardar) elBtnTokenGuardar.addEventListener('click', function () {
    guardarToken(elInputToken ? elInputToken.value.trim() : '');
    pollSesiones(); // aplica ya, sin esperar los 2 s del intervalo
  });
  if (elInputToken) elInputToken.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && elBtnTokenGuardar) elBtnTokenGuardar.click();
  });
  if (elBtnTokenBorrar) elBtnTokenBorrar.addEventListener('click', function () {
    if (elInputToken) elInputToken.value = '';
    guardarToken('');
    metricasGlobal = null;
    Object.keys(sesiones).forEach(function (id) { sesiones[id].metricas = null; });
    elChipMetricas.textContent = t('config_metricas_sin_token');
    elChipMetricas.className = 'drawer__estado';
    render();
  });
  if (elTogglePruebas) elTogglePruebas.addEventListener('change', function () {
    mostrarPruebas = elTogglePruebas.checked;
    guardarMostrarPruebas(mostrarPruebas);
    render();
  });

  // ---------------------------------------------------------------- switch Informativa / Técnica
  // Pedido directo de Ricardo: dos vistas, informativa por defecto, persistida en localStorage (no
  // sessionStorage: si cierra la pestaña y vuelve, que quede en la que dejó). La vista técnica es la
  // tabla de siempre, sin cambios de fondo; sólo se le suma/saca una clase para mostrarla u ocultarla.
  var VISTA_KEY = 'panelVista';
  var elBtnVistaInformativa = document.getElementById('btn-vista-informativa');
  var elBtnVistaTecnica = document.getElementById('btn-vista-tecnica');
  var elBtnAyudaInformativa = document.getElementById('btn-ayuda-informativa');
  var elBtnAyudaTecnica = document.getElementById('btn-ayuda-tecnica');
  function aplicarVista(vista) {
    var v = vista === 'tecnica' ? 'tecnica' : 'informativa';
    document.body.setAttribute('data-vista', v);
    if (elBtnVistaInformativa) elBtnVistaInformativa.setAttribute('aria-selected', String(v === 'informativa'));
    if (elBtnVistaTecnica) elBtnVistaTecnica.setAttribute('aria-selected', String(v === 'tecnica'));
    // Sólo una "?" a la vez: la de jerga técnica en Técnica, la de lenguaje llano en Informativa.
    // Si el popover de la "?" que se oculta estaba abierto, se cierra (su botón ya no es visible).
    if (elBtnAyudaInformativa) elBtnAyudaInformativa.hidden = v !== 'informativa';
    if (elBtnAyudaTecnica) elBtnAyudaTecnica.hidden = v !== 'tecnica';
    if (v !== 'informativa' && ctrlAyudaInformativa) ctrlAyudaInformativa.cerrar(false);
    if (v !== 'tecnica' && ctrlAyudaTecnica) ctrlAyudaTecnica.cerrar(false);
    try { localStorage.setItem(VISTA_KEY, v); } catch (e) { /* localStorage no disponible: la vista no persiste, no es fatal */ }
  }
  var vistaGuardada = (function () { try { return localStorage.getItem(VISTA_KEY); } catch (e) { return null; } })();
  aplicarVista(vistaGuardada === 'tecnica' ? 'tecnica' : 'informativa');
  if (elBtnVistaInformativa) elBtnVistaInformativa.addEventListener('click', function () { aplicarVista('informativa'); });
  if (elBtnVistaTecnica) elBtnVistaTecnica.addEventListener('click', function () { aplicarVista('tecnica'); });

  // Aviso con voz (M8): silenciable, persistido; por defecto ENCENDIDO (skill ui-subtitulos: el
  // estado nunca depende sólo de un canal — acá la voz es un extra sobre la tarjeta con texto+color,
  // apagarla no oculta ninguna alarma visual). Ahora vive en Configuración → Avisos.
  var SONIDO_KEY = 'panelSonido';
  if (elToggleSonido) {
    var sonidoGuardado = (function () { try { return localStorage.getItem(SONIDO_KEY); } catch (e) { return null; } })();
    elToggleSonido.checked = sonidoGuardado !== '0';
    elToggleSonido.addEventListener('change', function () {
      try { localStorage.setItem(SONIDO_KEY, elToggleSonido.checked ? '1' : '0'); } catch (e) { /* no fatal */ }
    });
  }

  // Volumen del aviso con voz: 0–100 %, default 70 % (pedido Ricardo), persistido en localStorage.
  var elInputVolumen = document.getElementById('input-volumen');
  var elVolumenValor = document.getElementById('volumen-valor');
  function leerVolumen() {
    try {
      var v = localStorage.getItem(VOLUMEN_KEY);
      if (v == null) return 70;
      var n = parseInt(v, 10);
      return isFinite(n) ? Math.min(100, Math.max(0, n)) : 70;
    } catch (e) { return 70; }
  }
  function guardarVolumen(v) {
    try { localStorage.setItem(VOLUMEN_KEY, String(v)); } catch (e) { /* no fatal */ }
  }
  function aplicarVolumenUi() {
    var v = leerVolumen();
    if (elInputVolumen) elInputVolumen.value = String(v);
    if (elVolumenValor) elVolumenValor.textContent = v + '%';
  }
  aplicarVolumenUi();
  if (elInputVolumen) elInputVolumen.addEventListener('input', function () {
    var v = parseInt(elInputVolumen.value, 10) || 0;
    guardarVolumen(v);
    if (elVolumenValor) elVolumenValor.textContent = v + '%';
  });
  var elBtnProbarAviso = document.getElementById('btn-probar-aviso');
  if (elBtnProbarAviso) elBtnProbarAviso.addEventListener('click', function () {
    if (!('speechSynthesis' in window)) return;
    try {
      var u = new SpeechSynthesisUtterance(t('voz_prueba'));
      u.lang = I18N ? I18N.vozLang() : 'es-AR';
      u.volume = leerVolumen() / 100;
      window.speechSynthesis.speak(u);
    } catch (e) { console.warn('panel: no se pudo probar el aviso de voz', e); }
  });

  // ---------------------------------------------------------------- popovers flotantes ("?")
  // Pedido de Ricardo: la ayuda ya NO se despliega en línea (empujaba el layout); es un popover
  // FLOTANTE anclado al botón (position: fixed, ver estilo.css), con sombra sobria, ancho máx.
  // ~420 px y z-index sobre todo el panel. Cierra con Esc, clic afuera y el mismo botón; maneja
  // foco (al abrir, foco al botón de cerrar; al cerrar, foco vuelve al botón que abrió). En
  // pantallas angostas (≤480 px) ocupa el ancho con 16 px de margen vía CSS (estilo.css).
  function crearFlotante(btn, elFlotante, opciones) {
    var abierto = false;
    var onClickFueraLigado = null;
    function posicionar() {
      if (opciones && opciones.overlay) return; // drawer lateral: CSS lo ancla (right:0), no se reposiciona
      if (window.innerWidth <= 480) return; // CSS fija el ancho/posición en mobile
      var r = btn.getBoundingClientRect();
      elFlotante.style.top = (r.bottom + 8) + 'px';
      var anchoFlotante = elFlotante.offsetWidth;
      var left = r.right - anchoFlotante;
      var maxLeft = window.innerWidth - anchoFlotante - 8;
      if (left > maxLeft) left = maxLeft;
      if (left < 8) left = 8;
      elFlotante.style.left = left + 'px';
    }
    function onKeydown(e) { if (e.key === 'Escape') cerrar(true); }
    function onClickFuera(e) {
      if (elFlotante.contains(e.target) || btn.contains(e.target)) return;
      cerrar(false);
    }
    function abrir() {
      if (abierto) return;
      abierto = true;
      elFlotante.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      posicionar();
      document.addEventListener('keydown', onKeydown);
      // setTimeout: evita que el mismo click que abre el flotante dispare "clic afuera" al burbujear
      // hasta document (patrón estándar de popovers).
      onClickFueraLigado = onClickFuera;
      setTimeout(function () { document.addEventListener('click', onClickFueraLigado); }, 0);
      if (opciones && opciones.overlay) opciones.overlay.hidden = false;
      var focoInicial = elFlotante.querySelector('[data-cerrar-popover], button, input, [tabindex]');
      if (focoInicial) focoInicial.focus();
      if (opciones && typeof opciones.alAbrir === 'function') opciones.alAbrir();
    }
    function cerrar(devolverFoco) {
      if (!abierto) return;
      abierto = false;
      elFlotante.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('keydown', onKeydown);
      if (onClickFueraLigado) { document.removeEventListener('click', onClickFueraLigado); onClickFueraLigado = null; }
      if (opciones && opciones.overlay) opciones.overlay.hidden = true;
      if (devolverFoco !== false) btn.focus();
      if (opciones && typeof opciones.alCerrar === 'function') opciones.alCerrar();
    }
    btn.addEventListener('click', function () { abierto ? cerrar(true) : abrir(); });
    var botonesCerrar = elFlotante.querySelectorAll('[data-cerrar-popover]');
    botonesCerrar.forEach && botonesCerrar.forEach(function (b) { b.addEventListener('click', function () { cerrar(true); }); });
    // NodeList.forEach no existe en IE, pero el resto del archivo ya usa ES5+DOM moderno (Array.map,
    // etc.) — sin polyfill a propósito, mismo criterio que el resto del panel.
    window.addEventListener('resize', function () { if (abierto) posicionar(); });
    return { abrir: abrir, cerrar: cerrar };
  }

  var ctrlAyudaInformativa = null, ctrlAyudaTecnica = null;
  if (elBtnAyudaInformativa) {
    var popAyudaInformativa = document.getElementById('popover-ayuda-informativa');
    if (popAyudaInformativa) ctrlAyudaInformativa = crearFlotante(elBtnAyudaInformativa, popAyudaInformativa);
  }
  if (elBtnAyudaTecnica) {
    var popAyudaTecnica = document.getElementById('popover-ayuda-tecnica');
    if (popAyudaTecnica) ctrlAyudaTecnica = crearFlotante(elBtnAyudaTecnica, popAyudaTecnica);
  }

  // ---------------------------------------------------------------- drawer de Configuración
  var elBtnConfig = document.getElementById('btn-config');
  var elPanelConfig = document.getElementById('panel-config');
  var elOverlayConfig = document.getElementById('overlay-config');
  var elBtnConfigCerrar = document.getElementById('btn-config-cerrar');
  if (elBtnConfig && elPanelConfig) {
    var drawerConfig = crearFlotante(elBtnConfig, elPanelConfig, {
      overlay: elOverlayConfig,
      alAbrir: function () { if (elInputToken) elInputToken.focus(); }
    });
    if (elOverlayConfig) elOverlayConfig.addEventListener('click', function () { drawerConfig.cerrar(false); });
    if (elBtnConfigCerrar) elBtnConfigCerrar.addEventListener('click', function () { drawerConfig.cerrar(true); });
  }

  // ---------------------------------------------------------------- idioma ES/EN (panel/i18n.js)
  var elBtnLocale = document.getElementById('btn-locale');
  function actualizarBotonLocale() {
    if (!elBtnLocale || !I18N) return;
    var actual = I18N.getLocale();
    elBtnLocale.querySelectorAll('.btn-locale__opcion').forEach(function (span) {
      span.classList.toggle('btn-locale__opcion--activa', span.getAttribute('data-locale') === actual);
    });
  }
  // Ayuda informativa como LISTA ESCANEABLE (pedido de Ricardo vía orquestador, no un párrafo
  // corrido): una fila por estado, con el MISMO color semántico e ícono que usa la tarjeta de esa
  // sala (var(--color-exito/error/atencion/aviso/texto-secundario/texto-silenciado), sin paleta
  // nueva) y una sola línea de explicación al lado; el color es sólo un clasificador — el texto de
  // la fila es la fuente real (skill ui-subtitulos: nunca sólo color). Debajo, 3 bullets breves y,
  // al pie, en gris, la referencia a la vista Técnica.
  var FILAS_AYUDA_INFORMATIVA = [
    { clase: 'mudo', icono: '◐', tituloKey: 'ayuda_fila_mudo_titulo', textoKey: 'ayuda_fila_mudo_texto' },
    { clase: 'rotacion', icono: '↺', tituloKey: 'ayuda_fila_rotacion_titulo', textoKey: 'ayuda_fila_rotacion_texto' },
    { clase: 'traduccion', icono: '▲', tituloKey: 'ayuda_fila_traduccion_titulo', textoKey: 'ayuda_fila_traduccion_texto' },
    { clase: 'reconectando', icono: '◌', tituloKey: 'ayuda_fila_reconectando_titulo', textoKey: 'ayuda_fila_reconectando_texto' },
    { clase: 'sana', icono: '●', tituloKey: 'ayuda_fila_sana_titulo', textoKey: 'ayuda_fila_sana_texto' },
    { clase: 'terminada', icono: '■', tituloKey: 'ayuda_fila_terminada_titulo', textoKey: 'ayuda_fila_terminada_texto' }
  ];
  function construirAyudaInformativa() {
    var el = document.getElementById('ayuda-informativa-contenido');
    if (!el) return;
    var filas = FILAS_AYUDA_INFORMATIVA.map(function (f) {
      return '<li class="ayuda-lista__fila">' +
        '<span class="ayuda-lista__icono ayuda-lista__icono--' + f.clase + '" aria-hidden="true">' + f.icono + '</span>' +
        '<span class="ayuda-lista__texto"><b>' + esc(t(f.tituloKey)) + '</b> — ' + esc(t(f.textoKey)) + '</span>' +
      '</li>';
    }).join('');
    var bullets = ['ayuda_bullet_urgencia', 'ayuda_bullet_quehacer', 'ayuda_bullet_voz', 'ayuda_bullet_salas'].map(function (k) {
      return '<li>' + esc(t(k)) + '</li>';
    }).join('');
    el.innerHTML =
      '<ul class="ayuda-lista">' + filas + '</ul>' +
      '<ul class="ayuda-lista__bullets">' + bullets + '</ul>' +
      '<p class="ayuda-lista__pie">' + esc(t('ayuda_pie_tecnico')) + '</p>';
  }

  function aplicarI18nEstatico() {
    if (!I18N) return;
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      el.innerHTML = t(el.getAttribute('data-i18n-html'));
    });
    document.querySelectorAll('[data-i18n-attr]').forEach(function (el) {
      el.getAttribute('data-i18n-attr').split(',').forEach(function (par) {
        var partes = par.split(':');
        if (partes.length === 2) el.setAttribute(partes[0].trim(), t(partes[1].trim()));
      });
    });
    actualizarBotonLocale();
    actualizarChipSeguridad();
    construirAyudaInformativa();
    document.title = t('titulo_pagina');
  }
  if (elBtnLocale) elBtnLocale.addEventListener('click', function () {
    if (!I18N) return;
    I18N.setLocale(I18N.getLocale() === 'es' ? 'en' : 'es');
  });
  if (I18N) I18N.onChange(function () {
    aplicarI18nEstatico();
    // Re-render de todo lo que ya se armó con texto en el idioma anterior (chips dinámicos, tabla,
    // tarjetas): mismas fuentes de datos, sin re-pedir nada al hub.
    if (elChipHub.textContent) {
      // El último resultado (ok/sin respuesta) puede volver a pedirse sin esperar el poll de 2 s.
      pollSesiones();
    }
    render();
  });
  aplicarI18nEstatico();

  // ---------------------------------------------------------------- control de salas (drawer "Salas")
  // Servicio APARTE del hub (`ops/control.py`, o `qa/out/panel-control/stub_control.py` mientras no
  // exista — ver reportes/monitor-control.md), mismo token que /api/metricas (leerToken() de arriba,
  // Authorization: Bearer, NUNCA query string). Resolución de origen: default
  // `http://<location.hostname>:8110`; `?control=host:puerto` la sobrescribe con la MISMA validación
  // que `?hub=` (hubParamSeguro, ya definida arriba: sin userinfo, sin "@ / \ ? #", sólo localhost/
  // 127.0.0.1/[::1]/el mismo host). Un valor rechazado se ignora completo y se avisa con texto
  // (#chip-control-seguridad), igual que el hub.
  var controlParamCrudo = params.get('control');
  var controlParam = (controlParamCrudo && hubParamSeguro(controlParamCrudo)) ? controlParamCrudo : null;
  var controlIgnoradoPorSeguridad = !!controlParamCrudo && !controlParam;
  var PUERTO_DEV_CONTROL_DEFAULT = '8110';
  var CONTROL_BASE = httpScheme + '://' + (controlParam || (location.hostname + ':' + PUERTO_DEV_CONTROL_DEFAULT));
  var CONTROL_POLL_MS = 3000; // brief: refresco cada 3 s; corre siempre (drawer abierto o cerrado) para
                              // que las tarjetas Informativa sepan qué salas tienen clip ("Escuchar").
  var PUERTO_DEV_WEB = '8101';

  var elChipControlSeguridad = document.getElementById('chip-control-seguridad');
  function actualizarChipControlSeguridad() {
    if (!elChipControlSeguridad) return;
    if (controlIgnoradoPorSeguridad) {
      elChipControlSeguridad.textContent = t('chip_control_seguridad', { valor: controlParamCrudo });
      elChipControlSeguridad.hidden = false;
    } else {
      elChipControlSeguridad.hidden = true;
    }
  }
  actualizarChipControlSeguridad();

  // Misma lógica que web/index.html para armar el link "Ver sala": si el hub sirve panel Y web desde
  // el mismo origen (mismoOrigen, definida arriba), la vista vive en location.origin; si no (dev,
  // panel en 8102 aparte), en el puerto de dev de web/servir.py (8101) del mismo host.
  function urlVistaSala(id, langMostrar) {
    var qs = 'lang=' + encodeURIComponent(langMostrar);
    if (mismoOrigen) return location.origin + '/s/' + encodeURIComponent(id) + '?' + qs;
    return httpScheme + '://' + location.hostname + ':' + PUERTO_DEV_WEB + '/s/' + encodeURIComponent(id) + '?' + qs;
  }

  function fetchControl(path, method, body) {
    var tok = leerToken();
    var headers = {};
    if (tok) headers.Authorization = 'Bearer ' + tok;
    var opts = { method: method || 'GET', headers: headers };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(CONTROL_BASE + path, opts);
  }

  var salasEstado = {
    disponible: null,   // null: todavía no se supo · true/false
    requiereToken: false,
    salas: [],
    porId: {},
    fuentes: null,
    audios: {}           // id -> <audio> persistido (addendum "escuchar": no recrear en cada poll)
  };

  function basename(p) { return String(p || '').split(/[\\/]/).pop(); }

  function destinosDeTraducirA(traducirA, langOrigen) {
    if (!traducirA || traducirA === 'none') return [];
    if (traducirA === 'auto') return [langOrigen === 'en' ? 'es' : 'en']; // valor legado, por si acaso
    return traducirA.split(',').filter(function (x) { return x && x !== langOrigen; });
  }
  function idiomaSalaTexto(sala) {
    var origen = nombreIdioma(sala.lang);
    var destinos = destinosDeTraducirA(sala.traducir_a, sala.lang);
    if (!destinos.length) return origen + ' (' + t('salas_sin_traduccion') + ')';
    return origen + ' → ' + destinos.map(nombreIdioma).join(', ');
  }
  function langParaVista(sala) {
    var destinos = destinosDeTraducirA(sala.traducir_a, sala.lang);
    return destinos.length ? destinos[0] : sala.lang;
  }

  function iconoEstadoSala(estado) {
    if (estado === 'corriendo') return 'exito';
    if (estado === 'arrancando' || estado === 'deteniendo') return 'aviso';
    if (estado === 'reiniciando') return 'atencion';
    if (estado === 'error') return 'error';
    return 'silenciado'; // detenida
  }
  function textoEstadoSala(sala) {
    if (sala.estado === 'corriendo') return t('salas_estado_corriendo');
    if (sala.estado === 'arrancando') return t('salas_estado_arrancando');
    if (sala.estado === 'reiniciando') return t('salas_estado_reiniciando');
    if (sala.estado === 'deteniendo') return t('salas_estado_deteniendo');
    if (sala.estado === 'error') return t('salas_estado_error', { detalle: sala.ultimo_error || '—' });
    return t('salas_estado_detenida');
  }
  function fuenteLegible(fuente) {
    if (!fuente) return '—';
    if (fuente.tipo === 'mic') return t('salas_fuente_mic_legible', { valor: fuente.valor });
    if (fuente.tipo === 'archivo') return t('salas_fuente_archivo_legible', { valor: basename(fuente.valor) });
    if (fuente.tipo === 'url') return t('salas_fuente_url_legible', { valor: fuente.valor });
    return '—';
  }
  function fmtMinSeg(s) {
    s = Math.max(0, Math.round(s || 0));
    return Math.floor(s / 60) + ':' + pad2(s % 60);
  }
  function urlYoutubeConTiempo(videoOrigen) {
    var sep = videoOrigen.url.indexOf('?') > -1 ? '&' : '?';
    return videoOrigen.url + sep + 't=' + Math.round(videoOrigen.inicio_s || 0) + 's';
  }

  // ---- reproducción sincronizada del clip (addendum "escuchar el original") ----
  function obtenerAudioEl(sala) {
    if (!salasEstado.audios[sala.id]) {
      var el = document.createElement('audio');
      el.preload = 'none';
      salasEstado.audios[sala.id] = el;
    }
    return salasEstado.audios[sala.id];
  }
  function urlAudioClip(fuente) {
    return CONTROL_BASE + '/api/control/audio/' + encodeURIComponent(basename(fuente.valor));
  }
  function sincronizarYReproducir(sala, audioEl, volumen) {
    if (!audioEl.src) audioEl.src = urlAudioClip(sala.fuente);
    var anchor = sala.audio_inicio;
    function posicionar() {
      var pos = anchor ? (Date.now() / 1000 - anchor) : 0;
      if (!isFinite(pos) || pos < 0) pos = 0;
      if (audioEl.duration && isFinite(audioEl.duration) && pos >= audioEl.duration) pos = 0;
      try { audioEl.currentTime = pos; } catch (e) { /* metadata todavía no cargó: se reintenta abajo */ }
    }
    if (audioEl.readyState >= 1) posicionar();
    else audioEl.addEventListener('loadedmetadata', posicionar, { once: true });
    audioEl.volume = (volumen == null ? 0.7 : volumen);
    var p = audioEl.play();
    if (p && p.catch) p.catch(function (e) { console.warn('panel: no se pudo reproducir el clip', e); });
  }
  function alternarEscuchar(sala, volumenPorDefecto) {
    var audioEl = obtenerAudioEl(sala);
    if (!audioEl.paused) { audioEl.pause(); return; }
    sincronizarYReproducir(sala, audioEl, audioEl.volume || volumenPorDefecto || 0.7);
  }

  // ---- polling: /api/control/salas y /api/control/fuentes ----
  function actualizarSalas() {
    return fetchControl('/api/control/salas').then(function (r) {
      if (r.status === 401) {
        salasEstado.disponible = true;
        salasEstado.requiereToken = true;
        salasEstado.salas = [];
        return;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json().then(function (data) {
        salasEstado.disponible = true;
        salasEstado.requiereToken = false;
        salasEstado.salas = Array.isArray(data.salas) ? data.salas : [];
      });
    }).catch(function () {
      salasEstado.disponible = false;
      salasEstado.salas = [];
    }).then(function () {
      salasEstado.porId = {};
      salasEstado.salas.forEach(function (s) { salasEstado.porId[s.id] = s; });
      if (!elPanelSalas || !elPanelSalas.hidden) renderSalas();
      render(); // refresca las tarjetas Informativa (botón "Escuchar" por sesión)
    });
  }
  function actualizarFuentes() {
    return fetchControl('/api/control/fuentes').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (data) {
      salasEstado.fuentes = data;
      poblarSelectsFuente();
    }).catch(function (e) { console.warn('panel: no se pudo leer /api/control/fuentes', e); });
  }

  // ---- acciones ----
  function mostrarErrorGeneral(msg) {
    var el = document.getElementById('salas-error-general');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }
  function accionSala(id, accion) {
    fetchControl('/api/control/salas/' + encodeURIComponent(id) + '/' + accion, 'POST').then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) {
        throw new Error(d.error || ('HTTP ' + r.status));
      });
      return actualizarSalas();
    }).catch(function (e) { mostrarErrorGeneral(t('salas_error_generico', { detalle: e.message || String(e) })); });
  }
  function borrarSala(id) {
    fetchControl('/api/control/salas/' + encodeURIComponent(id), 'DELETE').then(function (r) {
      if (!(r.ok || r.status === 204)) throw new Error('HTTP ' + r.status);
      return actualizarSalas();
    }).catch(function (e) { mostrarErrorGeneral(t('salas_error_generico', { detalle: e.message || String(e) })); });
  }

  // ---- render del drawer ----
  var COMANDO_OPS_CONTROL = 'python -m ops.control --hub ws://localhost:8100/ingest';
  function salaFilaHtml(sala) {
    var claseEstado = iconoEstadoSala(sala.estado);
    var puedeIniciar = sala.estado === 'detenida' || sala.estado === 'error';
    var puedeDetener = sala.estado === 'corriendo' || sala.estado === 'arrancando' || sala.estado === 'reiniciando';
    var link = urlVistaSala(sala.id, langParaVista(sala));
    var fuenteHtml = '';
    if (sala.fuente && sala.fuente.tipo === 'mic') {
      fuenteHtml = '<p class="sala-fila__fuente">' + esc(fuenteLegible(sala.fuente)) +
        ' — <span class="form-salas__ayuda">' + esc(t('salas_fuente_mic_en_vivo')) + '</span></p>';
    } else if (sala.fuente && sala.fuente.tipo === 'url') {
      fuenteHtml = '<p class="sala-fila__fuente">' + esc(fuenteLegible(sala.fuente)) + ' — <a href="' +
        esc(sala.fuente.valor) + '" target="_blank" rel="noopener">' + esc(sala.fuente.valor) + '</a></p>';
    } else if (sala.fuente && sala.fuente.tipo === 'archivo') {
      var origenHtml = (sala.video_origen && sala.video_origen.url)
        ? ' · <a href="' + esc(urlYoutubeConTiempo(sala.video_origen)) + '" target="_blank" rel="noopener">' +
          esc(t('salas_ver_original', { m: fmtMinSeg(sala.video_origen.inicio_s) })) + '</a>'
        : '';
      fuenteHtml =
        '<p class="sala-fila__fuente">' + esc(fuenteLegible(sala.fuente)) +
          ' <button type="button" class="sala-fila__escuchar" data-accion="escuchar" data-id="' + esc(sala.id) + '">' +
            esc(t('salas_escuchar')) + '</button>' +
          '<span id="audio-slot-' + cssEscape(sala.id) + '" class="audio-slot"></span>' +
          '<input type="range" class="sala-fila__volumen" min="0" max="100" value="70" data-accion="volumen" data-id="' + esc(sala.id) + '" aria-label="volumen">' +
          origenHtml +
        '</p>';
    }
    return (
      '<article class="sala-fila sala-fila--' + claseEstado + '">' +
        '<div class="sala-fila__cabecera">' +
          '<b>' + esc(sala.titulo || sala.id) + '</b>' +
          '<span class="sala-fila__estado sala-fila__estado--' + claseEstado + '">' +
            '<span aria-hidden="true">●</span> ' + esc(textoEstadoSala(sala)) + '</span>' +
        '</div>' +
        '<p class="sala-fila__idioma">' + esc(idiomaSalaTexto(sala)) + '</p>' +
        fuenteHtml +
        '<p class="sala-fila__meta">' + esc(t('salas_intentos', { n: num(sala.intentos || 0) })) +
          (sala.pid ? ' · ' + esc(t('salas_pid', { pid: sala.pid })) : '') + '</p>' +
        '<div class="sala-fila__botones">' +
          '<button type="button" data-accion="iniciar" data-id="' + esc(sala.id) + '"' + (puedeIniciar ? '' : ' disabled') + '>' + esc(t('salas_btn_iniciar')) + '</button>' +
          '<button type="button" data-accion="detener" data-id="' + esc(sala.id) + '"' + (puedeDetener ? '' : ' disabled') + '>' + esc(t('salas_btn_detener')) + '</button>' +
          '<button type="button" data-accion="borrar" data-id="' + esc(sala.id) + '" data-titulo="' + esc(sala.titulo || sala.id) + '">' + esc(t('salas_btn_borrar')) + '</button>' +
          '<a class="sala-fila__ver" href="' + esc(link) + '" target="_blank" rel="noopener">' + esc(t('salas_ver_sala')) + '</a>' +
        '</div>' +
      '</article>'
    );
  }

  var elPanelSalas = document.getElementById('panel-salas');
  var elAvisoServicio = document.getElementById('salas-aviso-servicio');
  var elAvisoServicioTexto = document.getElementById('salas-aviso-servicio-texto');
  var elBtnCopiarComando = document.getElementById('salas-btn-copiar-comando');
  var elListaSalas = document.getElementById('lista-salas');
  var elSalasVacio = document.getElementById('salas-vacio');
  var elFormNuevaSala = document.getElementById('form-nueva-sala');

  function renderSalas() {
    if (!elListaSalas) return;
    if (salasEstado.disponible === false) {
      if (elAvisoServicio) elAvisoServicio.hidden = false;
      if (elAvisoServicioTexto) elAvisoServicioTexto.textContent = t('salas_servicio_no_responde', { comando: COMANDO_OPS_CONTROL });
      if (elBtnCopiarComando) elBtnCopiarComando.hidden = false;
      elListaSalas.innerHTML = ''; delete elListaSalas.dataset.ultimoHtml;
      if (elSalasVacio) elSalasVacio.hidden = true;
      if (elFormNuevaSala) elFormNuevaSala.hidden = true;
      return;
    }
    if (elFormNuevaSala) elFormNuevaSala.hidden = false;
    if (salasEstado.requiereToken) {
      if (elAvisoServicio) elAvisoServicio.hidden = false;
      if (elAvisoServicioTexto) elAvisoServicioTexto.textContent = t('salas_falta_token');
      if (elBtnCopiarComando) elBtnCopiarComando.hidden = true;
      elListaSalas.innerHTML = ''; delete elListaSalas.dataset.ultimoHtml;
      if (elSalasVacio) elSalasVacio.hidden = true;
      return;
    }
    if (elAvisoServicio) elAvisoServicio.hidden = true;
    var salas = salasEstado.salas;
    if (elSalasVacio) elSalasVacio.hidden = salas.length !== 0;
    // Sólo se redibuja si algo cambió: redibujar cada 3 s reemplazaba los botones y un clic en
    // "Detener" justo en ese instante se perdía (visto en la prueba de punta a punta del 25/09).
    var htmlSalas = salas.map(salaFilaHtml).join('');
    if (htmlSalas === elListaSalas.dataset.ultimoHtml) return;
    elListaSalas.dataset.ultimoHtml = htmlSalas;
    elListaSalas.innerHTML = htmlSalas;
    // Reinsertar los <audio> ya creados (obtenerAudioEl): recrearlos en cada poll de 3 s cortaría la
    // reproducción en curso.
    salas.forEach(function (sala) {
      if (sala.fuente && sala.fuente.tipo === 'archivo') {
        var slot = document.getElementById('audio-slot-' + cssEscape(sala.id));
        if (slot && !slot.contains(salasEstado.audios[sala.id])) slot.appendChild(obtenerAudioEl(sala));
      }
    });
  }

  if (elListaSalas) {
    elListaSalas.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('[data-accion]') : null;
      if (!btn) return;
      var accion = btn.getAttribute('data-accion');
      var id = btn.getAttribute('data-id');
      if (accion === 'iniciar' || accion === 'detener') accionSala(id, accion);
      else if (accion === 'borrar') {
        var titulo = btn.getAttribute('data-titulo') || id;
        if (window.confirm(t('salas_confirmar_borrar', { titulo: titulo }))) borrarSala(id);
      } else if (accion === 'escuchar') {
        var sala = salasEstado.porId[id];
        if (sala) {
          var rango = elListaSalas.querySelector('input[data-accion="volumen"][data-id="' + id + '"]');
          alternarEscuchar(sala, rango ? (parseInt(rango.value, 10) || 70) / 100 : 0.7);
        }
      }
    });
    elListaSalas.addEventListener('input', function (e) {
      var el = e.target;
      if (el.getAttribute && el.getAttribute('data-accion') === 'volumen') {
        var sala = salasEstado.porId[el.getAttribute('data-id')];
        if (sala) obtenerAudioEl(sala).volume = (parseInt(el.value, 10) || 0) / 100;
      }
    });
  }

  // Botón "Escuchar" en la tarjeta Informativa (mismo <audio> compartido, ver tarjetaHtml() arriba).
  elTarjetas && elTarjetas.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('[data-accion="escuchar-tarjeta"]') : null;
    if (!btn) return;
    var sala = salasEstado.porId[btn.getAttribute('data-id')];
    if (sala) alternarEscuchar(sala, 0.7);
  });

  if (elBtnCopiarComando) elBtnCopiarComando.addEventListener('click', function () {
    if (!(navigator.clipboard && navigator.clipboard.writeText)) return;
    navigator.clipboard.writeText(COMANDO_OPS_CONTROL).then(function () {
      var prev = t('salas_copiar_comando');
      elBtnCopiarComando.textContent = t('salas_copiado');
      setTimeout(function () { elBtnCopiarComando.textContent = prev; }, 1500);
    }).catch(function () { /* el comando ya está visible arriba como texto para copiar a mano */ });
  });

  // ---- formulario "Nueva sala" ----
  function slugify(s) {
    var out = String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
    if (out && !/^[a-z0-9]/.test(out)) out = 's' + out;
    return out;
  }
  var elInputTitulo = document.getElementById('salas-input-titulo');
  var elInputId = document.getElementById('salas-input-id');
  var elSelectLang = document.getElementById('salas-select-lang');
  var elChipsTraducir = document.getElementById('salas-chips-traducir');
  var elSelectMic = document.getElementById('salas-select-mic');
  var elSelectArchivo = document.getElementById('salas-select-archivo');
  var elSelectKey = document.getElementById('salas-select-key');
  var elInputUrl = document.getElementById('salas-input-url');
  var elInputDuracion = document.getElementById('salas-input-duracion');
  var elCheckArrancar = document.getElementById('salas-check-arrancar');
  var elPanelMic = document.getElementById('salas-fuente-panel-mic');
  var elPanelArchivo = document.getElementById('salas-fuente-panel-archivo');
  var elPanelUrl = document.getElementById('salas-fuente-panel-url');
  var elBtnActualizarFuentes = document.getElementById('salas-btn-actualizar-fuentes');
  var idTocadoManualmente = false;

  if (elInputId) elInputId.addEventListener('input', function () { idTocadoManualmente = true; });
  if (elInputTitulo) elInputTitulo.addEventListener('input', function () {
    if (!idTocadoManualmente && elInputId) elInputId.value = slugify(elInputTitulo.value);
  });

  var IDIOMAS_DEFAULT = [{ codigo: 'en', probado: true }, { codigo: 'es', probado: true }];
  function idiomasDisponibles() { return (salasEstado.fuentes && salasEstado.fuentes.idiomas) || IDIOMAS_DEFAULT; }
  function poblarChipsTraducir() {
    if (!elChipsTraducir) return;
    var langOrigen = elSelectLang ? elSelectLang.value : 'en';
    var opuesto = langOrigen === 'en' ? 'es' : (langOrigen === 'es' ? 'en' : null);
    var previos = Array.prototype.map.call(elChipsTraducir.querySelectorAll('input:checked'), function (i) { return i.value; });
    elChipsTraducir.innerHTML = idiomasDisponibles().filter(function (idi) { return idi.codigo !== langOrigen; }).map(function (idi) {
      var marcar = previos.length ? (previos.indexOf(idi.codigo) > -1) : (idi.codigo === opuesto);
      return '<label class="salas-chip"><input type="checkbox" value="' + esc(idi.codigo) + '"' + (marcar ? ' checked' : '') + '> ' + esc(nombreIdioma(idi.codigo)) + '</label>';
    }).join('');
  }
  function poblarIdiomas() {
    var idiomas = idiomasDisponibles();
    if (elSelectLang) {
      var actual = elSelectLang.value;
      elSelectLang.innerHTML = idiomas.map(function (idi) {
        var sufijo = idi.probado ? '' : ' ' + t('salas_sin_probar');
        return '<option value="' + esc(idi.codigo) + '">' + esc(nombreIdioma(idi.codigo) + sufijo) + '</option>';
      }).join('');
      elSelectLang.value = idiomas.some(function (i) { return i.codigo === actual; }) ? actual : (idiomas[0] ? idiomas[0].codigo : 'en');
    }
    poblarChipsTraducir();
  }
  if (elSelectLang) elSelectLang.addEventListener('change', poblarChipsTraducir);

  function tipoFuenteSeleccionado() {
    var marcado = document.querySelector('input[name="salas-fuente-tipo"]:checked');
    return marcado ? marcado.value : 'mic';
  }
  function actualizarPanelFuente() {
    var tipo = tipoFuenteSeleccionado();
    if (elPanelMic) elPanelMic.hidden = tipo !== 'mic';
    if (elPanelArchivo) elPanelArchivo.hidden = tipo !== 'archivo';
    if (elPanelUrl) elPanelUrl.hidden = tipo !== 'url';
  }
  document.querySelectorAll('input[name="salas-fuente-tipo"]').forEach(function (r) {
    r.addEventListener('change', actualizarPanelFuente);
  });
  actualizarPanelFuente();

  function poblarSelectsFuente() {
    var f = salasEstado.fuentes || { microfonos: [], archivos: [], keys: [] };
    if (elSelectMic) {
      elSelectMic.innerHTML = (f.microfonos || []).length
        ? f.microfonos.map(function (m) { return '<option value="' + esc(m) + '">' + esc(m) + '</option>'; }).join('')
        : '<option value="" disabled selected>' + esc(t('salas_sin_microfonos')) + '</option>';
    }
    if (elSelectArchivo) {
      elSelectArchivo.innerHTML = (f.archivos || []).length
        ? f.archivos.map(function (p) { return '<option value="' + esc(p) + '">' + esc(basename(p)) + '</option>'; }).join('')
        : '<option value="" disabled selected>' + esc(t('salas_sin_archivos')) + '</option>';
    }
    if (elSelectKey) {
      elSelectKey.innerHTML = (f.keys || []).map(function (k) { return '<option value="' + esc(k) + '">' + esc(k) + '</option>'; }).join('');
    }
    poblarIdiomas();
  }
  if (elBtnActualizarFuentes) elBtnActualizarFuentes.addEventListener('click', actualizarFuentes);

  function limpiarErroresForm() {
    ['salas-error-id', 'salas-error-fuente', 'salas-error-general'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) { el.hidden = true; el.textContent = ''; }
    });
  }
  function mostrarErrorCampo(id, msg) {
    var el = document.getElementById(id);
    if (el) { el.textContent = msg; el.hidden = false; }
  }

  if (elFormNuevaSala) elFormNuevaSala.addEventListener('submit', function (e) {
    e.preventDefault();
    limpiarErroresForm();
    var titulo = elInputTitulo ? elInputTitulo.value.trim() : '';
    var id = elInputId ? elInputId.value.trim() : '';
    var lang = elSelectLang ? elSelectLang.value : 'en';
    var destinos = elChipsTraducir
      ? Array.prototype.map.call(elChipsTraducir.querySelectorAll('input:checked'), function (i) { return i.value; })
      : [];
    var traducirA = destinos.length ? destinos.join(',') : 'none';
    var tipo = tipoFuenteSeleccionado();
    var valor = tipo === 'mic' ? (elSelectMic ? elSelectMic.value : '')
      : tipo === 'archivo' ? (elSelectArchivo ? elSelectArchivo.value : '')
      : (elInputUrl ? elInputUrl.value.trim() : '');
    var duracionRaw = elInputDuracion ? elInputDuracion.value : '';
    var duracion = duracionRaw ? parseFloat(duracionRaw) : null;
    var arrancar = !!(elCheckArrancar && elCheckArrancar.checked);
    var key = elSelectKey ? elSelectKey.value : '';

    if (!titulo) return mostrarErrorCampo('salas-error-general', t('salas_error_generico', { detalle: 'nombre requerido' }));
    if (!id) return mostrarErrorCampo('salas-error-id', t('salas_error_generico', { detalle: 'id requerido' }));
    if (!valor) return mostrarErrorCampo('salas-error-fuente', t('salas_error_generico', { detalle: 'fuente requerida' }));

    var body = {
      id: id, titulo: titulo, lang: lang, traducir_a: traducirA,
      fuente: { tipo: tipo, valor: valor }, key: key, duracion_s: duracion, arrancar: arrancar
    };
    fetchControl('/api/control/salas', 'POST', body).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 201) {
          idTocadoManualmente = false;
          elFormNuevaSala.reset();
          if (elCheckArrancar) elCheckArrancar.checked = true;
          actualizarPanelFuente();
          poblarIdiomas();
          return actualizarSalas();
        }
        if (r.status === 409 && /id/.test(String(data.error || ''))) {
          var nuevo = id, i = 1;
          do { i += 1; nuevo = id + '-' + i; } while (salasEstado.porId[nuevo]);
          if (elInputId) elInputId.value = nuevo;
          idTocadoManualmente = true;
          return mostrarErrorCampo('salas-error-id', t('salas_id_repetido_sugerencia'));
        }
        mostrarErrorCampo('salas-error-general', t('salas_error_crear', { detalle: data.error || ('HTTP ' + r.status) }));
      });
    }).catch(function (e) {
      mostrarErrorCampo('salas-error-general', t('salas_error_crear', { detalle: e.message || String(e) }));
    });
  });

  // ---- abrir/cerrar el drawer (mismo patrón que Configuración, crearFlotante ya definida arriba) ----
  var elBtnSalas = document.getElementById('btn-salas');
  var elOverlaySalas = document.getElementById('overlay-salas');
  var elBtnSalasCerrar = document.getElementById('btn-salas-cerrar');
  if (elBtnSalas && elPanelSalas) {
    var drawerSalas = crearFlotante(elBtnSalas, elPanelSalas, {
      overlay: elOverlaySalas,
      alAbrir: function () {
        renderSalas();
        actualizarFuentes();
        if (elInputTitulo) elInputTitulo.focus();
      }
    });
    if (elOverlaySalas) elOverlaySalas.addEventListener('click', function () { drawerSalas.cerrar(false); });
    if (elBtnSalasCerrar) elBtnSalasCerrar.addEventListener('click', function () { drawerSalas.cerrar(true); });
  }

  // ---------------------------------------------------------------- arranque
  pollSesiones();
  setInterval(pollSesiones, POLL_MS);
  setInterval(render, TICK_MS);
  actualizarSalas();
  actualizarFuentes();
  setInterval(actualizarSalas, CONTROL_POLL_MS);
})();
