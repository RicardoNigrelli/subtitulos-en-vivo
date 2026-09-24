// app.js — vista de subtítulos de una sesión (frontend, Bloque 3)
// Contrato: v:1, type (text|rotation|watchdog|error|heartbeat|session_start|session_end|
// translation|partial|init), session_id, seq, lang (idioma ORIGINAL), text, translations,
// replay, meta. B2: translation (items [{seq,text,ok}], meta.lang_to, seq:null, no se guarda,
// el hub ya mergea en el text guardado) y partial (seq:null, no se guarda).
// B3: backfill de historial al reconectar (GET .../historial?desde=&tipos=todos, bufferizando
// los mensajes en vivo mientras tanto); las líneas pendientes de traducción esperan indefinido
// (ya no hay timeout de 15s: una traducción puede llegar después de session_end, ver README);
// título real de la sesión (init.title / session_start.meta.title / GET /api/sesiones) en la
// cabecera.
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

  function historialUrl(desde) {
    return 'http://' + hubHost + '/api/sesiones/' + encodeURIComponent(sessionId || '') +
      '/historial?desde=' + encodeURIComponent(desde) + '&tipos=todos';
  }

  // ---------- elementos ----------
  var elLineas = document.getElementById('lineas');
  var elChip = document.getElementById('chip-conexion');
  var elReplay = document.getElementById('chip-replay');
  var elTitulo = document.getElementById('titulo-sesion');
  var elSelectorIdioma = document.getElementById('selector-idioma');

  // ---------- estado de la conexión / vista (se reinicia al cambiar de idioma, ver resetVista) ----------
  var lastSeq = null;      // ultimo seq CONFIRMADO (pintado o contabilizado) de CUALQUIER tipo con
                            // seq (evita falsos huecos: rotation/watchdog/etc. tambien consumen
                            // numero, contracts/README.md "cubre todos los tipos salvo heartbeat/
                            // partial/translation"). Solo avanza hacia adelante (ver aplicarMensajeConSeq).
  var haveInit = false;    // ya pintamos las líneas del init (sólo la primera vez POR CONEXION)
  var isReplay = false;    // sticky por SESION (no se resetea al cambiar de idioma: no es un dato de la vista)
  var sessionLang = null;  // idioma ORIGINAL de la sesion (init.session_lang)
  var translationsLangs = []; // init.translations_langs (B2)
  var tituloSesionReal = null; // titulo real (init.title / session_start.meta.title / GET /api/sesiones, B3);
                                // sticky por SESION, igual que isReplay.
  var ws = null;
  var connId = 0;          // invalida handlers de un socket viejo tras resetVista (evita reconexion fantasma)
  var backoffMs = 1000;
  var BACKOFF_MAX = 10000;
  var reconnectTimer = null;
  var elParcial = null;    // nodo DOM de la unica linea parcial visible (o null)
  var pendientes = {};     // seq -> {nodo, span}: lineas en idioma traducido esperando su traduccion
  var enBackfill = false;  // B3: hay un GET .../historial en vuelo tras una reconexion
  var bufferEnVivo = [];   // B3: mensajes en vivo (ya parseados) recibidos mientras enBackfill es true,
                            // en orden de llegada; se aplican DESPUES del historial (finalizarBackfill).

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
  // Regla (documentada tambien en el reporte): al llegar un `text` sin `translations[lang]`
  // todavia, se pinta el ORIGINAL en gris como linea PENDIENTE (reemplazable, igual que un parcial).
  // Cuando llega su item de `translation` con ok:true, esa linea pasa a CONFIRMADA en blanco con el
  // texto traducido. Si llega ok:false, pasa a CONFIRMADA con el ORIGINAL (que ya estaba pintado) +
  // la marca "sin traducir". Una vez CONFIRMADA (por cualquiera de los dos caminos) la linea NUNCA
  // se vuelve a tocar: es la unica mutacion permitida (pendiente -> confirmada), consistente con
  // "las lineas confirmadas nunca se reescriben" (append puro) y con "no reescribís texto ya
  // mostrado" (brief del agente).
  // FIX B3 (pedido de backend/qa, contracts/README.md "puede llegar DESPUES del session_end"): B2
  // tenia un timeout de 15s que resolvia la linea como "sin traducir" si no llegaba nada. Un
  // reintento legitimo que llegara DESPUES de esos 15s (por ejemplo, tras el fin de la sesion,
  // como en b1-es-60s-trad.jsonl: 3 de 6 eventos translation llegan despues del session_end) se
  // hubiera perdido en silencio (pendientes[seq] ya no existia) Y ademas hubiera exigido una SEGUNDA
  // reescritura para corregirlo, prohibida por la regla de arriba. Por eso ya NO hay timeout: la
  // linea queda pendiente indefinidamente hasta que llegue su `translation` real (ok:true u
  // ok:false). Si nunca llega (el worker nunca la intento), queda gris: mejor eso que mentir con
  // una marca de "sin traducir" prematura que despues no se puede corregir.
  function agregarLineaPendiente(seq, textoOriginal) {
    var div = document.createElement('div');
    div.className = 'linea linea--pendiente';
    var span = document.createElement('span');
    span.className = 'linea__texto';
    span.textContent = textoOriginal;
    div.appendChild(span);
    elLineas.appendChild(div);
    autoScroll();
    pendientes[seq] = { nodo: div, span: span };
  }

  function resolverPendiente(seq, resultado) {
    var p = pendientes[seq];
    if (!p) return; // ya resuelta, o el item no le corresponde a ninguna linea pendiente: se ignora
                     // (contracts/README.md: "si no la tiene puede ignorarlo: cuando el text llegue,
                     // llega mergeado" — el hub ya hizo el merge del lado del texto)
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

  // ---------- seleccion de texto por idioma para UNA linea type=text (vive, de init.lines o de historial) ----------
  function procesarTexto(item) {
    limpiarParcial(); // "borrada al llegar el siguiente text final" (solo aplica si habia una, si no es no-op)
    if (lang === item.lang) {
      // vista del idioma ORIGINAL: siempre confirmada de una, nunca pendiente (no se traduce a si misma)
      agregarLinea(item.text);
      return;
    }
    var trad = item.translations ? item.translations[lang] : undefined;
    if (trad && trad.ok === true && typeof trad.text === 'string' && trad.text.length > 0) {
      // "init.lines/historial mergeadas: pintá directo la traducción si está" — mismo camino para
      // vivo, init y backfill.
      agregarLinea(trad.text);
    } else if (trad && trad.ok === false) {
      agregarLinea(item.text, { marca: 'sin traducir' });
    } else {
      // {} o ausente: todavia no hay traduccion. Pendiente hasta que llegue su translation (sin limite
      // de tiempo, ver comentario en agregarLineaPendiente).
      agregarLineaPendiente(item.seq, item.text);
    }
  }

  function normSeq(v) {
    return (typeof v === 'number') ? v : null;
  }

  // ---------- titulo real de la sesion (item 3, B3: "de /api/sesiones", visible arriba) ----------
  function actualizarTituloReal(t) {
    if (typeof t === 'string' && t && t !== tituloSesionReal) {
      tituloSesionReal = t;
      actualizarTitulo();
    }
  }

  function cargarTituloInicial() {
    // El WS ya trae el titulo (init.title, y en vivo session_start.meta.title) y gana si llega
    // despues / mas actualizado. Este fetch sirve para tenerlo YA si la sesion arranco antes de
    // que esta pestaña se conectara (brief: "Titulo de la sesion (de /api/sesiones) visible arriba").
    var miSessionId = sessionId;
    fetch('http://' + hubHost + '/api/sesiones').then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    }).then(function (lista) {
      if (miSessionId !== sessionId) return; // no deberia cambiar sin recargar, defensivo igual
      var s = (lista || []).filter(function (x) { return x.session_id === sessionId; })[0];
      if (s && s.title) actualizarTituloReal(s.title);
    }).catch(function (err) {
      console.warn('[titulo] no se pudo leer /api/sesiones:', err);
    });
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
    var base = (sessionLang && sessionLang !== lang)
      ? (sessionId + ' · ' + lang + ' (original ' + sessionLang + ')')
      : (sessionId + ' · ' + lang);
    // item 3 (B3): titulo real de la sesion, cuando ya lo sabemos, antepuesto al id/idioma.
    elTitulo.textContent = tituloSesionReal ? (tituloSesionReal + ' — ' + base) : base;
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
    enBackfill = false;
    bufferEnVivo = [];
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

  // ---------- B3: backfill de historial al reconectar (item 1) ----------
  // Al reconectar, si el nuevo `init.last_seq` no coincide con el ultimo que viamos, pedimos
  // GET /historial?desde=<ultimo seq visto>&tipos=todos (todos los tipos para no marcar un hueco
  // falso por un rotation/watchdog/etc. de por medio, igual que en vivo) MIENTRAS bufferizamos los
  // mensajes en vivo que sigan llegando por el WS (manejarMensaje). Al responder el historial, se
  // aplica en orden de seq (ya mergeado con traducciones, contracts/README.md) y DESPUES el buffer,
  // sin duplicar (aplicarMensajeConSeq ignora seq <= lastSeq). Si el hueco supera 50 lineas de texto,
  // se pintan solo las ultimas 50 con la marca "[tramo perdido: N líneas]" (N = lineas NO mostradas;
  // [SUPUESTO: frontend] el brief no precisa si N es el total o el resto: se eligio "resto" para que
  // N (perdidas) + 50 (mostradas) sumen el total real).
  function iniciarBackfill(desde) {
    enBackfill = true;
    var miConn = connId;
    fetch(historialUrl(desde)).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    }).then(function (lista) {
      if (miConn !== connId) return; // esta conexion ya no es la vigente (otro reconnect o cambio de idioma)
      aplicarListaBackfill(lista);
      finalizarBackfill(miConn);
    }).catch(function (err) {
      if (miConn !== connId) return;
      console.error('[backfill] no se pudo recuperar ' + historialUrl(desde) + ':', err);
      // No sabemos cuanto se perdio (ni si era de tipo text): marcador generico, igual que B1/B2.
      agregarLinea('[tramo perdido]', { especial: true });
      finalizarBackfill(miConn);
    });
  }

  function aplicarListaBackfill(lista) {
    lista = lista || [];
    var LIMITE = 50;
    var textos = lista.filter(function (m) { return m.type === 'text'; });
    if (textos.length > LIMITE) {
      var noMostradas = textos.length - LIMITE;
      var primeraQueSeMuestra = textos[textos.length - LIMITE].seq;
      agregarLinea('[tramo perdido: ' + noMostradas + ' líneas]', { especial: true });
      var idx = lista.findIndex(function (m) { return m.seq === primeraQueSeMuestra; });
      lista = lista.slice(idx);
      // Ya se aviso el salto arriba con el conteo exacto: que aplicarMensajeConSeq no marque OTRO
      // "[tramo perdido]" generico por la misma razon al procesar el primero de la lista recortada.
      lastSeq = lista[0].seq - 1;
    }
    lista.forEach(function (m) { aplicarMensajeConSeq(m); });
  }

  function finalizarBackfill(miConn) {
    if (miConn !== connId) return;
    enBackfill = false;
    var lote = bufferEnVivo;
    bufferEnVivo = [];
    lote.forEach(function (m) { aplicarMensajeConSeq(m); });
  }

  // ---------- procesamiento de UN mensaje ya resuelto por seq (vivo directo, historial o buffer) ----------
  function aplicarMensajeConSeq(msg) {
    if (msg.replay === true) marcarReplay();

    // contracts/README.md: "seq monotónico POR SESIÓN, cubre todos los tipos salvo heartbeat/partial/
    // translation". El chequeo de hueco va con TODOS esos tipos (para no mostrar un "[tramo perdido]"
    // falso cuando un 'rotation'/'watchdog' de por medio consumio un numero legitimamente — item 3,
    // B3), pero el MARCADOR VISIBLE solo se inserta sobre type=text: es el unico tipo que la vista
    // pinta como linea, asi que es el unico lugar donde un hueco importa.
    var seq = normSeq(msg.seq);
    var duplicado = false;
    var hayHueco = false;
    if (lastSeq !== null && seq !== null) {
      if (seq <= lastSeq) {
        duplicado = true; // viejo/repetido, o ya cubierto por el backfill: no se reescribe nada
      } else if (seq > lastSeq + 1) {
        hayHueco = true;
      }
    }
    // FIX B3: antes esta asignacion corria SIEMPRE, incluso si duplicado=true, lo que podia BAJAR
    // lastSeq con un mensaje viejo/repetido y disparar un hueco falso en el siguiente mensaje real.
    // lastSeq ahora solo avanza hacia adelante — necesario para el backfill: el historial y el buffer
    // en vivo pueden traer mensajes que se solapan con lo ya pintado (ver aplicarListaBackfill).
    if (seq !== null && !duplicado) lastSeq = seq;
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
        // Item 2 (B3): puede llegar mucho despues, incluso tras el session_end (b1-es-60s-trad.jsonl
        // trae 3 de 6 asi) — sigue funcionando porque ya no hay timeout que haya resuelto la linea antes.
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
        // item 3 (B3): titulo real de la sesion (ademas de GET /api/sesiones al arrancar).
        if (msg.meta && msg.meta.title) actualizarTituloReal(msg.meta.title);
        console.log('[hub] evento de control:', msg.type, msg);
        break;

      case 'session_end':
      case 'rotation':
      case 'watchdog':
      case 'error':
        // item 3 (B3): la audiencia NO ve estos eventos tecnicos (ni linea, ni cambio de chip:
        // el chip solo reacciona a open/close del socket, ver conectar()). Solo van a consola.
        console.log('[hub] evento de control:', msg.type, msg);
        break;

      default:
        console.warn('[hub] tipo de mensaje desconocido:', msg.type, msg);
    }
  }

  // ---------- init: primera vez (pinta lines) o reconexion (dispara backfill si hubo novedad) ----------
  function manejarInit(msg) {
    sessionLang = msg.session_lang || sessionLang;
    if (Array.isArray(msg.translations_langs)) translationsLangs = msg.translations_langs;
    if (msg.title) actualizarTituloReal(msg.title); // item 3 (B3)
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
      lastSeq = nuevoLastSeq;
    } else {
      // Reconexion (item 1, B3): antes se pintaba un "[tramo perdido]" generico ante cualquier
      // cambio. Ahora, si hubo novedad, se recupera con historial en vez de solo avisar.
      // sinCambios cubre tambien null===null (seguiamos "esperando", sin novedad real).
      var sinCambios = (nuevoLastSeq === lastSeq);
      if (!sinCambios) {
        iniciarBackfill(lastSeq !== null ? lastSeq : -1);
      }
      // OJO: lastSeq NO se pisa con nuevoLastSeq aca. Lo actualiza aplicarMensajeConSeq a medida
      // que procesa el historial y el buffer en vivo (solo hacia adelante), que es la fuente real
      // de lo que ya se pinto — nuevoLastSeq puede incluso ser MENOR si el hub perdio memoria
      // (reinicio) y todavia no re-recibio todo el backlog del productor.
    }
  }

  function manejarMensaje(evt) {
    var msg;
    try {
      msg = JSON.parse(evt.data);
    } catch (e) {
      console.error('mensaje no-JSON del hub:', evt.data);
      return;
    }

    if (msg.type === 'heartbeat') {
      // seq siempre null en heartbeat: no participa del conteo de huecos ni del backfill.
      // El chip fino de 4 estados / 250ms sobre este latido es B8.
      return;
    }

    if (msg.type === 'init') {
      manejarInit(msg);
      return;
    }

    if (enBackfill) {
      // item 1 (B3): mientras el historial esta en vuelo se bufferiza en orden de llegada; se
      // aplica DESPUES (finalizarBackfill), para no pintar un mensaje en vivo antes que el tramo
      // de historial que lo precede (rompería el orden por seq de lineas ya confirmadas).
      bufferEnVivo.push(msg);
      return;
    }

    aplicarMensajeConSeq(msg);
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
    cargarTituloInicial();
    conectar();
  }
})();
