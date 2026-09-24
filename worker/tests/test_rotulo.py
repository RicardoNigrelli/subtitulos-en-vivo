"""B4 (anti-alucinacion): todo mensaje que sale del TRANSPORTE DE CASETE va rotulado: replay:true y
meta.source="transporte-casete"; el session_start lleva "[TEST] " en meta.title. Contrato:
contracts/README.md ("replay: true si viene de un casete"). Hallazgo: reportes/adversario-b3.md §2.2.

Fuente: senal sintetica (no es voz); lo que "dice el server" sale del casete real; traductor con el
transporte FALSO ROTULADO de test_traductor.py (devuelve "[FALSO-es] ...", no es Gemini).
"""
import asyncio

from contracts import errores
from worker.ingesta import trocear
from worker.session import ConfigReabrir, SessionWorker
from worker.tests.test_reabrir import ES, _Bus, _voz
from worker.tests.test_traductor import TransporteFalsoRotulado
from worker.traductor import Limitador, Traductor
from worker.transporte_casete import FabricaCasete


def _correr_casete(tmp_path, dur_s=40.0, mudo=25.0):
    fab = FabricaCasete(str(ES), mudo)
    bus = _Bus()
    T0 = 1_790_000_000.0                      # epoch plausible: el contrato exige t_* >= 1e9
    chs = list(trocear(_voz(dur_s), t0_epoch=T0))
    caja = {}
    trad = Traductor("es", "en", transporte=TransporteFalsoRotulado(), modelos=["m-a", "m-b"],
                     log=tmp_path / "cuota-texto.log", limitador=Limitador(["m-a", "m-b"], log=None),
                     logger=lambda s: None)
    cfg = ConfigReabrir()
    cfg.drenaje_vieja_s, cfg.tick_s = 0.3, 0.05

    async def fuente():
        for c in chs:
            yield c
            await asyncio.sleep(0)

    async def dormir(_s):
        await asyncio.sleep(0)

    async def main():
        w = SessionWorker("sala-b4", "es", fab(0.0), fuente(), emisor=bus, heartbeat_s=0.05,
                          espera_final_s=0.3, reloj=lambda: T0 + caja["w"].pos_s, dormir=dormir,
                          log=lambda s: None, fabrica=fab, reabrir=cfg, titulo="clip-de-prueba",
                          source={"file": "x.wav"}, traductor=trad)
        caja["w"] = w
        return await w.correr()

    res = asyncio.run(main())
    return res, bus.msgs


def test_transporte_casete_rotula_todo_mensaje_y_valida_contra_el_contrato(tmp_path):
    res, msgs = _correr_casete(tmp_path)
    tipos = {m["type"] for m in msgs}
    # hay de todo: inicio, textos, traducciones, rotacion (mudo desde 25 s), heartbeat y fin
    assert {"session_start", "text", "translation", "rotation", "heartbeat", "session_end"} <= tipos, tipos
    sin_rotulo = [m for m in msgs if m["replay"] is not True
                  or (m.get("meta") or {}).get("source") != "transporte-casete"]
    assert not sin_rotulo, sin_rotulo[:2]
    st = next(m for m in msgs if m["type"] == "session_start")
    assert st["meta"]["title"].startswith("[TEST] ")
    assert st["meta"]["source_original"] == {"file": "x.wav"}      # no se pierde el origen
    assert st["meta"]["translations_langs"] == ["en"]
    malos = [(m["type"], errores(m)) for m in msgs if errores(m)]
    assert not malos, malos[:3]


def test_transporte_real_no_se_rotula():
    class TransporteSinRotulo:          # no declara SOURCE_REPLAY (como worker.transporte.TransporteGemini)
        pass

    w = SessionWorker("sala-real", "en", TransporteSinRotulo(), fuente=None, log=lambda s: None)
    m = w.emitir("session_start", meta={"title": "t", "source": {"file": "a.wav"}})
    assert w.rotulo is None and m["replay"] is False and m["meta"]["source"] == {"file": "a.wav"}
    from worker.transporte import TransporteGemini
    assert getattr(TransporteGemini, "SOURCE_REPLAY", None) is None
