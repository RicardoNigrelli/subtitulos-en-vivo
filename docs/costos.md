# Costos — estimación por minuto de audio y por sesión (R16, C3)

**Esto es una ESTIMACIÓN: no refleja una factura real.** La Live API de Gemini no devuelve `usage_metadata`
de forma confiable durante una transcripción normal: en toda la vibeathon apareció UNA sola vez,
justo en el mensaje de cierre de una sesión que se quedó sin cupo (ver abajo). No hay un endpoint de
facturación consultado en este proyecto. Todo lo que sigue combina ese único dato real observado con
los precios públicos de Gemini (sección "Precios", consultados el 24/09/2026): es una **estimación** para
presupuestar, no una factura reproducida.

## Dato real: tokens de audio por segundo (n=1)

El único caso en que el servidor mandó `usageMetadata` fue en el cierre por cupo agotado de una
sesión real, grabado tal cual en el repo:

```bash
grep -o '"promptTokenCount": [0-9]*' fixtures/casetes/b1-nerdearla-en-intento2-quota.jsonl
# "promptTokenCount": 7072

grep -o '"ACTIVITY_END", "audioOffset": "[0-9.]*s"' fixtures/casetes/b1-nerdearla-en-intento2-quota.jsonl | tail -1
# "ACTIVITY_END", "audioOffset": "282.780s"

grep -o '"responseTokenCount": [0-9]*' fixtures/casetes/b1-nerdearla-en-intento2-quota.jsonl
# "responseTokenCount": 640
```

- **Tokens de entrada (audio):** 7072 tokens / 282,8 s de audio ≈ **25 tokens de audio por segundo**
  (`promptTokenCount`, modalidad AUDIO). Es un solo caso observado (n=1); se usa como referencia por
  ser el único momento en que el servidor devolvió el conteo real.
- **Tokens de salida (texto transcripto):** 640 tokens / 282,8 s ≈ 2,3 tokens/s. Bastante menores;
  se ignoran en el ejemplo numérico de abajo salvo que el precio de salida sea distinto al de
  entrada (ver "Precios").

## Fórmula — costo estimado por minuto de audio (transcripción)

```
tokens_audio_por_minuto = 60 s/min × 25 tokens/s  =  1500 tokens de audio por minuto (ESTIMACIÓN)

costo_estimado_por_minuto  =  1500 × 3,50 USD / 1 000 000  ≈ 0,00525 USD        # ESTIMACIÓN (la página lo redondea a 0,005 USD/min)
costo_estimado_por_sesion  =  0,00525 USD × minutos_de_la_charla  (+ salida de texto ≈ 0,004 USD/min)
```

Ejemplo numérico, una charla de 30 minutos (del orden de las citadas en
`fixtures/audio/FUENTES.md`):

```
1500 tokens/min × 30 min = 45 000 tokens de audio por sesión

costo_estimado_por_sesion ≈ 45 000 × 3,50 / 1 000 000 ≈ 0,16 USD de audio de entrada
                             + 30 × 0,004 ≈ 0,12 USD de texto de salida  ⇒ ≈ 0,28 USD por charla   # ESTIMACIÓN
```

Con **dos sesiones simultáneas** (el mínimo de R21): duplicar el resultado anterior. El costo NO
depende de cuántos espectadores miran cada sesión (eje 2 de escalabilidad, `hub/README.md`): el fan-out
es tráfico hub↔navegador, no llamadas a Gemini.

## Llamadas de texto (traducción EN↔ES) por minuto de audio

El traductor (`worker/traductor.py`) arma un lote por idioma destino cada vez que junta **2 bloques
de texto o pasan 4 segundos** desde el primer bloque pendiente (lo que ocurra primero):

```bash
grep -n "^LOTE_MAX\|^LOTE_S\|^RPM " worker/traductor.py
# LOTE_MAX = int(_env_num("TRADUCTOR_LOTE_MAX", 2))
# LOTE_S = _env_num("TRADUCTOR_LOTE_S", 4.0)
# RPM = 12
# (también LOTE_MAX_SOLO = 3 y LOTE_S_SOLO = 8.0: lote más largo cuando un modelo de texto está fuera de servicio)
```

```
llamadas_de_texto_por_minuto_estimadas (techo)  =  60 s/min / LOTE_S  =  60 / 4  =  15 llamadas/min (el limitador propio lo recorta a 12 por modelo)
   por idioma destino (coincide con el limitador propio de 12 RPM por modelo, mismo archivo)
```

Costo estimado de traducción por minuto de audio (un idioma destino), con un tamaño de lote típico
de `<TOKENS_LOTE>` tokens de entrada+salida por llamada (a completar según el largo real de los
bloques traducidos):

```
costo_estimado_traduccion_por_minuto  ≈  12 lotes × (150 tokens entrada × 0,30 + 60 tokens salida × 2,50) / 1 000 000
                                      ≈  0,0023 USD por minuto  (≈ 0,07 USD por charla de 30 min)   # ESTIMACIÓN, flash-lite 3.5
```

Con traducción en las dos direcciones (EN→ES y ES→EN, `--traducir-a auto`) el estimado de arriba se
duplica.

## Precios (completar antes de publicar un número final)

Esta estimación depende de precios que cambian y que este documento NO fija de memoria. Completar
los precios de la sección "Precios" con
los valores vigentes de la página pública de precios de Gemini:

<https://ai.google.dev/gemini-api/docs/pricing>

Modelos usados (ver `.env.example`): `gemini-3.5-transcribe-live` (transcripción en vivo) y
`gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` (traducción de texto).

## Qué NO es este documento

- No refleja gasto real ni facturación: no llega `usage_metadata` de forma confiable en el flujo
  normal (skill `entrega`); es una **estimación** a partir de un solo caso real observado más
  precios públicos.
- No es una medida de precisión de reconocimiento ni de calidad: sólo habla de tokens y llamadas.
- No incluye infraestructura (hub, worker, hosting, ancho de banda): sólo la porción de API de
  Gemini.
- Durante la vibeathon se usó el nivel gratuito (sin cargo); esta fórmula sirve para presupuestar
  cuando el proyecto pasa a un nivel pago o a más sesiones de las que entran en ese nivel gratuito
  (ver "Cómo escalar a más sesiones" en el [`README.md`](../README.md), R21/C3).

## Precios (consultados el 24/09/2026 en https://ai.google.dev/gemini-api/docs/pricing, nivel pago)

| Modelo | Entrada | Salida |
|---|---|---|
| Gemini 3.5 Transcribe Live | 3,50 USD por 1M tokens de audio, o 0,005 USD por minuto de audio | 21,00 USD por 1M tokens de texto, o 0,004 USD por minuto |
| Gemini 3.5 Flash-Lite (texto) | 0,30 USD por 1M tokens | 2,50 USD por 1M tokens |
| Gemini 3.1 Flash-Lite (texto) | 0,25 USD por 1M tokens | 1,50 USD por 1M tokens |

La misma página indica que el audio se cuenta a **25 tokens por segundo**, lo que coincide con el único
`usageMetadata` observado (7072 tokens / 282,8 s). El nivel gratuito figura como "Free of charge" con los
límites de frecuencia citados en `README.md` (15 RPM / 500 RPD por modelo de texto; TPM 20K en transcripción).
Los precios pueden cambiar: volver a consultar la página antes de presupuestar un evento.

**Orden de magnitud para un evento (estimación), cuenta reproducible en una línea:**
`python -c "m=5*8*60; print(round(m*0.00525,1), round(m*0.004,1), round(m*0.0023,1), round(m*(0.00525+0.004+0.0023),1))"`
→ `12.6 9.6 5.5 27.7`. Es decir: 5 salas × 8 horas = 2400 minutos de audio ⇒ ≈ 12,6 USD de
transcripción (entrada) + ≈ 9,6 USD de salida de texto + ≈ 5,5 USD de traducción ⇒ **≈ 28 USD por día de
conferencia** con dos idiomas por sala, sin contar la infraestructura del hub (un contenedor). El audio que
realmente se ENVÍA supera al de la charla en un 15–19 % (solape de ventanas y reenvíos en rotaciones, medido
sobre los casetes reales: 691,5 y 680,0 s enviados por 580 s de charla, `qa/out/adv-final/e5-costos.log`): con
eso, ≈ 30,6 USD. Sigue siendo una estimación.
