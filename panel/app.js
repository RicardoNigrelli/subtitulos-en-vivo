// app.js — panel de producción (monitor, Bloque 3, R8e)
//
// Fuentes de datos (contracts/README.md, hub/README.md; skill ui-subtitulos "Panel de producción"):
//   - GET /api/sesiones cada 2 s: session_id, lang, title, replay, last_seq, last_t_emit, last_t_hub,
//     state (live/idle/ended), translations_langs, viewers, viewers_por_idioma. Público, sin token.
//   - GET /api/sesiones/<id>/historial?desde=<seq>&tipos=todos cada 2 s: backfill de TODO lo que el
//     hub todavía tiene en memoria (text, rotation, watchdog, error, session_start, session_end).
//     `translation` y `partial` son EFÍMEROS (contracts: TIPOS_EFIMEROS): el hub NUNCA los guarda,
//     sólo se ven pasando por el WS en vivo mientras el panel está conectado.
//   - WS público /ws/<session_id> (sin token, igual que cualquier espectador): stream en vivo de
//     TODOS los tipos salvo el heartbeat del worker (el hub no lo reenvía a audiencia). Es la única
//     fuente para `partial`, `translation` y para la latencia PERCIBIDA (t_receive - t_captured).
//   - GET /panel-api/metricas (servido por panel/servir.py, que agrega el Bearer del lado del server:
//     el token nunca llega al navegador) = espejo de GET /api/metricas del hub (Authorization: Bearer),
//     con los segundos de audio estimados (heartbeat.meta.audio_seconds_sent) por sesión.
//
// Reglas de la skill ui-subtitulos ("Panel de producción"): p50/p95 SIEMPRE (nearest-rank), NUNCA
// promedio; estado nunca sólo por color; rotaciones/reaperturas/errores como contadores con
// timestamp del último.
'use strict';

(function () {
  // ---------------------------------------------------------------- configuración
  var params = new URLSearchParams(location.search);
  var hubParam = params.get('hub');
  var hubHost = hubParam || (location.hostname + ':8100');
  var HTTP_BASE = 'http://' + hubHost;
  var WS_BASE = 'ws://' + hubHost;

  var POLL_MS = 2000;           // /api/sesiones, historial de reconciliación y /panel-api/metricas
  var TICK_MS = 1000;           // re-render (para que "sin texto hace N s" y parciales/60s avancen)
  var SIN_TEXTO_S = 15;         // umbral documentado (brief B3): live + > 15 s sin `text` => "sin texto"
  var MIN_N_PERCENTIL = 10;     // por debajo: "n insuficiente" (brief B3), nunca se inventa un p95
  var VENTANA_PARCIALES_S = 60; // ventana deslizante de "parciales / 60 s" (no es promedio de sesión)
  var WS_BACKOFF_MAX_MS = 10000;

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

  function fmtResumenLatencia(r) {
    if (r.n === 0) return '<span class="n-insuf">sin muestras</span>';
    if (r.n < MIN_N_PERCENTIL) {
      return '<span class="n-insuf">n insuficiente (n=' + r.n + ')</span>';
    }
    return '<span class="num">p50 ' + fmtSeg(r.p50) + ' · p95 ' + fmtSeg(r.p95) + '</span>' +
           '<span class="num-sub">n=' + r.n + '</span>';
  }

  // ---------------------------------------------------------------- estado en memoria (por sesión)
  var sesiones = {}; // session_id -> S
  var ordenFilas = []; // session_id, orden estable de aparición en la tabla

  function nuevaSesion(id) {
    return {
      id: id,
      lang: null, title: null, replay: null,
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

      rotaciones: 0, rotacionesPorMotivo: {}, ultimaRotacionEn: null,
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

    fetch('panel-api/metricas?hub=' + encodeURIComponent(hubHost)).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (m) {
      metricasGlobal = m;
      elChipMetricas.textContent = '/api/metricas: ok';
      elChipMetricas.className = 'chip chip--ok';
      Object.keys(sesiones).forEach(function (id) {
        sesiones[id].metricas = (m.sesiones && m.sesiones[id]) || null;
      });
    }).catch(function (e) {
      elChipMetricas.textContent = '/api/metricas: no disponible';
      elChipMetricas.className = 'chip chip--mal';
      console.warn('panel: /panel-api/metricas falló:', String(e));
    });
  }

  // ---------------------------------------------------------------- render
  function estadoDe(s, ahoraMs) {
    var base = s.hubState || 'waiting';
    if (base === 'ended') return { clase: 'ended', texto: 'ended' };
    if (base === 'idle') return { clase: 'idle', texto: 'idle' };
    if (base === 'waiting') return { clase: 'waiting', texto: 'esperando datos' };
    // live:
    if (s.ultimoTextoEn == null) return { clase: 'live', texto: 'live (sin texto todavía)' };
    var silencioS = (ahoraMs - s.ultimoTextoEn) / 1000;
    if (silencioS > SIN_TEXTO_S) {
      return { clase: 'mudo', texto: 'sin texto hace ' + Math.floor(silencioS) + ' s' };
    }
    return { clase: 'live', texto: 'live' };
  }

  function fmtDetalleMotivos(mapa) {
    var claves = Object.keys(mapa);
    if (!claves.length) return '';
    return claves.sort().map(function (k) { return esc(k) + ':' + mapa[k]; }).join(' · ');
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

  function filaHtml(s, ahoraMs) {
    var estado = estadoDe(s, ahoraMs);
    var lang = s.lang || '—';
    var badgeReplay = s.replay === true ? '<span class="badge badge-replay">REPLAY</span>' :
                       (s.replay === false ? '<span class="badge badge-vivo">EN VIVO</span>' : '');
    var destinos = s.translationsLangs && s.translationsLangs.length ?
      '<span class="num-sub">trad: ' + esc(s.translationsLangs.join(', ')) + '</span>' : '';

    var etiquetaLatWorker = s.replay === true ? 'grabada (t_emit − t_captured)' : 't_emit − t_captured';
    var latWorkerHtml = '<span class="num-sub">' + etiquetaLatWorker + '</span>' +
                        fmtResumenLatencia(resumenLatencia(s.latGrabada));
    var latPercibidaHtml;
    if (s.replay === true) {
      latPercibidaHtml = '<span class="n-insuf">no aplica (replay)</span>';
    } else {
      latPercibidaHtml = '<span class="num-sub">t_receive − t_captured (WS propio)</span>' +
                          fmtResumenLatencia(resumenLatencia(s.latPercibida));
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
      '<td>' + esc(lang) + ' ' + badgeReplay + destinos + '</td>' +
      '<td class="estado estado--' + estado.clase + '">' + esc(estado.texto) + '</td>' +
      '<td>' + esc(s.wsEstado) + '</td>' +
      '<td class="mono">' + (s.lastSeqHub == null ? '—' : s.lastSeqHub) +
        '<span class="num-sub">' + (s.lastTEmitHub ? fmtHoraMs(s.lastTEmitHub * 1000) : '—') + '</span></td>' +
      '<td class="mono">' + s.textos + '</td>' +
      '<td>' + latWorkerHtml + '</td>' +
      '<td>' + latPercibidaHtml + '</td>' +
      '<td>' + fmtContador(s.rotaciones, s.ultimaRotacionEn, fmtDetalleMotivos(s.rotacionesPorMotivo)) + '</td>' +
      '<td>' + fmtContador(s.watchdogReaperturas, s.ultimoWatchdogEn,
                 s.watchdogEventos !== s.watchdogReaperturas ? (s.watchdogEventos + ' eventos watchdog en total') : '') + '</td>' +
      '<td>' + fmtContador(s.errores, s.ultimoErrorEn, s.ultimoErrorDetalle ? esc(s.ultimoErrorDetalle) : '') + '</td>' +
      '<td>' + fmtContador(s.traduccionesOkFalse, s.ultimaTraduccionEn,
                 s.traduccionesOkTrue ? ('ok:true = ' + s.traduccionesOkTrue) : '') + '</td>' +
      '<td class="mono">' + fmtParcialesPorMinuto(s, ahoraMs) + '</td>' +
      '<td>' + fmtAudioEstimado(s) + '</td>' +
      '<td class="mono">' + viewersHtml + '</td>'
    );
  }

  var elCuerpo = document.getElementById('cuerpo-tabla');

  function render() {
    var ahoraMs = Date.now();
    var filaVacia = document.getElementById('fila-vacia');
    if (ordenFilas.length && filaVacia) filaVacia.remove();
    ordenFilas.forEach(function (id) {
      var s = sesiones[id];
      var tr = document.getElementById('fila-' + cssEscape(id));
      if (!tr) {
        tr = document.createElement('tr');
        tr.id = 'fila-' + cssEscape(id);
        elCuerpo.appendChild(tr);
      }
      tr.innerHTML = filaHtml(s, ahoraMs);
    });
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) { return '_' + c.charCodeAt(0) + '_'; });
  }

  // ---------------------------------------------------------------- arranque
  pollSesiones();
  setInterval(pollSesiones, POLL_MS);
  setInterval(render, TICK_MS);
})();
