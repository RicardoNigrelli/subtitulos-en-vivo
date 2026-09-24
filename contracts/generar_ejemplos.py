"""Genera contracts/ejemplos/*.jsonl: mensajes SINTETICOS y EVIDENTES del contrato v1.

    python -m contracts.generar_ejemplos

Los textos dicen literalmente "Ejemplo de contrato, linea N de 20": no son transcripciones.
Todos llevan replay=true y meta.source="ejemplo-contrato". Sirven para desarrollar front y
panel contra el hub real con `python -m hub.inyectar`; NO son evidencia de ASR (R17a/R18).
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts import EJEMPLOS_DIR

T0 = 1790262000.0  # 2026-09-24 12:00:00 AR (15:00 UTC): base arbitraria de los ejemplos
FUENTE = {"source": "ejemplo-contrato"}
N = 20
LARGAS = {5, 12}  # lineas largas a proposito, para probar el corte de renglon en 390 px


def _r(x: float) -> float:
    return round(x, 3)


def _texto(i: int, lang: str) -> tuple[str, str]:
    """(original, traduccion) de la linea i de una sesion en `lang`."""
    if i in LARGAS:
        en = (f"Contract example, line {i} of {N}: this line is deliberately longer to test "
              f"line wrapping on narrow screens.")
        es = (f"Ejemplo de contrato, línea {i} de {N}: esta línea es más larga a propósito para "
              f"probar el corte de renglón en pantallas angostas.")
    elif lang == "en":
        en, es = f"Contract example, line {i} of {N}.", f"Ejemplo de contrato, línea {i} de {N}."
    else:
        es = f"Ejemplo de contrato, línea {i} de {N} (sesión en español)."
        en = f"Contract example, line {i} of {N} (Spanish session)."
    return (en, es) if lang == "en" else (es, en)


def sesion(lang: str, session_id: str, t0: float, falla: int) -> list[dict]:
    """20 mensajes type=text; en la linea `falla` la traduccion viene marcada como fallida."""
    otro = "es" if lang == "en" else "en"
    msgs = []
    for i in range(1, N + 1):
        original, traduccion = _texto(i, lang)
        a0, a1 = 3.0 * (i - 1), 3.0 * i
        t_cap = t0 + a1
        msgs.append({
            "v": 1,
            "type": "text",
            "session_id": session_id,
            "seq": i,
            "lang": lang,
            "text": original,
            "translations": {otro: ({"text": None, "ok": False} if i == falla
                                    else {"text": traduccion, "ok": True})},
            "audio_start": _r(a0),
            "audio_end": _r(a1),
            "t_captured": _r(t_cap),
            "t_emit": _r(t_cap + 1.2),
            "replay": True,
            "meta": dict(FUENTE),
        })
    return msgs


def tipos() -> list[dict]:
    """Un ejemplo de cada type, en orden de vida de una sesion."""
    sid, t = "ejemplo-tipos", T0 + 3600.0
    base = {"v": 1, "session_id": sid, "lang": "en", "replay": True}
    return [
        {**base, "type": "session_start", "seq": 1, "t_emit": _r(t),
         "meta": {"title": "Ejemplo de contrato: un mensaje de cada tipo", "source": "ejemplo-contrato"}},
        {**base, "type": "text", "seq": 2, "text": "Contract example, text message (type=text).",
         "translations": {"es": {"text": "Ejemplo de contrato, mensaje de texto (type=text).", "ok": True}},
         "audio_start": 0.0, "audio_end": 3.1, "t_captured": _r(t + 3.1), "t_emit": _r(t + 4.3),
         "meta": dict(FUENTE)},
        {**base, "type": "rotation", "seq": 3, "t_emit": _r(t + 5.0),
         "meta": {"reason": "goaway", "old_id": "ejemplo-conexion-1", "new_id": "ejemplo-conexion-2", **FUENTE}},
        {**base, "type": "watchdog", "seq": 4, "t_emit": _r(t + 21.0),
         "meta": {"silent_s": 15.0, "reopened": True, **FUENTE}},
        {**base, "type": "error", "seq": 5, "t_emit": _r(t + 24.0),
         "meta": {"code": "translate_timeout",
                  "message": "Ejemplo de contrato: la traducción no llegó a tiempo.", **FUENTE}},
        {**base, "type": "text", "seq": 6, "text": "Contract example, text whose translation failed.",
         "translations": {"es": {"text": None, "ok": False}},
         "audio_start": 21.0, "audio_end": 24.0, "t_captured": _r(t + 24.0), "t_emit": _r(t + 27.9),
         "meta": dict(FUENTE)},
        {**base, "type": "heartbeat", "seq": None, "t_emit": _r(t + 28.0),
         "meta": {"alive": True, "audio_seconds_sent": 27.0, **FUENTE}},
        {**base, "type": "session_end", "seq": 7, "t_emit": _r(t + 30.0),
         "meta": {"reason": "fin del ejemplo", **FUENTE}},
    ]


def traduccion() -> list[dict]:
    """AMPLIADO 24/09 B2: `partial` + `text` con translations {} + `translation` que el hub mergea.

    Incluye los tres casos del merge: traduccion ok, traduccion fallida (ok:false) y una traduccion
    que llega ANTES que su text (el hub la deja pendiente y la aplica cuando llega el seq 4).
    """
    sid, t = "ejemplo-traduccion", T0 + 7200.0
    base = {"v": 1, "session_id": sid, "lang": "en", "replay": True}

    def parcial(texto: str, a0: float, dt: float) -> dict:
        return {**base, "type": "partial", "seq": None, "text": texto, "audio_start": a0,
                "t_captured": _r(t + dt), "t_emit": _r(t + dt + 0.2), "meta": dict(FUENTE)}

    def final(seq: int, texto: str, a0: float, a1: float, dt: float) -> dict:
        return {**base, "type": "text", "seq": seq, "text": texto, "translations": {},
                "audio_start": a0, "audio_end": a1, "t_captured": _r(t + dt), "t_emit": _r(t + dt + 0.9),
                "meta": dict(FUENTE)}

    def trad(items: list[dict], dt: float, batch_ms: int) -> dict:
        return {**base, "type": "translation", "seq": None, "text": None, "translations": {},
                "items": items, "t_emit": _r(t + dt),
                "meta": {"lang_to": "es", "model": "ejemplo-contrato", "batch_ms": batch_ms, **FUENTE}}

    return [
        {**base, "type": "session_start", "seq": 1, "t_emit": _r(t),
         "meta": {"title": "Ejemplo de contrato: partial, text y translation", "source": "ejemplo-contrato"}},
        parcial("Contract example, partial", 0.0, 1.0),
        parcial("Contract example, partial text that", 0.0, 2.0),
        final(2, "Contract example, final text 1 (translated later).", 0.0, 3.0, 3.0),
        parcial("Contract example, partial of", 3.0, 4.5),
        final(3, "Contract example, final text 2 (its translation fails).", 3.0, 6.0, 6.0),
        trad([{"seq": 2, "text": "Ejemplo de contrato, texto final 1 (traducido después).", "ok": True},
              {"seq": 3, "text": None, "ok": False}], 8.0, 1200),
        trad([{"seq": 4, "text": "Ejemplo de contrato, texto final 3 (su traducción llegó antes que el texto).",
               "ok": True}], 9.0, 900),
        final(4, "Contract example, final text 3 (its translation arrived first).", 6.0, 9.0, 9.0),
        {**base, "type": "session_end", "seq": 5, "t_emit": _r(t + 11.0),
         "meta": {"reason": "fin del ejemplo", **FUENTE}},
    ]


def escribir(path: Path, msgs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"{path}: {len(msgs)} mensajes")


def main() -> None:
    EJEMPLOS_DIR.mkdir(exist_ok=True)
    escribir(EJEMPLOS_DIR / "sesion-en.jsonl", sesion("en", "ejemplo-en", T0, falla=13))
    escribir(EJEMPLOS_DIR / "sesion-es.jsonl", sesion("es", "ejemplo-es", T0 + 1.5, falla=7))
    escribir(EJEMPLOS_DIR / "tipos.jsonl", tipos())
    escribir(EJEMPLOS_DIR / "traduccion.jsonl", traduccion())


if __name__ == "__main__":
    main()
