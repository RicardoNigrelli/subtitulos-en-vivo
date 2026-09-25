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
'use strict';

(function () {
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
  var hubIgnoradoPorSeguridad = !!hubParamCrudo && !hostnamePermitido(hubParamCrudo);
  var hubParam = (hubParamCrudo && hostnamePermitido(hubParamCrudo)) ? hubParamCrudo : null;

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

  var POLL_MS = 2000;           // /api/sesiones, historial de reconciliación y /api/metricas
  var TICK_MS = 1000;           // re-render (para que "sin texto hace N s" y parciales/60s avancen)
  var SIN_TEXTO_S = 15;         // umbral documentado (brief B3): live + > 15 s sin `text` => "sin texto"
  var MIN_N_PERCENTIL = 10;     // por debajo: "n insuficiente" (brief B3), nunca se inventa un p95
  var VENTANA_PARCIALES_S = 60; // ventana deslizante de "parciales / 60 s" (no es promedio de sesión)
  var WS_BACKOFF_MAX_MS = 10000;

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

  function fmtSeg(x) {
    if (x == null || !isFinite(x)) return '—';
    return x.toFixed(3) + ' s';
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
    if (r.n === 0) return '<span class="n-insuf">sin muestras</span>';
    if (r.n < MIN_N_PERCENTIL) {
      return '<span class="lat-par"><span class="n-insuf">n insuficiente (n=' + r.n + ')</span>' + spark + '</span>';
    }
    return '<span class="lat-par"><span class="num">p50 ' + fmtSeg(r.p50) + ' · p95 ' + fmtSeg(r.p95) + '</span>' +
           spark + '</span>' +
           '<span class="num-sub">n=' + r.n + '</span>';
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
    var titulo = 'últimas ' + vals.length + ' muestras: ' + fmtSeg(min) + ' a ' + fmtSeg(max);
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
      watchdogEventos: 0, watchdogReaperturas: 0, ultimoWatchdogEn: null,
      errores: 0, ultimoErrorEn: null, ultimoErrorDetalle: null,
      traduccionesOkFalse: 0, traduccionesOkTrue: 0, ultimaTraduccionEn: null,
      parcialesTs: [],         // ms de cada `partial` visto en vivo (ventana deslizante)

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
  if (elChipHubSeguridad) {
    if (hubIgnoradoPorSeguridad) {
      elChipHubSeguridad.textContent = 'hub externo ignorado por seguridad: "' + hubParamCrudo + '"';
      elChipHubSeguridad.hidden = false;
    } else {
      elChipHubSeguridad.hidden = true;
    }
  }

  var metricasGlobal = null;

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
      elChipHub.textContent = 'hub: ok (' + lista.length + ' sesiones)';
      elChipHub.className = 'chip chip--ok';
      marcarAviso(null);
      lista.forEach(function (resumen) {
        var s = sesionDe(resumen.session_id);
        s.lang = resumen.lang;
        if (resumen.title) s.title = resumen.title;
        if (resumen.replay !== undefined && resumen.replay !== null) s.replay = resumen.replay;
        s.hubState = resumen.state;
        s.lastSeqHub = resumen.last_seq;
        s.lastTEmitHub = resumen.last_t_emit;
        s.viewers = resumen.viewers || 0;
        s.viewersPorIdioma = resumen.viewers_por_idioma || {};
        s.translationsLangs = resumen.translations_langs || [];
        reconciliarHistorial(s);
      });
      elUltimaActualizacion.textContent = 'actualizado ' + fmtHoraMs(Date.now());
      render();
    }).catch(function (e) {
      elChipHub.textContent = 'hub: SIN RESPUESTA';
      elChipHub.className = 'chip chip--mal';
      marcarAviso('No se pudo leer ' + HTTP_BASE + '/api/sesiones (' + String(e) + '). ' +
                  'Los contadores que se ven quedaron en el último valor conocido.');
    });

    fetchMetricas().then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (m) {
      metricasGlobal = m;
      elChipMetricas.textContent = 'métricas: ok';
      elChipMetricas.className = 'chip chip--ok';
      Object.keys(sesiones).forEach(function (id) {
        sesiones[id].metricas = (m.sesiones && m.sesiones[id]) || null;
      });
    }).catch(function (e) {
      if (e && e.message === 'SIN_TOKEN') {
        metricasGlobal = null;
        elChipMetricas.textContent = 'métricas: sin token';
        elChipMetricas.className = 'chip';
        return;
      }
      elChipMetricas.textContent = 'métricas: no disponible (' + String((e && e.message) || e) + ')';
      elChipMetricas.className = 'chip chip--mal';
      console.warn('panel: /api/metricas falló:', String(e));
    });
  }

  // ---------------------------------------------------------------- render
  function estadoDe(s, ahoraMs) {
    var base = s.hubState || 'waiting';
    if (base === 'ended') return { clase: 'ended', texto: 'terminada' };
    if (base === 'idle') return { clase: 'idle', texto: 'inactiva' };
    if (base === 'waiting') return { clase: 'waiting', texto: 'esperando datos' };
    // live:
    if (s.ultimoTextoEn == null) return { clase: 'live', texto: 'en vivo (sin texto todavía)' };
    var silencioS = (ahoraMs - s.ultimoTextoEn) / 1000;
    if (silencioS > SIN_TEXTO_S) {
      return { clase: 'mudo', texto: 'sin texto hace ' + Math.floor(silencioS) + ' s' };
    }
    return { clase: 'live', texto: 'en vivo' };
  }

  // Franja de color por motivo de rotación (sistema.md §2 ref. Master Control Room: color como
  // clasificador, siempre con texto al lado — nunca el punto solo). Motivos sin mapeo (cualquier
  // string que mande el worker) caen en el modificador neutro "otro", no se pierden.
  var MOTIVO_CLASE = { cierre: 'motivo--cierre', atasco: 'motivo--atasco',
                        preventiva: 'motivo--preventiva', goaway: 'motivo--goaway' };
  function fmtDetalleMotivos(mapa) {
    var claves = Object.keys(mapa);
    if (!claves.length) return '';
    return claves.sort().map(function (k) {
      var clase = MOTIVO_CLASE[k] || 'motivo--otro';
      return '<span class="motivo ' + clase + '">' + esc(k) + ':' + mapa[k] + '</span>';
    }).join(' ');
  }

  // B8: audio_lost_s acumulado (B5 ya lo sumaba en aplicarMensaje, pero no se mostraba en ninguna
  // celda: brief B8 punto 3, "verificá la fila"). Va junto al detalle de Rotaciones.
  function fmtDetalleRotacion(s) {
    var motivos = fmtDetalleMotivos(s.rotacionesPorMotivo);
    if (!(s.audioLostS > 0)) return motivos;
    var perdido = 'audio perdido ' + s.audioLostS.toFixed(1) + ' s';
    return motivos ? motivos + ' · ' + perdido : perdido;
  }

  function fmtContador(n, ultimoMs, detalle) {
    var html = '<span class="contador' + (n === 0 ? ' contador-cero' : '') + '">' + n + '</span>';
    if (ultimoMs != null) html += '<span class="contador-detalle">últ. ' + fmtHoraMs(ultimoMs) + '</span>';
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
      return '<span class="n-insuf">sin heartbeat con audio_seconds_sent</span>';
    }
    var seg = m.latido_worker.meta.audio_seconds_sent;
    var hace = m.latido_worker.t_hub ? ((Date.now() / 1000) - m.latido_worker.t_hub) : null;
    var html = '<span class="num">' + seg.toFixed(1) + ' s <i>(estimación)</i></span>';
    if (hace != null) html += '<span class="num-sub">latido hace ' + hace.toFixed(0) + ' s</span>';
    return html;
  }

  function filaHtml(s, ahoraMs, estado) {
    var lang = s.lang || '—';
    var badgeReplay = s.replay === true ? '<span class="badge badge-replay">REPLAY</span>' :
                       (s.replay === false ? '<span class="badge badge-vivo">LIVE</span>' : '');
    var badgeTest = s.test === true ? '<span class="badge badge-test">TEST</span>' : '';
    var destinos = s.translationsLangs && s.translationsLangs.length ?
      '<span class="num-sub">trad: ' + esc(s.translationsLangs.join(', ')) + '</span>' : '';

    var etiquetaLatWorker = s.replay === true ? 'grabada (t_emit − t_captured)' : 't_emit − t_captured';
    var latWorkerHtml = '<span class="num-sub">' + etiquetaLatWorker + '</span>' +
                        fmtResumenLatencia(resumenLatencia(s.latGrabada), sparklineSvg(s.latGrabada));
    var latPercibidaHtml;
    if (s.replay === true) {
      latPercibidaHtml = '<span class="n-insuf">no aplica (replay)</span>';
    } else {
      latPercibidaHtml = '<span class="num-sub">t_receive − t_captured (WS propio)</span>' +
                          fmtResumenLatencia(resumenLatencia(s.latPercibida), sparklineSvg(s.latPercibida));
    }

    var viewersHtml = String(s.viewers || 0);
    var vpi = Object.keys(s.viewersPorIdioma || {});
    if (vpi.length) {
      viewersHtml += '<span class="num-sub">' +
        vpi.sort().map(function (k) { return esc(k) + ':' + s.viewersPorIdioma[k]; }).join(' · ') +
        '</span>';
    }

    return (
      '<td class="col-sesion"><span class="sesion-id">' + esc(s.id) + '</span>' +
        (s.title ? '<span class="sesion-titulo">' + esc(s.title) + '</span>' : '') + '</td>' +
      '<td>' + esc(lang) + ' ' + badgeReplay + badgeTest + destinos + '</td>' +
      '<td class="estado estado--' + estado.clase + '">' + esc(estado.texto) + '</td>' +
      '<td class="mono">' + (s.lastSeqHub == null ? '—' : s.lastSeqHub) +
        '<span class="num-sub">' + (s.lastTEmitHub ? fmtHoraMs(s.lastTEmitHub * 1000) : '—') + '</span></td>' +
      '<td class="mono">' + s.textos + '</td>' +
      '<td>' + latWorkerHtml + '</td>' +
      '<td>' + latPercibidaHtml + '</td>' +
      '<td>' + fmtContador(s.rotaciones, s.ultimaRotacionEn, fmtDetalleRotacion(s)) + '</td>' +
      '<td>' + fmtContador(s.watchdogReaperturas, s.ultimoWatchdogEn,
                 s.watchdogEventos !== s.watchdogReaperturas ? (s.watchdogEventos + ' eventos watchdog en total') : '') + '</td>' +
      '<td>' + fmtContador(s.errores, s.ultimoErrorEn, s.ultimoErrorDetalle ? esc(s.ultimoErrorDetalle) : '') + '</td>' +
      '<td>' + fmtContador(s.traduccionesOkFalse, s.ultimaTraduccionEn,
                 s.traduccionesOkTrue ? ('ok:true = ' + s.traduccionesOkTrue) : '') + '</td>' +
      '<td class="mono col-secundaria">' + fmtParcialesPorMinuto(s, ahoraMs) + '</td>' +
      '<td class="col-secundaria">' + fmtAudioEstimado(s) + '</td>' +
      '<td class="mono">' + viewersHtml + '</td>'
    );
  }

  var elCuerpo = document.getElementById('cuerpo-tabla');

  function render() {
    var ahoraMs = Date.now();
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
      elChipPruebasOcultas.textContent = totalTest === 0 ? 'sin sesiones TEST' :
        (totalTest + (totalTest === 1 ? ' sesión TEST' : ' sesiones TEST') +
         (mostrarPruebas ? ' (mostradas)' : ' (ocultas; "mostrar pruebas" las trae)'));
    }
    // Corrección de dirección (bloque 10): la conexión WS del PANEL a cada sesión es un dato del
    // PANEL, no un estado de la sesión (mostrarlo por fila confundía "terminada" con "en vivo" en
    // la misma línea). Se agrega UNA vez en la barra de estado, agregado sobre todas las filas.
    if (elChipConexion) {
      var totalFilas = ordenFilas.length;
      var enVivo = ordenFilas.reduce(function (acc, id) {
        return acc + (sesiones[id].wsEstado === 'en vivo' ? 1 : 0);
      }, 0);
      elChipConexion.textContent = 'conexión panel: ' + enVivo + '/' + totalFilas + ' en vivo';
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
  var mostrarPruebas = elTogglePruebas ? elTogglePruebas.checked === true : false;

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
    elChipMetricas.textContent = 'métricas: sin token';
    elChipMetricas.className = 'chip';
    render();
  });
  if (elTogglePruebas) elTogglePruebas.addEventListener('change', function () {
    mostrarPruebas = elTogglePruebas.checked;
    render();
  });

  // ---------------------------------------------------------------- layout: alto de cabecera variable
  // La cabecera ahora tiene dos filas y un desplegable "?" (sistema.md §4): su alto cambia según el
  // ancho de pantalla (flex-wrap) y según si "?" está abierto. En vez de un número de píxeles fijo
  // para anclar el thead sticky de la tabla debajo (frágil: se superpone o deja un hueco), se mide
  // el alto real y se publica como variable CSS.
  var elCabecera = document.querySelector('.cabecera');
  function ajustarAltoCabecera() {
    if (!elCabecera) return;
    document.documentElement.style.setProperty('--cabecera-alto', elCabecera.offsetHeight + 'px');
  }
  if (elCabecera) {
    ajustarAltoCabecera();
    window.addEventListener('resize', ajustarAltoCabecera);
    if (typeof ResizeObserver === 'function') {
      new ResizeObserver(ajustarAltoCabecera).observe(elCabecera);
    } else {
      document.querySelectorAll('.ayuda').forEach(function (d) {
        d.addEventListener('toggle', ajustarAltoCabecera);
      });
    }
  }

  // ---------------------------------------------------------------- arranque
  pollSesiones();
  setInterval(pollSesiones, POLL_MS);
  setInterval(render, TICK_MS);
})();
