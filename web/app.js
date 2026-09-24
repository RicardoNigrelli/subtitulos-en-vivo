// app.js — vista de subtítulos de una sesión (frontend, Bloque 2)
// Contrato: v:1, type (text|rotation|watchdog|error|heartbeat|session_start|session_end|
// translation|partial|init), session_id, seq, lang (idioma ORIGINAL), text, translations,
// replay, meta. Nuevo en B2: translation (items [{seq,text,ok}], meta.lang_to, seq:null,
// no se guarda, el hub ya mergea en el text guardado) y partial (seq:null, no se guarda).
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

  function wsUrlFor(idioma) {
    return 'ws://' + hubHost + '/ws/' + encodeURIComponent(sessionId || '') + '?lang=' + encodeURIComponent(idioma);
  }

  // ---------- elementos ----------
  var elLineas = document.getElementById('lineas');
  var elChip = document.getElementById('chip-conexion');
  var elReplay = document.getElementById('chip-replay');
  var elTitulo = document.getElementById('titulo-sesion');
  var elSelectorIdioma = document.getElementById('selector-idioma');

  // ---------- estado de la conexión / vista (se reinicia al cambiar de idioma, ver resetVista) ----------
  var lastSeq = null;      // ultimo seq visto de CUALQUIER tipo con seq (evita falsos huecos: rotation/
                            // watchdog/etc. tambien consumen numero, contracts/README.md "cubre todos
                            // los tipos salvo heartbeat/partial/translation")
  var haveInit = false;    // ya pintamos las líneas del init (sólo la primera vez POR CONEXION)
  var isReplay = false;    // sticky por SESION (no se resetea al cambiar de idioma: no es un dato de la vista)
  var sessionLang = null;  // idioma ORIGINAL de la sesion (init.session_lang)
  var translationsLangs = []; // init.translations_langs (B2)
  var ws = null;
  var connId = 0;          // invalida handlers de un socket viejo tras resetVista (evita reconexion fantasma)
  var backoffMs = 1000;
  var BACKOFF_MAX = 10000;
  var reconnectTimer = null;
  var elParcial = null;    // nodo DOM de la unica linea parcial visible (o null)
  var pendientes = {};     // seq -> {nodo, span, timer}: lineas en idioma traducido esperando su traduccion
  var PENDIENTE_TIMEOUT_MS = 15000;

  function setChip(estado) {
    // estados minimos (texto, nunca sólo color); el chip de 4 estados con tolerancia de 250ms
    // sobre el heartbeat es B8 (ver reportes/plan.md) — se mantiene tal cual venia de B1.
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

  function marcarComoNueva(nodo) {
    var anterior = elLineas.querySelector('.linea--nueva');
    if (anterior) anterior.classList.remove('linea--nueva');
    nodo.classList.add('linea--nueva');
  }

  function quitarNuevaDeTodos() {
    var anterior = elLineas.querySelector('.linea--nueva');
    if (anterior) anterior.classList.remove('linea--nueva');
  }

  function agregarLinea(texto, opts) {
    opts = opts || {};
    // "append puro": una linea confirmada, una vez pintada, no cambia mas de contenido.
    var div = document.createElement('div');
    div.className = 'linea' + (opts.especial ? ' linea--especial' : '');

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
    if (opts.especial) {
      quitarNuevaDeTodos();
    } else {
      marcarComoNueva(div);
    }
    autoScroll();
    return div;
  }

  // ---------- linea PENDIENTE (vista traducida, texto final sin su traduccion todavia) ----------
  // Regla (brief B2, documentada tambien en el reporte): al llegar un `text` sin `translations[lang]`
  // todavia, se pinta el ORIGINAL en gris como linea PENDIENTE (reemplazable, igual que un parcial).
  // Cuando llega su item de `translation` con ok:true, esa linea pasa a CONFIRMADA en blanco con el
  // texto traducido. Si llega ok:false, o si pasan 15s sin nada, pasa a CONFIRMADA con el ORIGINAL
  // (que ya estaba pintado) + la marca "sin traducir". Una vez CONFIRMADA (por cualquiera de los tres
  // caminos) la linea NUNCA se vuelve a tocar: es la unica mutacion permitida (pendiente -> confirmada),
  // consistente con "las lineas confirmadas nunca se reescriben" (append puro).
  function agregarLineaPendiente(seq, textoOriginal) {
    var div = document.createElement('div');
    div.className = 'linea linea--pendiente';
    var span = document.createElement('span');
    span.className = 'linea__texto';
    span.textContent = textoOriginal;
    div.appendChild(span);
    elLineas.appendChild(div);
    autoScroll();

    var timer = setTimeout(function () {
      resolverPendiente(seq, { marca: 'sin traducir' });
    }, PENDIENTE_TIMEOUT_MS);
    pendientes[seq] = { nodo: div, span: span, timer: timer };
  }

  function resolverPendiente(seq, resultado) {
    var p = pendientes[seq];
    if (!p) return; // ya resuelta, o el item no le corresponde a ninguna linea pendiente: se ignora
                     // (contracts/README.md: "si no la tiene puede ignorarlo: cuando el text llegue,
                     // llega mergeado" — el hub ya hizo el merge del lado del texto)
    clearTimeout(p.timer);
    delete pendientes[seq];
    p.nodo.classList.remove('linea--pendiente');
    if (resultado.texto != null) {
      p.span.textContent = resultado.texto; // unica reescritura permitida: pendiente -> confirmada
    }
    if (resultado.marca) {
      var marca = document.createElement('span');
      marca.className = 'marca-sin-traducir';
      marca.textContent = resultado.marca;
      p.nodo.appendChild(marca);
    }
    marcarComoNueva(p.nodo);
    autoScroll();
  }

  function limpiarPendientes() {
    Object.keys(pendientes).forEach(function (k) { clearTimeout(pendientes[k].timer); });
    pendientes = {};
  }

  // ---------- linea PARCIAL (solo vista del idioma original: "los parciales no se traducen") ----------
  function mostrarParcial(texto) {
    if (!elParcial) {
      elParcial = document.createElement('div');
      elParcial.className = 'linea linea--parcial';
      var span = document.createElement('span');
      span.className = 'linea__texto';
      elParcial.appendChild(span);
      elLineas.appendChild(elParcial);
    }
    elParcial.firstChild.textContent = texto;
    autoScroll();
  }

  function limpiarParcial() {
    if (elParcial) {
      elParcial.remove();
      elParcial = null;
    }
  }

  function autoScroll() {
    // B1/B2: scroll simple pegado al fondo en cada línea nueva.
    // El "se rinde apenas el usuario sube" + botón "volver al vivo" es B8 (plan.md linea 75).
    window.scrollTo(0, document.body.scrollHeight);
  }

  // ---------- seleccion de texto por idioma para UNA linea type=text (vive o de init.lines) ----------
  function procesarTexto(item) {
    limpiarParcial(); // "borrada al llegar el siguiente text final" (solo aplica si habia una, si no es no-op)
    if (lang === item.lang) {
      // vista del idioma ORIGINAL: siempre confirmada de una, nunca pendiente (no se traduce a si misma)
      agregarLinea(item.text);
      return;
    }
    var trad = item.translations ? item.translations[lang] : undefined;
    if (trad && trad.ok === true && typeof trad.text === 'string' && trad.text.length > 0) {
      // "init.lines mergeadas: pintá directo la traducción si está" — mismo camino para vivo y para init.
      agregarLinea(trad.text);
    } else if (trad && trad.ok === false) {
      agregarLinea(item.text, { marca: 'sin traducir' });
    } else {
      // {} o ausente: todavia no hay traduccion. Pendiente hasta translation/ok o 15s.
      agregarLineaPendiente(item.seq, item.text);
    }
  }

  function normSeq(v) {
    return (typeof v === 'number') ? v : null;
  }

  // ---------- selector de idioma (R6: original + translations_langs), cambia ?lang= sin recarga ----------
  function construirSelector() {
    if (!elSelectorIdioma) return;
    var idiomas = [];
    if (sessionLang) idiomas.push(sessionLang);
    (translationsLangs || []).forEach(function (xx) {
      if (idiomas.indexOf(xx) === -1) idiomas.push(xx);
    });
    elSelectorIdioma.textContent = '';
    if (idiomas.length < 2) return; // nada entre lo que elegir todavia

    idiomas.forEach(function (xx) {
      var a = document.createElement('a');
      var qs = new URLSearchParams(location.search);
      qs.set('lang', xx);
      a.href = location.pathname + '?' + qs.toString();
      a.className = 'link-idioma' + (xx === lang ? ' link-idioma--activo' : '');
      a.textContent = xx === sessionLang ? (xx + ' (original)') : xx;
      if (xx === lang) a.setAttribute('aria-current', 'page');
      a.addEventListener('click', function (ev) {
        if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        ev.preventDefault();
        cambiarIdioma(xx);
      });
      elSelectorIdioma.appendChild(a);
    });
  }

  function actualizarTitulo() {
    if (!elTitulo) return;
    elTitulo.textContent = (sessionLang && sessionLang !== lang)
      ? (sessionId + ' · ' + lang + ' (original ' + sessionLang + ')')
      : (sessionId + ' · ' + lang);
  }

  function resetVista() {
    // Cambio de idioma = vista nueva (no es "reescribir lo mostrado": es OTRA pantalla). Se cierra el
    // socket viejo (invalidando sus handlers via connId, para no disparar una reconexion fantasma sobre
    // el idioma anterior) y se limpia todo el estado para arrancar de cero con el init del idioma nuevo.
    connId += 1;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) { try { ws.close(); } catch (e) { /* noop */ } ws = null; }
    limpiarPendientes();
    elParcial = null;
    elLineas.textContent = '';
    lastSeq = null;
    haveInit = false;
    backoffMs = 1000;
  }

  function cambiarIdioma(nuevo) {
    if (nuevo === lang) return;
    lang = nuevo;
    var qs = new URLSearchParams(location.search);
    qs.set('lang', lang);
    history.pushState({ lang: lang }, '', location.pathname + '?' + qs.toString());
    construirSelector();
    actualizarTitulo();
    resetVista();
    conectar();
  }

  window.addEventListener('popstate', function () {
    var nuevo = new URLSearchParams(location.search).get('lang') || lang;
    if (nuevo === lang) return;
    lang = nuevo;
    construirSelector();
    actualizarTitulo();
    resetVista();
    conectar();
  });

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
      sessionLang = msg.session_lang || sessionLang;
      if (Array.isArray(msg.translations_langs)) translationsLangs = msg.translations_langs;
      construirSelector();
      actualizarTitulo();

      var nuevoLastSeq = normSeq(msg.last_seq);
      if (!haveInit) {
        // contracts/README.md: "lines" son los últimos 10 mensajes type=text COMPLETOS
        // (texto + traducciones ya mergeadas), no strings sueltos: pasan por procesarTexto,
        // la MISMA seleccion de idioma que un 'text' en vivo (si no, se pinta "[object Object]").
        // Huecos DENTRO del historial: cada item trae su propio 'seq', se compara consecutivo a
        // consecutivo (no solo hacia adelante), por si el hueco ya existia antes de conectarse.
        var prevSeq = null;
        (msg.lines || []).forEach(function (item) {
          var itemSeq = normSeq(item.seq);
          if (prevSeq !== null && itemSeq !== null && itemSeq > prevSeq + 1) {
            agregarLinea('[tramo perdido]', { especial: true });
          }
          procesarTexto(item);
          if (itemSeq !== null) prevSeq = itemSeq;
        });
        haveInit = true;
      } else {
        // Reconexion (haveInit ya era true): solo NO hay hueco si el hub prueba que nada cambio
        // mientras estuvimos desconectados (mismo last_seq, no-nulo). Cualquier otro caso -> hueco.
        var sinCambios = (lastSeq !== null && nuevoLastSeq !== null && nuevoLastSeq === lastSeq);
        if (!sinCambios) {
          agregarLinea('[tramo perdido]', { especial: true });
        }
      }
      lastSeq = nuevoLastSeq;
      return;
    }

    if (msg.type === 'heartbeat') {
      // seq siempre null en heartbeat: no participa del conteo de huecos.
      // El chip fino de 4 estados / 250ms sobre este latido es B8.
      return;
    }

    // contracts/README.md: "seq monotónico POR SESIÓN, cubre todos los tipos salvo heartbeat/partial/
    // translation". El chequeo de hueco va con TODOS esos tipos (para no mostrar un "[tramo perdido]"
    // falso cuando un 'rotation'/'watchdog' de por medio consumio un numero legitimamente), pero el
    // MARCADOR VISIBLE solo se inserta sobre type=text (brief B2: "hueco por seq, sólo sobre type=text"):
    // es el unico tipo que la vista pinta como linea, asi que es el unico lugar donde un hueco importa.
    var seq = normSeq(msg.seq);
    var duplicado = false;
    var hayHueco = false;
    if (lastSeq !== null && seq !== null) {
      if (seq <= lastSeq) {
        duplicado = true; // viejo/repetido: no se reescribe nada
      } else if (seq > lastSeq + 1) {
        hayHueco = true;
      }
    }
    if (seq !== null) lastSeq = seq;
    if (duplicado) return;
    if (hayHueco && msg.type === 'text') {
      agregarLinea('[tramo perdido]', { especial: true });
    }

    // Si la sesion arranco "waiting" (sin lang todavia) y este es el primer mensaje real, ahora
    // sabemos el idioma original: al menos permite que el selector aparezca sin esperar un reconnect.
    if (msg.lang && !sessionLang) {
      sessionLang = msg.lang;
      construirSelector();
      actualizarTitulo();
    }

    switch (msg.type) {
      case 'text':
        procesarTexto(msg);
        break;

      case 'partial':
        // "Sólo en la vista del idioma original (los parciales no se traducen)".
        if (msg.lang === lang) {
          mostrarParcial(msg.text || '');
        }
        break;

      case 'translation':
        // En vivo llega aparte del text (que salió antes con translations:{}). Si meta.lang_to no es
        // el idioma que estamos viendo, no nos sirve. Por item: ok:true reemplaza el texto pendiente
        // por el traducido; ok:false deja el original (ya pintado) y agrega la marca. Si la linea ya
        // no esta pendiente (resuelta antes, o el text todavia no llego: el hub ya se lo merge cuando
        // llegue) resolverPendiente no hace nada — contracts/README.md lo permite explícitamente.
        if (msg.meta && msg.meta.lang_to === lang) {
          (msg.items || []).forEach(function (item) {
            if (item.ok === true && typeof item.text === 'string' && item.text.length > 0) {
              resolverPendiente(item.seq, { texto: item.text });
            } else {
              resolverPendiente(item.seq, { marca: 'sin traducir' });
            }
          });
        }
        break;

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
    connId += 1;
    var miConn = connId;
    setChip(haveInit ? 'reconectando' : 'conectando');
    var socket = new WebSocket(wsUrlFor(lang));
    ws = socket;

    socket.addEventListener('open', function () {
      if (miConn !== connId) return;
      backoffMs = 1000;
      setChip('conectado');
    });

    socket.addEventListener('message', function (evt) {
      if (miConn !== connId) return;
      manejarMensaje(evt);
    });

    socket.addEventListener('close', function () {
      if (miConn !== connId) return;
      setChip('reconectando');
      programarReconexion();
    });

    socket.addEventListener('error', function (e) {
      if (miConn !== connId) return;
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
    actualizarTitulo();
    conectar();
  }
})();
