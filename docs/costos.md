# Costos — estimación por minuto de audio y por sesión (R16, C3)

**Esto es una ESTIMACIÓN: no refleja una factura real.** La Live API de Gemini no devuelve `usage_metadata`
de forma confiable durante una transcripción normal: en toda la vibeathon apareció UNA sola vez,
justo en el mensaje de cierre de una sesión que se quedó sin cupo (ver abajo). No hay un endpoint de
facturación consultado en este proyecto. Todo lo que sigue combina ese único dato real observado con
los precios públicos de Gemini (a completar, marcador `<PRECIO...>`): es una **estimación** para
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

costo_estimado_por_minuto  =  1500 × <PRECIO_INPUT_AUDIO_POR_TOKEN>            # USD, ESTIMACIÓN
costo_estimado_por_sesion  =  1500 × <PRECIO_INPUT_AUDIO_POR_TOKEN> × minutos_de_la_charla
```

Ejemplo numérico, una charla de 30 minutos (del orden de las citadas en
`fixtures/audio/FUENTES.md`), con el precio todavía sin completar:

```
1500 tokens/min × 30 min = 45 000 tokens de audio por sesión

costo_estimado_por_sesion ≈ 45 000 × <PRECIO_INPUT_AUDIO_POR_TOKEN>   # USD, ESTIMACIÓN
```

Con **dos sesiones simultáneas** (el mínimo de R21): duplicar el resultado anterior. El costo NO
depende de cuántos espectadores miran cada sesión (eje 2 de escalabilidad, `hub/README.md`): el fan-out
es tráfico hub↔navegador, no llamadas a Gemini.

## Llamadas de texto (traducción EN↔ES) por minuto de audio

El traductor (`worker/traductor.py`) arma un lote por idioma destino cada vez que junta **2 bloques
de texto o pasan 5 segundos** desde el primer bloque pendiente (lo que ocurra primero):

```bash
grep -n "^LOTE_MAX\|^LOTE_S\|^RPM " worker/traductor.py
# LOTE_MAX = 2
# LOTE_S = 5.0
# RPM = 12
```

```
llamadas_de_texto_por_minuto_estimadas (techo)  =  60 s/min / LOTE_S  =  60 / 5  =  12 llamadas/min
   por idioma destino (coincide con el limitador propio de 12 RPM por modelo, mismo archivo)
```

Costo estimado de traducción por minuto de audio (un idioma destino), con un tamaño de lote típico
de `<TOKENS_LOTE>` tokens de entrada+salida por llamada (a completar según el largo real de los
bloques traducidos):

```
costo_estimado_traduccion_por_minuto  ≈  12 × <TOKENS_LOTE> × <PRECIO_TEXTO_POR_TOKEN>   # USD, ESTIMACIÓN
```

Con traducción en las dos direcciones (EN→ES y ES→EN, `--traducir-a auto`) el estimado de arriba se
duplica.

## Precios (completar antes de publicar un número final)

Esta estimación depende de precios que cambian y que este documento NO fija de memoria. Completar
`<PRECIO_INPUT_AUDIO_POR_TOKEN>`, `<PRECIO_OUTPUT_AUDIO_POR_TOKEN>` y `<PRECIO_TEXTO_POR_TOKEN>` con
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
