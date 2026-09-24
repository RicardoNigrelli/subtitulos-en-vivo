// app.js — vista de subtítulos de una sesión (frontend, Bloque 4)
// Contrato: v:1, type (text|rotation|watchdog|error|heartbeat|session_start|session_end|
// translation|partial|init), session_id, seq, lang (idioma ORIGINAL), text, translations,
// replay, meta. B2: translation (items [{seq,text,ok}], meta.lang_to, seq:null, no se guarda,
// el hub ya mergea en el text guardado) y partial (seq:null, no se guarda).
// B3: backfill de historial al reconectar (GET .../historial?desde=&tipos=todos, bufferizando
// los mensajes en vivo mientras tanto); título real de la sesión en la cabecera.
// B4 (adelantado de B8, ver skill ui-subtitulos):
//  - Mismo origen: si esta página la sirve el propio hub (hub/estaticos.py, mismo puerto que
//    /api y /ws), el front habla a location.host; si la sirve web/servir.py (puerto de dev 8101),
//    sigue yendo al hub de dev en :8100. `?hub=` pisa todo esto (ver calcularOrigen()).
//  - Chip de 4 estados con TEXTO ("en vivo" · "sin texto hace N s" · "reconectando…" ·
//    "desconectado"), con un monitor de heartbeat propio (tolerancia 250ms sobre el latido de
//    1s del hub) en vez de depender sólo de open/close del socket (ver tick()).
//  - Scroll que se rinde apenas el usuario sube, con botón "volver al vivo".
//  - Huecos de tiempo visibles: `rotation` con `meta.audio_lost_s > 5`, o un `text` cuyo
//    `audio_start` se aleja > 8s del `audio_end` anterior en la MISMA vista.
//  - Línea pendiente de traducción: tope de 120s (reloj del hub vía t_hub, no el del cliente);
//    pasado el tope, se resuelve como "sin traducir" igual que un ok:false real.
//  - Selector de idioma también se actualiza con un `session_start` en vivo (además de `init`).
// WS: ws://<host>[:8100]/ws/<session_id>?lang=<xx>
'use strict';

(function () {
  // ---------- URL: sesión desde el path, idioma e (opcional) hub desde la query ----------
  function parsePath() {
    // funciona con /s/<id> y con /s/<id>/lo-que-sea (servir.py y hub/estaticos.py reescriben
    // ambos a sesion.html)
    var m = location.pathname.match(/\/s\/([^/?#]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  var params = new URLSearchParams(location.search);
  var sessionId = parsePath();
  var lang = params.get('lang') || 'en';
  var hubParam = params.get('hub');

  // ---------- B4: mismo origen si la sirve el hub, sino el hub de dev en :8100 ----------
  // La única señal confiable del lado cliente es el PUERTO: web/servir.py (este agente, dev)
  // siempre corre en 8101; cualquier otro puerto/origen (el :8080 de este bloque, o el que
  // exponga producción/Docker con hub/estaticos.py sirviendo web/) se interpreta como "me sirve
  // el hub" y habla a location.host directo. [SUPUESTO: frontend] no hay forma de saberlo con
  // certeza sin un fetch previo (que sumaría una vuelta antes de abrir el WS); ?hub= pisa esto
  // siempre (dev contra otro hub, p. ej. uno propio para pruebas que se puede matar).
  var PUERTO_DEV_WEB = '8101';
  var mismoOrigen = !hubParam && location.protocol !== 'file:' && location.port !== PUERTO_DEV_WEB;
  var wsScheme = (mismoOrigen && location.protocol === 'https:') ? 'wss' : 'ws';
  var httpScheme = (mismoOrigen && location.protocol === 'https:') ? 'https' : 'http';
  var hubHost = hubParam || (mismoOrigen ? location.host : (location.hostname + ':8100'));

  function wsUrlFor(idioma) {
    return wsScheme + '://' + hubHost + '/ws/' + encodeURIComponent(sessionId || '') + '?lang=' + encodeURIComponent(idioma);
  }

  function historialUrl(desde) {
    return httpScheme + '://' + hubHost + '/api/sesiones/' + encodeURIComponent(sessionId || '') +
      '/historial?desde=' + encodeURIComponent(desde) + '&tipos=todos';
  }

  // ---------- elementos ----------
  var elLineas = document.getElementById('lineas');
  var elChip = document.getElementById('chip-conexion');
  var elReplay = document.getElementById('chip-replay');
  var elTitulo = document.getElementById('titulo-sesion');
  var elSelectorIdioma = document.getElementById('selector-idioma');
  var elVolverVivo = document.getElementById('volver-vivo');

  // ---------- constantes B4 (documentadas donde se usan; las de tiempo son [SUPUESTO: frontend]
  // salvo la de 250ms/1s de latido y la de 120s de traducción pendiente, que vienen del brief) ----------
  var HEARTBEAT_PERIODO_MS = 1000;   // contracts/README.md: el hub late cada 1s
  var HEARTBEAT_TOLERANCIA_MS = 250; // brief B4: tolerancia sobre el latido, sin parpadeo con jitter < 250ms
  var UMBRAL_SIN_TEXTO_S = 15;       // brief B4: "> 15 s sin text"
  var UMBRAL_DESCONECTADO_MS = 5000; // [SUPUESTO: frontend] caído más de esto: "desconectado" en vez de "reconectando…"
  var TOPE_PENDIENTE_S = 120;        // brief B4: tope de espera de una traducción
  var UMBRAL_GAP_AUDIO_S = 8;        // brief B4: audio_start - audio_end anterior > 8s
  var UMBRAL_ROTATION_AUDIO_LOST_S = 5; // brief B4: meta.audio_lost_s > 5
  var LANG_DESTINO_RE = /^[a-z]{2}(-[A-Z]{2})?$/;

  // ---------- estado de la conexión / vista (se reinicia al cambiar de idioma, ver resetVista) ----------
  var lastSeq = null;      // ultimo seq CONFIRMADO (pintado o contabilizado) de CUALQUIER tipo con
                            // seq (evita falsos huecos: rotation/watchdog/etc. tambien consumen
                            // numero, contracts/README.md "cubre todos los tipos salvo heartbeat/
                            // partial/translation"). Solo avanza hacia adelante (ver aplicarMensajeConSeq).
  var haveInit = false;    // ya pintamos las líneas del init (sólo la primera vez POR CONEXION)
  var isReplay = false;    // sticky por SESION (no se resetea al cambiar de idioma: no es un dato de la vista)
  var sessionLang = null;  // idioma ORIGINAL de la sesion (init.session_lang)
  var translationsLangs = []; // init.translations_langs + session_start.meta.translations_langs en vivo (B4)
  var tituloSesionReal = null; // titulo real (init.title / session_start.meta.title / GET /api/sesiones, B3);
                                // sticky por SESION, igual que isReplay.
  var ws = null;
  var connId = 0;          // invalida handlers de un socket viejo tras resetVista (evita reconexion fantasma)
  var backoffMs = 1000;
  var BACKOFF_MAX = 10000;
  var reconnectTimer = null;
  var elParcial = null;    // nodo DOM de la unica linea parcial visible (o null)
  var pendientes = {};     // seq -> {nodo, span, creadoTHub}: lineas en idioma traducido esperando su traduccion
  var enBackfill = false;  // B3: hay un GET .../historial en vuelo tras una reconexion
  var bufferEnVivo = [];   // B3: mensajes en vivo (ya parseados) recibidos mientras enBackfill es true,
                            // en orden de llegada; se aplican DESPUES del historial (finalizarBackfill).

  // ---------- B4: monitor de conexión (chip de 4 estados) ----------
  // Todo en reloj del HUB (t_hub, epoch segundos) anclado con Date.now() local sólo para
  // extrapolar entre latidos: así "sin texto hace N s" no depende de que el reloj del cliente
  // esté sincronizado con el del hub, sólo de que no se desvíe demasiado EN LOS ~250ms-1s entre
  // ticks (ver tick()).
  var wsAbierto = false;
  var ultimoHeartbeatLocalMs = null; // Date.now() de la última vez que "sentimos" un latido (o el open)
  var ultimoHeartbeatTHub = null;    // t_hub de ese latido (o del init/open_ok más reciente)
  var ultimoTextoTHub = null;        // t_hub del último `text` aplicado en ESTA vista (o del init si nunca hubo)
  var ultimoAudioEnd = null;         // audio_end del último `text` aplicado en ESTA vista (huecos > 8s)
  var cayendoDesde = null;           // Date.now() de cuando se detectó la caída (reconectando -> desconectado)
  var tickTimer = null;

  // ---------- B4: scroll que se rinde ----------
  var siguiendoAlFondo = true;

  function setChip(estado, n) {
    // 4 estados, siempre con TEXTO (nunca sólo color, skill ui-subtitulos):
    var textos = {
      'en-vivo': 'en vivo',
      'sin-texto': 'sin texto hace ' + n + ' s',
      'reconectando': 'reconectando…',
      'desconectado': 'desconectado'
    };
    var texto = textos[estado] || estado;
    if (elChip.textContent !== texto) elChip.textContent = texto;
    var clase = 'chip chip--' + estado;
    if (elChip.className !== clase) elChip.className = clase;
  }

  // Recalcula el estado del chip cada 250ms: heartbeat sano (con tolerancia) decide entre
  // "en vivo"/"sin texto hace N s"; si no, "reconectando…" primero y "desconectado" si la caída
  // sigue más de UMBRAL_DESCONECTADO_MS. También barre las líneas pendientes vencidas (120s).
  function tick() {
    var ahora = Date.now();
    var sano = false;
    if (wsAbierto) {
      if (ultimoHeartbeatLocalMs !== null && (ahora - ultimoHeartbeatLocalMs) > (HEARTBEAT_PERIODO_MS + HEARTBEAT_TOLERANCIA_MS)) {
        // el latido dejo de llegar a horario: puede ser un socket "zombie" (el navegador todavia
        // lo ve abierto pero el hub murio sin cerrar prolijo). Se fuerza el cierre para que
        // dispare la reconexion real (si no, quedaria mintiendo "en vivo" para siempre).
        wsAbierto = false;
        if (ws) { try { ws.close(); } catch (e) { /* noop */ } }
      } else {
        sano = true;
      }
    }
    if (sano) {
      cayendoDesde = null;
      var segSinTexto = null;
      if (ultimoTextoTHub !== null && ultimoHeartbeatTHub !== null) {
        segSinTexto = (ultimoHeartbeatTHub - ultimoTextoTHub) + (ahora - ultimoHeartbeatLocalMs) / 1000;
      }
      if (segSinTexto === null || segSinTexto <= UMBRAL_SIN_TEXTO_S) {
        setChip('en-vivo');
      } else {
        setChip('sin-texto', Math.max(0, Math.round(segSinTexto)));
      }
    } else {
      if (cayendoDesde === null) cayendoDesde = ahora;
      setChip((ahora - cayendoDesde) >= UMBRAL_DESCONECTADO_MS ? 'desconectado' : 'reconectando');
    }
    purgarPendientesVencidos(ahora);
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

  // ---------- B4: huecos de tiempo (audio sin texto), distintos del hueco por seq ----------
  // "[tramo sin texto: N s]": una charla real puede quedarse callada un rato (pausa, corte,
  // reapertura del watchdog) sin que se pierda NINGÚN mensaje (el seq sigue continuo) — por eso
  // esto es un marcador aparte de "[tramo perdido]" (que es por seq discontinuo, aplicarMensajeConSeq).
  function marcarHuecoAudio(segundos) {
    agregarLinea('[tramo sin texto: ' + Math.round(segundos) + ' s]', { especial: true });
  }

  // Al llegar un `text`: si el audio saltó más de 8s desde el fin del anterior, marca el hueco
  // ANTES de pintar la línea nueva (el hueco queda "entre" las dos). Aplica en vivo, backfill e
  // init.lines por igual: procesarTexto() es el único camino para pintar un `text` (item 4, B4).
  function marcarGapAudioSiCorresponde(item) {
    if (ultimoAudioEnd !== null && typeof item.audio_start === 'number' &&
        (item.audio_start - ultimoAudioEnd) > UMBRAL_GAP_AUDIO_S) {
      marcarHuecoAudio(item.audio_start - ultimoAudioEnd);
    }
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
  // B3: sin timeout (una traduccion real puede llegar despues de session_end).
  // B4 (pedido del adversario en el cierre de B3: "estado terminal para lineas pendientes"): tope
  // de 120s en reloj del HUB (creadoTHub, el t_hub del `text` que abrió la línea pendiente) contra
  // el t_hub del latido más reciente (ver tick()/purgarPendientesVencidos). Pasado el tope, se
  // resuelve exactamente como un ok:false real: "sin traducir" con el original ya pintado.
  function agregarLineaPendiente(seq, textoOriginal, creadoTHub) {
    var div = document.createElement('div');
    div.className = 'linea linea--pendiente';
    var span = document.createElement('span');
    span.className = 'linea__texto';
    span.textContent = textoOriginal;
    div.appendChild(span);
    elLineas.appendChild(div);
    autoScroll();
    pendientes[seq] = { nodo: div, span: span, creadoTHub: (typeof creadoTHub === 'number' ? creadoTHub : null) };
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

  // B4: barrido de líneas pendientes que superaron TOPE_PENDIENTE_S, en reloj del hub (ver
  // comentario de agregarLineaPendiente). ahoraLocalMs es el Date.now() del tick que llama esto.
  function purgarPendientesVencidos(ahoraLocalMs) {
    if (ultimoHeartbeatTHub === null || ultimoHeartbeatLocalMs === null) return;
    var ahoraTHub = ultimoHeartbeatTHub + (ahoraLocalMs - ultimoHeartbeatLocalMs) / 1000;
    Object.keys(pendientes).forEach(function (seqStr) {
      var p = pendientes[seqStr];
      if (p.creadoTHub === null) return; // sin ancla real (no debería pasar: todo text trae t_hub)
      if ((ahoraTHub - p.creadoTHub) > TOPE_PENDIENTE_S) {
        resolverPendiente(Number(seqStr), { marca: 'sin traducir' });
      }
    });
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

  // ---------- B4: scroll pegado al fondo que se rinde ----------
  function estaCercaDelFondo() {
    var margen = 48; // px de tolerancia (evita que un redondeo de 1px deje "siguiendo" en false)
    return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - margen);
  }

  function autoScroll() {
    if (siguiendoAlFondo) {
      window.scrollTo(0, document.body.scrollHeight);
    }
  }

  function mostrarVolverVivo() {
    if (elVolverVivo) elVolverVivo.hidden = false;
  }

  function ocultarVolverVivo() {
    if (elVolverVivo) elVolverVivo.hidden = true;
  }

  // El usuario sube: el scroll se rinde (deja de seguir) y aparece el botón. Si vuelve a llegar
  // al fondo por su cuenta (scrollea de nuevo hasta abajo), retoma solo, sin esperar al botón.
  function alScrollear() {
    if (estaCercaDelFondo()) {
      if (!siguiendoAlFondo) {
        siguiendoAlFondo = true;
        ocultarVolverVivo();
      }
    } else if (siguiendoAlFondo) {
      siguiendoAlFondo = false;
      mostrarVolverVivo();
    }
  }

  function volverAlVivo() {
    siguiendoAlFondo = true;
    ocultarVolverVivo();
    window.scrollTo(0, document.body.scrollHeight);
  }

  // ---------- seleccion de texto por idioma para UNA linea type=text (vive, de init.lines o de historial) ----------
  function procesarTexto(item) {
    limpiarParcial(); // "borrada al llegar el siguiente text final" (solo aplica si habia una, si no es no-op)
    marcarGapAudioSiCorresponde(item); // B4, ANTES de pintar la linea nueva
    if (typeof item.t_hub === 'number') ultimoTextoTHub = item.t_hub; // B4: ancla del chip ("texto reciente")
    if (typeof item.audio_end === 'number') ultimoAudioEnd = item.audio_end; // B4: ancla del proximo gap-check

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
      // {} o ausente: todavia no hay traduccion. Pendiente hasta que llegue su translation, con
      // tope de 120s en reloj del hub (ver agregarLineaPendiente/purgarPendientesVencidos, B4).
      agregarLineaPendiente(item.seq, item.text, item.t_hub);
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
    fetch(httpScheme + '://' + hubHost + '/api/sesiones').then(function (resp) {
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

  // B4: translations_langs también puede llegar en vivo por session_start.meta.translations_langs
  // (contracts/README.md "Índice de sesiones", B4), no sólo por `init`. Válida contra el mismo
  // patrón que usa el hub (^[a-z]{2}(-[A-Z]{2})?$) antes de sumarlo.
  function agregarIdiomasDestino(valor) {
    var lista = Array.isArray(valor) ? valor : (typeof valor === 'string' ? [valor] : []);
    var cambio = false;
    lista.forEach(function (xx) {
      if (typeof xx === 'string' && LANG_DESTINO_RE.test(xx) && translationsLangs.indexOf(xx) === -1) {
        translationsLangs.push(xx);
        cambio = true;
      }
    });
    if (cambio) construirSelector();
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
    // B4: estado del chip / monitor de heartbeat, y del hueco por audio — vista nueva, ancla nueva.
    wsAbierto = false;
    ultimoHeartbeatLocalMs = null;
    ultimoHeartbeatTHub = null;
    ultimoTextoTHub = null;
    ultimoAudioEnd = null;
    cayendoDesde = null;
    siguiendoAlFondo = true;
    ocultarVolverVivo();
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
        // B4: translations_langs puede venir en el propio session_start (contracts/README.md,
        // "Índice de sesiones"), sin esperar la 1a traduccion ni un reconnect para verlo en init.
        if (msg.meta) agregarIdiomasDestino(msg.meta.translations_langs);
        console.log('[hub] evento de control:', msg.type, msg);
        break;

      case 'rotation':
        // B4 (item 4): hueco de audio grande por una reapertura (atasco/cierre) que el worker no
        // pudo cubrir con el reenvio de solape. meta.audio_lost_s es una cota superior calculada
        // por worker/session.py (_audio_perdido); 0 en preventiva/GoAway (la vieja drena sola).
        if (msg.meta && typeof msg.meta.audio_lost_s === 'number' && msg.meta.audio_lost_s > UMBRAL_ROTATION_AUDIO_LOST_S) {
          marcarHuecoAudio(msg.meta.audio_lost_s);
          var rango = msg.meta.audio_lost_rango;
          if (Array.isArray(rango) && typeof rango[1] === 'number') {
            // no dejar que el proximo `text` marque el MISMO hueco dos veces por el chequeo de
            // audio_start - audio_end anterior (marcarGapAudioSiCorresponde).
            ultimoAudioEnd = (ultimoAudioEnd === null) ? rango[1] : Math.max(ultimoAudioEnd, rango[1]);
          }
        }
        console.log('[hub] evento de control:', msg.type, msg);
        break;

      case 'session_end':
      case 'watchdog':
      case 'error':
        // item 3 (B3): la audiencia NO ve estos eventos tecnicos (ni linea, ni cambio de chip:
        // el chip reacciona al heartbeat/socket, ver tick()). Solo van a consola.
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

    // B4: init como ancla de heartbeat (t_hub llega en cada init, hub/nucleo.py `suscribir`) —
    // cubre el hueco entre 'open' y el primer heartbeat real (~1s). Si todavia no vimos NINGUN
    // `text` en esta vista, arranca el reloj de "sin texto" desde el propio init (sesion en
    // silencio ANTES de que nos conectemos: mejor decir "sin texto hace Ns" que "en vivo" a ciegas).
    if (typeof msg.t_hub === 'number') {
      ultimoHeartbeatTHub = msg.t_hub;
      ultimoHeartbeatLocalMs = Date.now();
      if (ultimoTextoTHub === null) ultimoTextoTHub = msg.t_hub;
    }

    var nuevoLastSeq = normSeq(msg.last_seq);
    if (!haveInit) {
      // contracts/README.md: "lines" son los últimos 10 mensajes type=text COMPLETOS
      // (texto + traducciones ya mergeadas), no strings sueltos: pasan por procesarTexto,
      // la MISMA seleccion de idioma que un 'text' en vivo (si no, se pinta "[object Object]").
      // FIX B4 (encontrado con el propio test del item 4: contracts/ejemplos + un rotation
      // sintetico entre dos text): `lines` sólo trae type=text (contracts/README.md, "init"), así
      // que un `rotation`/`watchdog`/`error` legítimo entre dos líneas de texto consume un seq
      // sin aparecer en `lines` — comparar el seq consecutivo DE `lines` (B3) marcaba un
      // "[tramo perdido]" falso ahí. La detección real de huecos (con TODOS los tipos, sin falsos
      // positivos) ya la hacen aplicarMensajeConSeq/backfill para todo lo que llega después de
      // este primer pintado; acá sólo se pinta lo que mandó el hub, sin inferir de más.
      (msg.lines || []).forEach(function (item) { procesarTexto(item); });
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
      // B4: ancla del monitor de conexion (chip de 4 estados, ver tick()).
      if (typeof msg.t_hub === 'number') {
        ultimoHeartbeatTHub = msg.t_hub;
        ultimoHeartbeatLocalMs = Date.now();
      }
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
    var socket = new WebSocket(wsUrlFor(lang));
    ws = socket;

    socket.addEventListener('open', function () {
      if (miConn !== connId) return;
      backoffMs = 1000;
      wsAbierto = true;
      // ancla provisoria hasta que llegue el init (que trae t_hub real): evita que tick() vea
      // "vencido" en la fraccion de segundo entre el open y el primer mensaje.
      ultimoHeartbeatLocalMs = Date.now();
    });

    socket.addEventListener('message', function (evt) {
      if (miConn !== connId) return;
      manejarMensaje(evt);
    });

    socket.addEventListener('close', function () {
      if (miConn !== connId) return;
      wsAbierto = false;
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
    setChip('reconectando'); // estado inicial: se está por intentar la primera conexión
    window.addEventListener('scroll', alScrollear, { passive: true });
    if (elVolverVivo) elVolverVivo.addEventListener('click', volverAlVivo);
    tickTimer = setInterval(tick, 250);
    conectar();
  }
})();
