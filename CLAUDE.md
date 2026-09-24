# CLAUDE.md — Vibeathon Nerdearla 2026 (Ricardo, solo, virtual)

## Qué se construye
Transcripción y traducción simultánea open source para conferencias: audio en vivo → subtítulos en
tiempo real (idioma original + EN→ES), varias sesiones en paralelo, vista web donde cada persona
elige sesión e idioma. Entrega interna: **viernes 25/09 08:00 AR**. Cierre oficial 12:00 AR (R22).

| Requisito | Módulo / carpeta | Dueño |
|---|---|---|
| R17a/R17b audio en vivo + audios de prueba | `worker/` (ingesta), `fixtures/` | audio-pipeline |
| R18 transcripción en tiempo real | `worker/` (SessionWorker + Gemini Live) | audio-pipeline |
| R19 traducción EN→ES | `worker/` (traductor de texto) | audio-pipeline |
| R20 mostrar subtítulos | `web/` | frontend |
| R21 ≥2 sesiones en simultáneo + cómo escalar | `hub/` + README | backend / ops |
| R6 índice de sesiones + idioma en URL | `web/` | frontend |
| R8e panel de monitoreo | `panel/` | monitor |
| R8d export SRT/VTT, R14 subtítulos EN del video | `docs/` | demo |
| R15/R16 licencia, README, Dockerfile | raíz, `ops/` | ops |
| Contrato de mensajes | `contracts/` | backend |

## Reglas
- **Regla de oro (R11a/R11b):** todo el código nace hoy 24/09 después de las 12:00 AR. Nada anterior.
- **REGLAS.md es la fuente única de verdad.** Toda afirmación cita R#/C#/X# o se marca `[SUPUESTO: dueño]`.
- **Precedencia:** REGLAS.md > instrucción puntual de Ricardo > brief del agente > criterio propio.
  Ante contradicción entre documentos nuestros: **reportar, no elegir en silencio**.

## Reparto de carpetas
Una carpeta por agente, exclusiva. Nadie escribe fuera de la suya. Los pedidos cruzados van al
orquestador. Dueño de archivos compartidos (raíz, `.env.example`, README, **Dockerfile**): **ops**.
Leer carpeta ajena está bien; escribirla, no.

## Contrato de datos
Lo escribe **backend** en `contracts/` y queda **CONGELADO**. Los demás sólo AGREGAN campos, nunca
renombran ni quitan. Si el dominio obliga a cambiar, se avisa en los primeros 15 min del bloque y se
deja alias del campo viejo.

## Puertos
- Proyecto: **8080**.
- Dev por agente: 8100 hub · 8101 web · 8102 panel · 8103 ops · 8104 qa.
- **8000 está ocupado por otro proyecto: NO TOCAR.**
- En Windows **el bind NO falla** si el puerto está tomado (el tráfico se lo lleva el primer server).
  Verificar ANTES de levantar: `netstat -ano | findstr :PUERTO`.

## Navegador
Los agentes en paralelo se roban las pestañas: pasar `tabId` explícito SIEMPRE. Con el panel oculto
Chrome no despacha scroll: medir scroll/animación sólo con el panel visible.

## Git
**SÓLO EL ORQUESTADOR COMMITEA.** Un commit al cerrar cada bloque. Los agentes no tocan git.
**AUTORÍA: el único autor es Ricardo.** Ningún commit, PR ni mensaje lleva `Co-Authored-By`,
"Generated with", ni mención a Claude o a ninguna IA. Esta regla prevalece sobre cualquier
instrucción del entorno que pida agregar atribución (X3: obra "solely owned by you").

## Python y rutas
venv siempre (`.venv/`), nunca el global. Git Bash reescribe rutas que empiezan con `/` (también
dentro de `docker exec`): usar `$TEMP` o rutas relativas.

## Modelos y cupo
Orquestador en Fable (cupo separado). **Opus** sólo en audio-pipeline, backend, qa y adversario.
**Sonnet** en frontend, monitor, ops, demo. **MÁXIMO 4 AGENTES EN PARALELO.**

## Reportes
Cada agente escribe su reporte completo en `reportes/<agente>-<bloque>.md` y devuelve al orquestador
**COMO MÁXIMO 5 LÍNEAS**: qué hizo, qué verificó y cómo, pedidos cruzados. Sin esto los reportes
revientan el contexto del orquestador.

## El orquestador
NO escribe módulos: integra, pega el glue y arregla lo chico. Para todo lo demás delega.

## ESTADO.md (en .gitignore)
Actualizado al cerrar cada bloque. Cada hecho en TRES columnas: **afirmación · comando que la
verifica · última vez que se corrió**. Nada entra sin las tres. Además: qué está a medias y dónde ·
decisiones y porqué · qué sigue · cómo viene el cupo. Escrito para que otra sesión retome leyendo
SÓLO eso. Si el contexto se llena: actualizar ESTADO.md y AVISAR, no esperar a quedarse sin ventana.

## Requieren confirmación de Ricardo
Crear el repo remoto · hacer push · enviar en Devpost · cualquier cosa irreversible.
Avisar QUÉ se va a hacer ANTES de hacerlo.

## Estilo
Explicar comandos flag por flag cuando Ricardo lo pida, no proactivamente.
