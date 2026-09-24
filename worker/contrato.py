"""Mensajes del contrato v1 (lo define backend en contracts/; nombres acordados en el brief de B1).

Campos: v, type, session_id, seq, lang, text, translations, audio_start, audio_end, t_captured,
t_emit, replay, meta. `seq` lo emite el WORKER (monotonico por sesion); heartbeat lleva seq null.
"""
from __future__ import annotations

import time
from typing import Any, Optional

V = 1
TIPOS = ("text", "rotation", "watchdog", "error", "heartbeat", "session_start", "session_end",
         "translation", "partial")
# B2 (aditivos): translation {items: [{seq, text, ok}], meta: {lang_to, model, batch_ms, source?}} y
# partial (interimInputTranscription). Los dos llevan seq null (no son lineas del historial).
SIN_SEQ = ("heartbeat", "translation", "partial")


def mensaje(tipo: str, session_id: str, seq: Optional[int], lang: str, *,
            text: Optional[str] = None, translations: Optional[dict] = None,
            audio_start: Optional[float] = None, audio_end: Optional[float] = None,
            t_captured: Optional[float] = None, t_emit: Optional[float] = None,
            replay: bool = False, meta: Optional[dict] = None,
            items: Optional[list] = None) -> dict[str, Any]:
    if tipo not in TIPOS:
        raise ValueError(f"tipo desconocido: {tipo}")
    if tipo in SIN_SEQ:
        seq = None
    m = {
        "v": V,
        "type": tipo,
        "session_id": session_id,
        "seq": seq,
        "lang": lang,
        "text": text,
        "translations": {} if translations is None else translations,
        "audio_start": audio_start,
        "audio_end": audio_end,
        "t_captured": t_captured,
        "t_emit": time.time() if t_emit is None else t_emit,
        "replay": bool(replay),
        "meta": {} if meta is None else meta,
    }
    if tipo == "translation":
        m["items"] = list(items or [])
    return m
