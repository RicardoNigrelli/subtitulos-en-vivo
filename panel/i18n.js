// i18n.js — diccionario ES/EN del panel de producción (monitor, R8e), sin dependencias.
//
// Pedido de Ricardo (bloque config/idioma, 25/09): un botón "ES | EN" que cambia TODOS los textos
// del panel (cabecera, switch, resumen, estados de tarjetas, "qué hacer", ayuda, configuración,
// tabla técnica y encabezados, pie). Persistido en localStorage; por defecto `navigator.language`
// (es* -> es, resto -> en); `?locale=en|es` en la URL lo fuerza para esa carga.
//
// Decisión documentada (panel/README.md): las etiquetas LIVE / REPLAY / TEST y los nombres de motivo
// de rotación (cierre/atasco/preventiva/goaway, valores literales del contrato) NO se traducen: son
// jerga de broadcast/contrato ya en uso igual en los dos idiomas, igual que `ok:true`/`ok:false`,
// `type=text`, nombres de campo, rutas de archivo y `?hub=host:puerto`.
'use strict';

(function (global) {
  var LOCALE_KEY = 'panelLocale';

  var DICT = {
    es: {
      titulo_pagina: 'Panel de producción — vibeathon',
      titulo: 'Panel de producción',
      subtitulo: 'control de sesiones en vivo',
      vista_informativa: 'Informativa',
      vista_tecnica: 'Técnica',
      vista_aria_label: 'Vista del panel',

      btn_config: 'Configuración',
      btn_salas: 'Salas',
      btn_locale_aria: 'Idioma del panel',

      ayuda_aria_informativa: 'Cómo leer el panel',
      ayuda_aria_tecnica: 'Cómo leer este panel',
      ayuda_titulo_informativa: 'Cómo leer el panel',
      ayuda_titulo_tecnica: 'Cómo leer este panel',
      ayuda_cerrar: 'Cerrar ayuda',

      // Lista escaneable (pedido de Ricardo vía orquestador): una fila por estado, MISMO color e
      // ícono que la tarjeta correspondiente (ver panel/app.js construirAyudaInformativa()), 1 línea
      // de explicación por fila; abajo, 3 bullets breves; al pie, en gris, la referencia a la vista
      // Técnica. Reusa los tokens de color existentes (verde/rojo/ámbar/gris/neutro), sin paleta nueva.
      ayuda_fila_sana_titulo: 'Al día / Replay',
      ayuda_fila_sana_texto: 'Todo funciona bien.',
      ayuda_fila_mudo_titulo: 'Sin texto todavía / Sin texto hace N s',
      ayuda_fila_mudo_texto: 'La sala lleva más de 30 s en vivo sin mostrar nada, o dejó de mostrar texto por más de 20 s.',
      ayuda_fila_rotacion_titulo: 'Se trabó y se reabrió',
      ayuda_fila_rotacion_texto: 'Tuvo un problema técnico y se recuperó sola.',
      ayuda_fila_traduccion_titulo: 'Traducción fallando',
      ayuda_fila_traduccion_texto: 'Varias de las últimas traducciones no salieron.',
      ayuda_fila_reconectando_titulo: 'Reconectando',
      ayuda_fila_reconectando_texto: 'Este panel perdió la conexión con esa sala y está reintentando.',
      ayuda_fila_terminada_titulo: 'Terminada',
      ayuda_fila_terminada_texto: 'La sesión ya cerró.',
      ayuda_bullet_urgencia: 'Las tarjetas se ordenan por urgencia: lo que hay que atender aparece primero.',
      ayuda_bullet_quehacer: '«Qué hacer» es una sugerencia concreta, no una orden.',
      ayuda_bullet_voz: 'Las alarmas también suenan en voz alta (se activan/desactivan y se les baja el volumen en Configuración → Avisos).',
      ayuda_bullet_salas: 'El botón «Salas» (cabecera) crea, arranca, detiene y borra sesiones contra el servicio de control, sin usar la terminal.',
      ayuda_pie_tecnico: 'Detalles técnicos en la vista Técnica.',
      ayuda_contenido_tecnica:
        'Estado <code>live</code> con más de <code>15 s</code> sin un <code>type=text</code> nuevo se ' +
        'muestra como "sin texto hace N s" (umbral documentado, brief B3). p50/p95 = <b>nearest-rank</b>, ' +
        'nunca promedio; con menos de <code>10</code> muestras se marca "n insuficiente". El trazo junto ' +
        'al p50/p95 es un <b>sparkline</b> de las últimas 20 muestras: es un adorno, no reemplaza los ' +
        'números. "grabada" = <code>t_emit − t_captured</code> tal como quedó en el casete (sesiones ' +
        '<code>replay:true</code>); en sesiones en vivo esa misma resta es la latencia real del worker y ' +
        'se agrega además la <b>percibida</b> = <code>t_receive − t_captured</code> medida en el ' +
        'WebSocket propio de este panel. "Parciales/60 s" es una ventana deslizante, no un promedio de ' +
        'sesión completa. Segundos de audio: <b>estimación</b> (viene de ' +
        '<code>heartbeat.meta.audio_seconds_sent</code> del worker, sólo visible con token vía ' +
        '<code>Authorization: Bearer</code> a <code>/api/metricas</code>; sin token esa columna dice ' +
        '"sin token" y el resto de la fila sigue funcionando). El token se configura desde el botón de ' +
        'engranaje (Configuración → Acceso), se guarda en <code>sessionStorage</code> (nunca en la URL ' +
        'ni en la query string) y viaja sólo en la cabecera de la petición; <code>?hub=</code> sólo se ' +
        'acepta si apunta a localhost/127.0.0.1/el mismo host que sirve esta página — cualquier otro ' +
        'valor se ignora y se avisa arriba, para no filtrar el token a un host ajeno. Sesiones con ' +
        '<code>test:true</code> (fixtures/transporte de casete, no ASR en vivo) llevan el rótulo ' +
        '<b>TEST</b> y están OCULTAS por defecto: "mostrar pruebas" (Configuración → Vista) las trae de ' +
        'vuelta. <code>rotation.meta.audio_lost_s</code>, cuando el worker lo manda, se acumula por ' +
        'sesión y aparece junto a "Rotaciones"; el color de cada motivo (cierre/atasco/preventiva/' +
        'goaway) es un clasificador visual, el texto del motivo siempre está al lado. La franja de ' +
        'color a la izquierda de cada fila resume el estado de esa sesión (vivo/mudo/idle/ended/' +
        'esperando), igual que el texto de la columna Estado. El botón <b>Salas</b> habla con un ' +
        'servicio de control aparte (<code>ops/control.py</code>), NO con el hub: usa el MISMO token ' +
        'de Configuración → Acceso y, por defecto, <code>http://&lt;host&gt;:8110</code> ' +
        '(<code>?control=host:puerto</code> lo cambia con la misma validación que <code>?hub=</code>).',

      config_titulo: 'Configuración',
      config_cerrar: 'Cerrar configuración',
      config_seccion_acceso: 'Acceso',
      config_token_label: 'token /api/metricas',
      config_token_guardar: 'guardar',
      config_token_borrar: 'borrar',
      config_metricas_sin_token: 'métricas: sin token',
      config_metricas_con_token: 'métricas: con token',
      config_metricas_error: 'métricas: con token — error: {detalle}',
      config_seccion_vista: 'Vista',
      config_mostrar_pruebas: 'mostrar pruebas',
      config_seccion_avisos: 'Avisos',
      config_aviso_voz: 'aviso con voz',
      config_volumen: 'volumen',
      config_probar_aviso: 'probar aviso',
      voz_prueba: 'Este es un aviso de prueba del panel.',

      salas_titulo: 'Salas',
      salas_cerrar: 'Cerrar salas',
      salas_seccion_lista: 'Salas activas',
      salas_vacio: 'Todavía no hay salas. Creá la primera.',
      salas_seccion_nueva: 'Nueva sala',
      salas_campo_titulo: 'Nombre visible',
      salas_campo_id: 'id',
      salas_campo_id_ayuda: 'Se sugiere solo desde el nombre; editable. Minúsculas, dígitos, "-" y "_".',
      salas_campo_lang: 'Idioma de la charla',
      salas_campo_traducir_a: 'Traducir a',
      salas_aviso_idiomas_extra: 'Cada idioma extra suma llamadas al modelo de traducción.',
      salas_campo_fuente: 'Fuente',
      salas_fuente_mic: 'Micrófono',
      salas_fuente_archivo: 'Clip de prueba',
      salas_fuente_url: 'Stream',
      salas_actualizar: 'actualizar',
      salas_fuente_url_ayuda: 'udp://, srt://, rtmp://',
      salas_campo_key: 'Key',
      salas_campo_duracion: 'Duración (s, opcional)',
      salas_campo_arrancar: 'arrancar ahora',
      salas_crear: 'Crear sala',
      salas_copiar_comando: 'copiar comando',
      salas_copiado: 'copiado',
      salas_servicio_no_responde: 'El servicio de control no está corriendo: {comando}',
      salas_falta_token: 'Para crear y controlar salas, pegá el token del hub (HUB_TOKEN). Es el mismo que en Configuración.',
      salas_token_label: 'Token del hub',
      salas_token_placeholder: 'Pegá el token',
      salas_token_guardar: 'Guardar',
      salas_sin_probar: '(sin probar en vivo)',
      salas_sin_microfonos: 'No se detectaron micrófonos.',
      salas_sin_archivos: 'No hay clips en fixtures/audio/clips/.',
      salas_fuente_mic_legible: 'Micrófono: {valor}',
      salas_fuente_archivo_legible: 'Clip: {valor}',
      salas_fuente_url_legible: 'Stream: {valor}',
      salas_estado_detenida: 'Detenida',
      salas_estado_arrancando: 'Arrancando',
      salas_estado_corriendo: 'Corriendo',
      salas_estado_reiniciando: 'Reiniciando',
      salas_estado_deteniendo: 'Deteniendo',
      salas_estado_error: 'Error: {detalle}',
      salas_btn_iniciar: 'Iniciar',
      salas_btn_detener: 'Detener',
      salas_btn_borrar: 'Borrar',
      salas_ver_sala: 'Ver sala',
      salas_confirmar_borrar: '¿Borrar la sala "{titulo}"? No se puede deshacer.',
      salas_sin_traduccion: 'sin traducción',
      salas_duracion_sin_limite: 'sin límite (modo sala)',
      salas_duracion_valor: '{n} s',
      salas_error_generico: 'No se pudo completar: {detalle}',
      salas_error_crear: 'No se pudo crear la sala: {detalle}',
      salas_intentos: '{n} intentos',
      salas_pid: 'pid {pid}',
      salas_id_repetido_sugerencia: 'Ya existe una sala con ese id; se sugirió uno nuevo.',
      salas_escuchar: 'Escuchar',
      salas_fuente_mic_en_vivo: 'Escuchás la sala en vivo.',
      salas_ver_original: 'Ver original en YouTube (desde {m})',

      pruebas_sin: 'sin sesiones TEST',
      pruebas_singular: 'sesión TEST',
      pruebas_plural: 'sesiones TEST',
      pruebas_mostradas: '(mostradas)',
      pruebas_ocultas: '(ocultas; "mostrar pruebas" las trae)',

      chip_hub_conectando: 'hub: conectando…',
      chip_hub_ok: 'hub: ok ({n} sesiones)',
      chip_hub_sin_respuesta: 'hub: SIN RESPUESTA',
      sin_datos_aun: 'sin datos aún',
      actualizado: 'actualizado {hora}',
      chip_conexion: 'conexión panel: {n}/{total} en vivo',
      chip_hub_seguridad: 'hub externo ignorado por seguridad: "{valor}"',
      chip_control_seguridad: 'control externo ignorado por seguridad: "{valor}"',
      aviso_sin_respuesta: 'No se pudo leer {url} ({detalle}). Los contadores que se ven quedaron en el último valor conocido.',

      resumen_en_vivo: 'en vivo',
      resumen_en_replay: 'en replay',
      resumen_a_atender: 'a atender',
      resumen_sin_problemas: 'sin problemas',
      resumen_ultimo_dato: 'último dato:',
      tarjetas_vacio: 'Esperando la primera respuesta de /api/sesiones…',

      estado_terminada: 'Terminada',
      estado_sin_texto_nunca: 'Sin texto todavía (hace {s} s)',
      estado_sin_texto_alarma: 'Sin texto hace {s} s',
      estado_rotacion: 'Se trabó y se reabrió{perdido}',
      estado_rotacion_perdido: ' ({n} s perdidos)',
      estado_traduccion: 'Traducción fallando ({f} de {n})',
      estado_reconectando: 'Reconectando',
      estado_replay: 'Replay',
      estado_al_dia: 'Al día',
      estado_inactiva: 'Inactiva',
      estado_esperando: 'esperando datos',
      estado_en_vivo: 'en vivo',
      estado_en_vivo_sin_texto: 'en vivo (sin texto todavía)',
      estado_mudo: 'sin texto hace {s} s',

      accion_prefijo: 'Qué hacer:',
      accion_sin_texto_nunca: 'Verificar el audio de la sala (¿el micrófono o el archivo está enviando?).',
      accion_sin_texto_alarma: 'Verificar el audio de la sala; si sigue, revisar worker.log.',
      accion_rotacion_atasco: 'Se recuperó sola. Si pasa seguido con otra sala del mismo proyecto, arrancarlas con 20 s de diferencia (ver README) y revisar worker.log.',
      accion_rotacion_cierre: 'Se recuperó sola. Si pasa seguido, revisar worker.log.',
      accion_traduccion: 'Ver worker.log; puede ser un límite de cuota de traducción.',
      accion_reconectando: 'Esperando reconexión con el servidor. Si sigue más de un minuto, revisar el hub o la red.',

      espectador_uno: '{n} espectador',
      espectador_varios: '{n} espectadores',

      voz_nunca_texto: 'Atención. La sala {sala} está en vivo hace más de treinta segundos y todavía no muestra texto.',
      voz_silencio: 'Atención. La sala {sala} no muestra texto hace más de {s} segundos.',

      th_sesion: 'Sesión',
      th_idioma: 'Idioma',
      th_estado: 'Estado',
      th_ultimo_seq: 'Último seq',
      th_textos: 'Textos',
      th_lat_worker: 'Latencia worker (p50 / p95)',
      th_lat_percibida: 'Latencia percibida (p50 / p95)',
      th_rotaciones: 'Rotaciones',
      th_watchdog: 'Watchdog reaperturas',
      th_errores: 'Errores',
      th_trad_okfalse: 'Traducciones ok:false',
      th_parciales: 'Parciales / 60 s',
      th_audio: 'Audio enviado (estimación)',
      th_espectadores: 'Espectadores',

      trad_prefijo: 'trad:',
      lat_grabada_sub: 'grabada (t_emit − t_captured)',
      lat_worker_sub: 't_emit − t_captured',
      lat_percibida_sub: 't_receive − t_captured (WS propio)',
      lat_no_aplica_replay: 'no aplica (replay)',
      sin_muestras: 'sin muestras',
      n_insuficiente: 'n insuficiente (n={n})',
      ok_true_igual: 'ok:true = {n}',
      watchdog_eventos_total: '{n} eventos watchdog en total',
      audio_perdido: 'audio perdido {s}',
      sparkline_titulo: 'últimas {n} muestras: {min} a {max}',
      audio_sin_heartbeat: 'sin heartbeat con audio_seconds_sent',
      audio_estimacion: '(estimación)',
      audio_latido_hace: 'latido hace {n} s',
      ultimo_prefijo: 'últ. {hora}',

      pie_fuente: 'Fuente:',
      pie_resto: 'refresco de /api/sesiones cada 2 s · reconciliación de historial cada 2 s · ' +
        're-render cada 1 s. Override de hub: agregá ?hub=host:puerto a esta URL (sólo localhost/' +
        '127.0.0.1/este mismo host). Consola del navegador sin errores es parte de la evidencia de ' +
        'cierre (ver reportes/monitor-b10.md).'
    },

    en: {
      titulo_pagina: 'Production panel — vibeathon',
      titulo: 'Production panel',
      subtitulo: 'live session control',
      vista_informativa: 'Overview',
      vista_tecnica: 'Technical',
      vista_aria_label: 'Panel view',

      btn_config: 'Settings',
      btn_salas: 'Rooms',
      btn_locale_aria: 'Panel language',

      ayuda_aria_informativa: 'How to read the panel',
      ayuda_aria_tecnica: 'How to read this panel',
      ayuda_titulo_informativa: 'How to read the panel',
      ayuda_titulo_tecnica: 'How to read this panel',
      ayuda_cerrar: 'Close help',

      ayuda_fila_sana_titulo: 'Up to date / Replay',
      ayuda_fila_sana_texto: 'Everything is working fine.',
      ayuda_fila_mudo_titulo: 'No text yet / No text for N s',
      ayuda_fila_mudo_texto: 'The room has been live for more than 30 s without showing anything, or stopped showing text for more than 20 s.',
      ayuda_fila_rotacion_titulo: 'Got stuck and reopened',
      ayuda_fila_rotacion_texto: 'It had a technical problem and recovered on its own.',
      ayuda_fila_traduccion_titulo: 'Translation failing',
      ayuda_fila_traduccion_texto: 'Several of the latest translations did not come through.',
      ayuda_fila_reconectando_titulo: 'Reconnecting',
      ayuda_fila_reconectando_texto: 'This panel lost its connection to that room and is retrying.',
      ayuda_fila_terminada_titulo: 'Ended',
      ayuda_fila_terminada_texto: 'The session already closed.',
      ayuda_bullet_urgencia: 'Cards are sorted by urgency: whatever needs attention comes first.',
      ayuda_bullet_quehacer: '"What to do" is a concrete suggestion, not an order.',
      ayuda_bullet_voz: 'Alerts also sound out loud (turn them on/off and set the volume in Settings → Alerts).',
      ayuda_bullet_salas: 'The "Rooms" button (header) creates, starts, stops and deletes sessions through the control service, no terminal needed.',
      ayuda_pie_tecnico: 'Technical details are in the Technical view.',
      ayuda_contenido_tecnica:
        'A <code>live</code> state with more than <code>15 s</code> without a new <code>type=text</code> ' +
        'is shown as "no text for N s" (documented threshold, brief B3). p50/p95 = <b>nearest-rank</b>, ' +
        'never an average; with fewer than <code>10</code> samples it is marked "insufficient n". The ' +
        'line next to p50/p95 is a <b>sparkline</b> of the last 20 samples: it is a decoration, it does ' +
        'not replace the numbers. "recorded" = <code>t_emit − t_captured</code> as it was stored in the ' +
        'tape (<code>replay:true</code> sessions); in live sessions that same subtraction is the ' +
        'worker\'s real latency, and the <b>perceived</b> latency = <code>t_receive − t_captured</code>, ' +
        'measured on this panel\'s own WebSocket, is added as well. "Partials/60 s" is a sliding window, ' +
        'not a whole-session average. Audio seconds: an <b>estimate</b> (comes from the worker\'s ' +
        '<code>heartbeat.meta.audio_seconds_sent</code>, only visible with a token via ' +
        '<code>Authorization: Bearer</code> to <code>/api/metricas</code>; without a token that column ' +
        'says "no token" and the rest of the row keeps working). The token is set from the gear button ' +
        '(Settings → Access), stored in <code>sessionStorage</code> (never in the URL or the query ' +
        'string), and only travels in the request header; <code>?hub=</code> is only accepted if it ' +
        'points to localhost/127.0.0.1/the same host serving this page — any other value is ignored and ' +
        'a warning appears above, so the token does not leak to an unrelated host. Sessions with ' +
        '<code>test:true</code> (fixtures/tape transport, not live ASR) carry the <b>TEST</b> label and ' +
        'are HIDDEN by default: "show test sessions" (Settings → View) brings them back. ' +
        '<code>rotation.meta.audio_lost_s</code>, when the worker sends it, accumulates per session and ' +
        'appears next to "Rotations"; the color of each reason (cierre/atasco/preventiva/goaway) is a ' +
        'visual classifier, the reason\'s text is always alongside it. The colored stripe on the left of ' +
        'each row summarizes that session\'s state (live/mute/idle/ended/waiting), same as the text in ' +
        'the Status column. The <b>Rooms</b> button talks to a separate control service ' +
        '(<code>ops/control.py</code>), NOT the hub: it uses the SAME token from Settings → Access and, ' +
        'by default, <code>http://&lt;host&gt;:8110</code> (<code>?control=host:port</code> overrides it ' +
        'with the same validation as <code>?hub=</code>).',

      config_titulo: 'Settings',
      config_cerrar: 'Close settings',
      config_seccion_acceso: 'Access',
      config_token_label: '/api/metricas token',
      config_token_guardar: 'save',
      config_token_borrar: 'clear',
      config_metricas_sin_token: 'metrics: no token',
      config_metricas_con_token: 'metrics: with token',
      config_metricas_error: 'metrics: with token — error: {detalle}',
      config_seccion_vista: 'View',
      config_mostrar_pruebas: 'show test sessions',
      config_seccion_avisos: 'Alerts',
      config_aviso_voz: 'voice alert',
      config_volumen: 'volume',
      config_probar_aviso: 'test alert',
      voz_prueba: 'This is a test alert from the panel.',

      salas_titulo: 'Rooms',
      salas_cerrar: 'Close rooms',
      salas_seccion_lista: 'Active rooms',
      salas_vacio: 'No rooms yet. Create the first one.',
      salas_seccion_nueva: 'New room',
      salas_campo_titulo: 'Display name',
      salas_campo_id: 'id',
      salas_campo_id_ayuda: 'Suggested from the name; editable. Lowercase, digits, "-" and "_".',
      salas_campo_lang: 'Talk language',
      salas_campo_traducir_a: 'Translate to',
      salas_aviso_idiomas_extra: 'Each extra language adds calls to the translation model.',
      salas_campo_fuente: 'Source',
      salas_fuente_mic: 'Microphone',
      salas_fuente_archivo: 'Test clip',
      salas_fuente_url: 'Stream',
      salas_actualizar: 'refresh',
      salas_fuente_url_ayuda: 'udp://, srt://, rtmp://',
      salas_campo_key: 'Key',
      salas_campo_duracion: 'Duration (s, optional)',
      salas_campo_arrancar: 'start now',
      salas_crear: 'Create room',
      salas_copiar_comando: 'copy command',
      salas_copiado: 'copied',
      salas_servicio_no_responde: 'The control service is not running: {comando}',
      salas_falta_token: 'To create and control rooms, paste the hub token (HUB_TOKEN). It is the same one as in Settings.',
      salas_token_label: 'Hub token',
      salas_token_placeholder: 'Paste the token',
      salas_token_guardar: 'Save',
      salas_sin_probar: '(untested live)',
      salas_sin_microfonos: 'No microphones detected.',
      salas_sin_archivos: 'No clips in fixtures/audio/clips/.',
      salas_fuente_mic_legible: 'Microphone: {valor}',
      salas_fuente_archivo_legible: 'Clip: {valor}',
      salas_fuente_url_legible: 'Stream: {valor}',
      salas_estado_detenida: 'Stopped',
      salas_estado_arrancando: 'Starting',
      salas_estado_corriendo: 'Running',
      salas_estado_reiniciando: 'Restarting',
      salas_estado_deteniendo: 'Stopping',
      salas_estado_error: 'Error: {detalle}',
      salas_btn_iniciar: 'Start',
      salas_btn_detener: 'Stop',
      salas_btn_borrar: 'Delete',
      salas_ver_sala: 'View room',
      salas_confirmar_borrar: 'Delete room "{titulo}"? This cannot be undone.',
      salas_sin_traduccion: 'no translation',
      salas_duracion_sin_limite: 'no limit (room mode)',
      salas_duracion_valor: '{n} s',
      salas_error_generico: 'Could not complete: {detalle}',
      salas_error_crear: 'Could not create the room: {detalle}',
      salas_intentos: '{n} attempts',
      salas_pid: 'pid {pid}',
      salas_id_repetido_sugerencia: 'That id already exists; a new one was suggested.',
      salas_escuchar: 'Listen',
      salas_fuente_mic_en_vivo: 'You\'re listening to the room live.',
      salas_ver_original: 'View original on YouTube (from {m})',

      pruebas_sin: 'no TEST sessions',
      pruebas_singular: 'TEST session',
      pruebas_plural: 'TEST sessions',
      pruebas_mostradas: '(shown)',
      pruebas_ocultas: '(hidden; "show test sessions" brings them back)',

      chip_hub_conectando: 'hub: connecting…',
      chip_hub_ok: 'hub: ok ({n} sessions)',
      chip_hub_sin_respuesta: 'hub: NO RESPONSE',
      sin_datos_aun: 'no data yet',
      actualizado: 'updated {hora}',
      chip_conexion: 'panel connection: {n}/{total} live',
      chip_hub_seguridad: 'external hub ignored for security: "{valor}"',
      chip_control_seguridad: 'external control ignored for security: "{valor}"',
      aviso_sin_respuesta: 'Could not read {url} ({detalle}). The counters shown are stuck at the last known value.',

      resumen_en_vivo: 'live',
      resumen_en_replay: 'in replay',
      resumen_a_atender: 'need attention',
      resumen_sin_problemas: 'no issues',
      resumen_ultimo_dato: 'last update:',
      tarjetas_vacio: 'Waiting for the first response from /api/sesiones…',

      estado_terminada: 'Ended',
      estado_sin_texto_nunca: 'No text yet (for {s} s)',
      estado_sin_texto_alarma: 'No text for {s} s',
      estado_rotacion: 'Got stuck and reopened{perdido}',
      estado_rotacion_perdido: ' ({n} s lost)',
      estado_traduccion: 'Translation failing ({f} of {n})',
      estado_reconectando: 'Reconnecting',
      estado_replay: 'Replay',
      estado_al_dia: 'Up to date',
      estado_inactiva: 'Idle',
      estado_esperando: 'waiting for data',
      estado_en_vivo: 'live',
      estado_en_vivo_sin_texto: 'live (no text yet)',
      estado_mudo: 'no text for {s} s',

      accion_prefijo: 'What to do:',
      accion_sin_texto_nunca: 'Check the room\'s audio (is the mic or the file actually sending anything?).',
      accion_sin_texto_alarma: 'Check the room\'s audio; if it continues, check worker.log.',
      accion_rotacion_atasco: 'It recovered on its own. If this keeps happening with another room from the same project, stagger their start by 20 s (see README) and check worker.log.',
      accion_rotacion_cierre: 'It recovered on its own. If this keeps happening, check worker.log.',
      accion_traduccion: 'Check worker.log; it may be a translation quota limit.',
      accion_reconectando: 'Waiting to reconnect to the server. If it continues for more than a minute, check the hub or the network.',

      espectador_uno: '{n} viewer',
      espectador_varios: '{n} viewers',

      voz_nunca_texto: 'Attention. Room {sala} has been live for more than thirty seconds and still is not showing text.',
      voz_silencio: 'Attention. Room {sala} has not shown text for more than {s} seconds.',

      th_sesion: 'Session',
      th_idioma: 'Language',
      th_estado: 'Status',
      th_ultimo_seq: 'Last seq',
      th_textos: 'Texts',
      th_lat_worker: 'Worker latency (p50 / p95)',
      th_lat_percibida: 'Perceived latency (p50 / p95)',
      th_rotaciones: 'Rotations',
      th_watchdog: 'Watchdog reopens',
      th_errores: 'Errors',
      th_trad_okfalse: 'Translations ok:false',
      th_parciales: 'Partials / 60 s',
      th_audio: 'Audio sent (estimate)',
      th_espectadores: 'Viewers',

      trad_prefijo: 'to:',
      lat_grabada_sub: 'recorded (t_emit − t_captured)',
      lat_worker_sub: 't_emit − t_captured',
      lat_percibida_sub: 't_receive − t_captured (panel\'s own WS)',
      lat_no_aplica_replay: 'not applicable (replay)',
      sin_muestras: 'no samples',
      n_insuficiente: 'insufficient n (n={n})',
      ok_true_igual: 'ok:true = {n}',
      watchdog_eventos_total: '{n} watchdog events total',
      audio_perdido: 'audio lost {s}',
      sparkline_titulo: 'last {n} samples: {min} to {max}',
      audio_sin_heartbeat: 'no heartbeat with audio_seconds_sent',
      audio_estimacion: '(estimate)',
      audio_latido_hace: 'heartbeat {n} s ago',
      ultimo_prefijo: 'last {hora}',

      pie_fuente: 'Source:',
      pie_resto: 'refresh of /api/sesiones every 2 s · history reconciliation every 2 s · ' +
        're-render every 1 s. Hub override: add ?hub=host:port to this URL (localhost/127.0.0.1/this ' +
        'same host only). A browser console with no errors is part of the closing evidence (see ' +
        'reportes/monitor-b10.md).'
    }
  };

  var NOMBRES_IDIOMA = {
    es: { en: 'inglés', es: 'español', pt: 'portugués', fr: 'francés', de: 'alemán', it: 'italiano' },
    en: { en: 'English', es: 'Spanish', pt: 'Portuguese', fr: 'French', de: 'German', it: 'Italian' }
  };

  function detectarDefault() {
    var nav = (navigator.language || navigator.userLanguage || 'es');
    return /^es/i.test(String(nav)) ? 'es' : 'en';
  }

  function localeDeUrl() {
    try {
      var p = new URLSearchParams(location.search).get('locale');
      if (p === 'en' || p === 'es') return p;
    } catch (e) { /* location no disponible (tests): ignorar */ }
    return null;
  }

  function localeGuardado() {
    try { return localStorage.getItem(LOCALE_KEY); } catch (e) { return null; }
  }

  function guardarLocale(l) {
    try { localStorage.setItem(LOCALE_KEY, l); } catch (e) { /* no fatal */ }
  }

  var forzadoPorUrl = !!localeDeUrl();
  var locale = localeDeUrl() || localeGuardado() || detectarDefault();
  if (locale !== 'en' && locale !== 'es') locale = 'es';

  var listeners = [];

  function aplicarLangHtml() {
    try { document.documentElement.lang = locale === 'en' ? 'en' : 'es'; } catch (e) { /* no fatal */ }
  }
  aplicarLangHtml();

  function t(key, vars) {
    var d = DICT[locale] || DICT.es;
    var s = (d && d[key] != null) ? d[key] : (DICT.es[key] != null ? DICT.es[key] : key);
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.split('{' + k + '}').join(vars[k]);
      });
    }
    return s;
  }

  function setLocale(l) {
    if (l !== 'en' && l !== 'es') return;
    if (l === locale) return;
    locale = l;
    guardarLocale(l);
    aplicarLangHtml();
    listeners.forEach(function (fn) { try { fn(locale); } catch (e) { console.error('i18n: listener falló', e); } });
  }

  function onChange(fn) {
    if (typeof fn === 'function') listeners.push(fn);
  }

  function getLocale() { return locale; }

  function vozLang() { return locale === 'en' ? 'en-US' : 'es-AR'; }

  function localeBcp47() { return locale === 'en' ? 'en-US' : 'es-AR'; }

  function numero(n, opciones) {
    if (n == null || !isFinite(n)) return String(n);
    try { return Number(n).toLocaleString(localeBcp47(), opciones); } catch (e) { return String(n); }
  }

  function nombreIdioma(codigo) {
    if (!codigo) return '—';
    var base = String(codigo).split('-')[0].toLowerCase();
    var mapa = NOMBRES_IDIOMA[locale] || NOMBRES_IDIOMA.es;
    return mapa[base] || codigo;
  }

  global.PanelI18n = {
    t: t,
    setLocale: setLocale,
    getLocale: getLocale,
    onChange: onChange,
    vozLang: vozLang,
    numero: numero,
    nombreIdioma: nombreIdioma,
    forzadoPorUrl: forzadoPorUrl
  };
})(window);
