// app.js — vista de subtítulos de una sesión (frontend, Bloque 1)
// Contrato: v:1, type (text|rotation|watchdog|error|heartbeat|session_start|session_end|init),
// session_id, seq, lang (idioma ORIGINAL), text, translations, replay, meta.
// WS: ws://<host>:8100/ws/<session_id>?lang=<xx>
'use strict';

(function () {
  // ---------- URL: sesión desde el path, idioma e (opcional) hub desde la query ----------
  function parsePath() {
    // funciona con /s/<id> y con /s/<id>/lo-que-sea (servir.py reescribe ambos a sesion.html)
    var m = location.pathname.match(/\/s\/([^/?#]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  var params = new URLSearchParams(location.search);
  var sessionId = parsePath();
  var lang = params.get('lang') || 'en';
  var hubParam = params.get('hub');
  var hubHost = hubParam || (location.hostname + ':8100');
  var wsUrl = 'ws://' + hubHost + '/ws/' + encodeURIComponent(sessionId || '') + '?lang=' + encodeURIComponent(lang);

  // ---------- elementos ----------
  var elLineas = document.getElementById('lineas');
  var elChip = document.getElementById('chip-conexion');
  var elReplay = document.getElementById('chip-replay');
  var elTitulo = document.getElementById('titulo-sesion');

  // ---------- estado de la conexión / append puro ----------
  var lastSeq = null;      // seq del último mensaje pintado (o de init.last_seq)
  var haveInit = false;    // ya pintamos las líneas del init (sólo la primera vez)
  var isReplay = false;    // sticky: una vez REPLAY, siempre REPLAY (brief: "visible siempre")
  var ws = null;
  var backoffMs = 1000;
  var BACKOFF_MAX = 10000;
  var reconnectTimer = null;

  function setChip(estado) {
    // estados mínimos de B1 (texto, nunca sólo color); el chip de 4 estados con
    // tolerancia de 250 ms sobre heartbeat es B8 (ver reportes/plan.md).
    var textos = { conectando: 'conectando…', conectado: 'conectado', reconectando: 'reconectando…' };
    elChip.textContent = textos[estado] || estado;
    elChip.className = 'chip chip--' + estado;
  }

  function marcarReplay() {
    if (!isReplay) {
      isReplay = true;
      elReplay.hidden = false;
    }
  }

  function agregarLinea(texto, opts) {
    opts = opts || {};
    // "append puro": el texto ya mostrado no se reescribe nunca. Lo único que cambia
    // de una línea vieja es una clase CSS (deja de ser "la nueva"), no su contenido.
    var anterior = elLineas.querySelector('.linea--nueva');
    if (anterior) anterior.classList.remove('linea--nueva');

    var div = document.createElement('div');
    div.className = 'linea' + (opts.especial ? ' linea--especial' : ' linea--nueva');

    var span = document.createElement('span');
    span.className = 'linea__texto';
    span.textContent = texto;
    div.appendChild(span);

    if (opts.marca) {
      var marca = document.createElement('span');
      marca.className = 'marca-sin-traducir';
      marca.textContent = opts.marca;
      div.appendChild(marca);
    }

    elLineas.appendChild(div);
    autoScroll();
  }

  function autoScroll() {
    // B1: scroll simple pegado al fondo en cada línea nueva.
    // El "se rinde apenas el usuario sube" + botón "volver al vivo" es B8 (plan.md).
    window.scrollTo(0, document.body.scrollHeight);
  }

  function textoAMostrar(msg) {
    if (msg.lang === lang) {
      return { texto: msg.text, sinTraducir: false };
    }
    var trad = msg.translations ? msg.translations[lang] : undefined;
    if (trad && trad.ok === true && typeof trad.text === 'string') {
      return { texto: trad.text, sinTraducir: false };
    }
    // ok:false (text:null) o directamente ausente ({} = no hay traducción todavía, caso B1):
    // mostramos el original con marca visible, tal como pide el brief.
    return { texto: msg.text, sinTraducir: true };
  }

  function normSeq(v) {
    return (typeof v === 'number') ? v : null;
  }

  function manejarMensaje(evt) {
    var msg;
    try {
      msg = JSON.parse(evt.data);
    } catch (e) {
      console.error('mensaje no-JSON del hub:', evt.data);
      return;
    }

    if (msg.replay === true) marcarReplay();

    if (msg.type === 'init') {
      var nuevoLastSeq = normSeq(msg.last_seq);
      if (elTitulo && msg.session_lang && msg.session_lang !== lang) {
        elTitulo.textContent = sessionId + ' · viendo ' + lang + ' (original ' + msg.session_lang + ')';
      }
      if (!haveInit) {
        // contracts/README.md: "lines" son los últimos 10 mensajes type=text COMPLETOS
        // (texto + traducciones), no strings sueltos: pasan por la misma selección de
        // idioma que un mensaje 'text' en vivo (si no, se pinta "[object Object]").
        // BUG encontrado por el adversario: si el hueco ya existía ANTES de que este
        // viewer se conectara (p. ej. se perdieron seq 2-4 y el historial trae seq 1 y
        // luego seq 5), hay que marcarlo IGUAL: cada item de 'lines' trae su propio
        // 'seq', así que se compara consecutivo a consecutivo, no sólo hacia adelante.
        var prevSeq = null;
        (msg.lines || []).forEach(function (item) {
          var itemSeq = normSeq(item.seq);
          if (prevSeq !== null && itemSeq !== null && itemSeq > prevSeq + 1) {
            agregarLinea('[tramo perdido]', { especial: true });
          }
          var r = textoAMostrar(item);
          agregarLinea(r.texto, { marca: r.sinTraducir ? 'sin traducir' : null });
          if (itemSeq !== null) prevSeq = itemSeq;
        });
        haveInit = true;
      } else {
        // BUG encontrado por el adversario (reportes/adversario-b1-pasada1.md) y corregido acá:
        // reconexión (haveInit ya era true). Antes sólo marcábamos "[tramo perdido]" si
        // nuevoLastSeq era distinto Y no-nulo; si el hub reconectado todavía no tenía datos
        // para esta sesión (nuevoLastSeq === null, p. ej. el hub se reinició y el worker
        // recién estaba reabriendo), la condición no se cumplía, NO se marcaba el hueco, Y
        // ADEMÁS lastSeq quedaba reseteado a null en la línea de abajo: el próximo mensaje
        // 'text' se aceptaba sin chequeo de hueco (lastSeq null) y el salto real (p. ej. 4→7,
        // visto en vivo con nerdearla-en cuando audio-pipeline tuvo intento1/intento2) quedaba
        // sin marcar. Ahora: sólo NO hay hueco si el hub prueba que nada cambió mientras
        // estuvimos desconectados (mismo last_seq, no-nulo). Cualquier otro caso -> hueco.
        var sinCambios = (lastSeq !== null && nuevoLastSeq !== null && nuevoLastSeq === lastSeq);
        if (!sinCambios) {
          agregarLinea('[tramo perdido]', { especial: true });
        }
      }
      lastSeq = nuevoLastSeq;
      return;
    }

    if (msg.type === 'heartbeat') {
      // contracts/README.md: seq siempre null en heartbeat, no participa del conteo de huecos.
      // B1: sólo evidencia de vida del socket. El chip fino de 4 estados / 250ms es B8.
      return;
    }

    // contracts/README.md: "seq monotónico POR SESIÓN, cubre todos los tipos salvo heartbeat".
    // Por eso el chequeo de hueco va ACÁ (para text/rotation/watchdog/error/session_start/
    // session_end juntos) y no sólo dentro del caso 'text': si sólo mirara los 'text', un
    // 'rotation' o 'session_start' de por medio se vería como un salto de seq y mostraría
    // un "[tramo perdido]" falso.
    var seq = normSeq(msg.seq);
    var duplicado = false;
    if (lastSeq !== null && seq !== null) {
      if (seq <= lastSeq) {
        duplicado = true; // viejo/repetido: no se reescribe nada
      } else if (seq > lastSeq + 1) {
        agregarLinea('[tramo perdido]', { especial: true });
      }
    }
    if (seq !== null) lastSeq = seq;
    if (duplicado) return;

    switch (msg.type) {
      case 'text': {
        var r = textoAMostrar(msg);
        agregarLinea(r.texto, { marca: r.sinTraducir ? 'sin traducir' : null });
        break;
      }

      case 'session_start':
      case 'session_end':
      case 'rotation':
      case 'watchdog':
      case 'error':
        console.log('[hub] evento de control:', msg.type, msg);
        break;

      default:
        console.warn('[hub] tipo de mensaje desconocido:', msg.type, msg);
    }
  }

  function conectar() {
    setChip(haveInit ? 'reconectando' : 'conectando');
    ws = new WebSocket(wsUrl);

    ws.addEventListener('open', function () {
      backoffMs = 1000;
      setChip('conectado');
    });

    ws.addEventListener('message', manejarMensaje);

    ws.addEventListener('close', function () {
      setChip('reconectando');
      programarReconexion();
    });

    ws.addEventListener('error', function (e) {
      console.error('[ws] error', e);
      // 'close' se dispara después de 'error' en el ciclo de vida del WebSocket;
      // el reintento se programa una sola vez desde 'close'.
    });
  }

  function programarReconexion() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      conectar();
    }, backoffMs);
    backoffMs = Math.min(backoffMs * 2, BACKOFF_MAX);
  }

  // ---------- arranque ----------
  if (!sessionId) {
    var aviso = document.createElement('p');
    aviso.className = 'aviso-sesion';
    aviso.textContent = 'Sesión no especificada en la URL. Usá /s/<sesion>?lang=xx.';
    elLineas.appendChild(aviso);
    elChip.textContent = 'sin sesión';
  } else {
    if (elTitulo) elTitulo.textContent = sessionId + ' · ' + lang;
    conectar();
  }
})();
