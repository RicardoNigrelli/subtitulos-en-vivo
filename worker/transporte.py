"""Interfaz Transporte (SessionWorker desacoplado del transporte) + transporte real Gemini Live.

El SessionWorker habla SOLO con esta interfaz: conectar / enviar_audio / activity_start /
activity_end / recibir / cerrar. `recibir()` entrega los mensajes CRUDOS del server como dict
(el JSON del frame tal cual), mas un evento final {"_close": {...}} con el codigo de cierre.
Asi el mismo SessionWorker corre contra Gemini o contra un casete (B3/B4) sin API.

Config Gemini (google-genai 2.25.0, verificada con `types.*.model_fields` el 24/09 13:02):
  LiveConnectConfig.input_audio_transcription = AudioTranscriptionConfig(language_codes, custom_vocabulary)
  LiveConnectConfig.realtime_input_config = RealtimeInputConfig(
      automatic_activity_detection=AutomaticActivityDetection(disabled=True),
      activity_handling=ActivityHandling.NO_INTERRUPTION)
  Turnos manuales: send_realtime_input(activity_start=ActivityStart()) / (activity_end=ActivityEnd())
  Audio: send_realtime_input(audio=Blob(data, mime_type="audio/pcm;rate=16000"))
  Sin session_resumption (skill gemini-live: se acepta y nunca entrega handle).
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Optional, Protocol

from worker.ingesta import MIME


class Transporte(Protocol):
    async def conectar(self) -> dict: ...
    async def enviar_audio(self, data: bytes) -> None: ...
    async def activity_start(self) -> None: ...
    async def activity_end(self) -> None: ...
    def recibir(self) -> AsyncIterator[dict]: ...
    async def cerrar(self) -> None: ...


def config_live(lang: str, vocab: list[str] | None, modo: Optional[str] = None,
                auto_vad: bool = False):
    """auto_vad=True (A/B B3): VAD automatico del server ACTIVADO, sin activity_start/end nuestros.
    NO_INTERRUPTION va igual en los dos modos (skill gemini-live: sin eso se pierde ~80 %)."""
    from google.genai import types

    atc = {"custom_vocabulary": list(vocab or [])}
    if lang:
        atc["language_codes"] = [lang]
    if modo:
        atc["mode"] = modo
    return types.LiveConnectConfig(
        input_audio_transcription=types.AudioTranscriptionConfig(**atc),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=not auto_vad),
            activity_handling=types.ActivityHandling.NO_INTERRUPTION,
        ),
    )


def config_a_dict(cfg) -> dict:
    return cfg.model_dump(mode="json", exclude_none=True)


class TransporteGemini:
    """Transporte real. Lee los frames crudos del websocket (no el objeto parseado del SDK)."""

    def __init__(self, modelo: str, lang: str, vocab: list[str] | None = None,
                 nombre_key: str = "GEMINI_API_KEY", modo: Optional[str] = None,
                 auto_vad: bool = False):
        self.modelo = modelo
        self.auto_vad = auto_vad
        self.lang = lang
        self.vocab = list(vocab or [])
        self.nombre_key = nombre_key
        self.cfg = config_live(lang, self.vocab, modo, auto_vad)
        self._cm = None
        self._session = None
        self._cerrado = False

    def config_enviada(self) -> dict:
        return {"model": self.modelo, "config": config_a_dict(self.cfg)}

    async def conectar(self) -> dict:
        from worker.gemini import cliente

        c = cliente(self.nombre_key)
        self._cm = c.aio.live.connect(model=self.modelo, config=self.cfg)
        self._session = await self._cm.__aenter__()
        # El SDK consume el primer frame (setupComplete) dentro de connect(): se devuelve lo que
        # el SDK parseo de ese frame. Todo lo demas se lee crudo en recibir().
        sc = getattr(self._session, "_setup_complete", None)
        if sc is not None and hasattr(sc, "model_dump"):
            return {"setupComplete": sc.model_dump(mode="json", exclude_none=True)}
        return {"setupComplete": {}}

    async def enviar_audio(self, data: bytes) -> None:
        from google.genai import types

        await self._session.send_realtime_input(audio=types.Blob(data=data, mime_type=MIME))

    async def activity_start(self) -> None:
        from google.genai import types

        await self._session.send_realtime_input(activity_start=types.ActivityStart())

    async def activity_end(self) -> None:
        from google.genai import types

        await self._session.send_realtime_input(activity_end=types.ActivityEnd())

    async def recibir(self) -> AsyncIterator[dict]:
        from websockets.exceptions import ConnectionClosed

        ws = self._session._ws
        while True:
            try:
                raw = await ws.recv(decode=False)
            except ConnectionClosed as e:
                rc, sc = e.rcvd, e.sent
                if self._cerrado:
                    by = "client"
                elif rc is not None:
                    by = "server"
                else:
                    by = "red"      # sin frame de cierre del server (caida o keepalive vencido)
                yield {"_close": {"code": rc.code if rc else 1006,
                                  "reason": rc.reason if rc else "sin frame de cierre",
                                  "by": by,
                                  "sent": {"code": sc.code, "reason": sc.reason} if sc else None,
                                  "exc": f"{type(e).__name__}: {e}"}}
                return
            except Exception as e:  # red, etc.
                yield {"_close": {"code": None, "reason": f"{type(e).__name__}: {e}", "by": "error"}}
                return
            try:
                yield json.loads(raw)
            except Exception:
                yield {"_raw_no_json": raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)}

    async def cerrar(self) -> None:
        self._cerrado = True
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None
