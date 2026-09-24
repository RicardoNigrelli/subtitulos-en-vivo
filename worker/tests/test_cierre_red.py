"""B7: reapertura tras un cierre 1006 que aparece EN PLENO ENVIO (intento corto de B5, sin API).

Casete real de la falla: fixtures/casetes/b5-qa-b5-en-cierre1006.jsonl (copia de
qa/out/smoke-b5-qa-b5-en.casete.jsonl). Ahi el envio se TRABO con 39,6 s de audio enviado (los
heartbeats 16:38:21, :26 y :31 repiten audio_seconds_sent 39.6 y la ventana 15 nunca cierra), el
watchdog pidio reabrir (atasco/mudo) pero la reapertura solo se ejecuta en el lazo de envio, que estaba
colgado; a los ~10 s el receptor vio close 1006 "sin frame de cierre" (by red) y el MISMO envio
fallo con ConnectionClosedError: la excepcion salio de _enviar y la sala termino con
session_end server_cerro, sin `connect` c2 ni `rotation`.

Aca se reproduce ese mecanismo con el transporte de casete (rotulado, test): la linea close y la
posicion (39,6 s) salen del casete de B5; lo que "dice el server" antes y despues sale del casete real
largo de test_reabrir (b1 EN). La fuente es la senal SINTETICA de test_reabrir (no es voz).
"""
import asyncio
import json

from worker.ingesta import trocear
from worker.session import ConfigReabrir, SessionWorker
from worker.transporte_casete import FabricaCasete
from worker.tests.test_reabrir import CASETES, EN, _Bus, _voz

B5 = CASETES / "b5-qa-b5-en-cierre1006.jsonl"


def _b5():
    """(linea server close, audio_seconds_sent del ultimo heartbeat antes del close) del casete B5."""
    audio_s = None
    for linea in open(B5, encoding="utf-8"):
        d = json.loads(linea)
        if d.get("dir") == "server" and d.get("kind") == "close":
            return d["payload"], audio_s
        if d.get("kind") == "heartbeat":
            audio_s = d["payload"]["meta"]["audio_seconds_sent"]
    raise AssertionError("el casete B5 no tiene close")


def correr(red_bloqueo_s, envio_timeout_s=5.0, dur_s=80.0):
    close, audio_s = _b5()
    fab = FabricaCasete(str(EN), red_caida=audio_s, red_bloqueo_s=red_bloqueo_s, red_close=close)
    bus = _Bus()
    chs = list(trocear(_voz(dur_s), t0_epoch=0.0))
    caja = {}

    async def fuente():
        for c in chs:
            yield c

    async def dormir(_s):
        await asyncio.sleep(0)

    cfg = ConfigReabrir()
    cfg.drenaje_vieja_s, cfg.tick_s, cfg.envio_timeout_s = 0.3, 0.05, envio_timeout_s

    async def main():
        w = SessionWorker("sala-b7", "en", fab(0.0), fuente(), emisor=bus, heartbeat_s=10_000,
                          espera_final_s=0.3, reloj=lambda: caja["w"].pos_s, dormir=dormir,
                          log=lambda s: None, fabrica=fab, reabrir=cfg)
        caja["w"] = w
        return await w.correr()

    # tope duro: con el codigo de B5 un envio colgado no vuelve nunca (el test falla, no se cuelga)
    res = asyncio.run(asyncio.wait_for(main(), 60))
    return res, bus.msgs, fab, close, audio_s


def _chequear(msgs, codigo):
    seqs = [m["seq"] for m in msgs if m["seq"] is not None]
    assert seqs == list(range(1, len(seqs) + 1)), "seq con hueco o repetido"
    rots = [m for m in msgs if m["type"] == "rotation"]
    assert rots, "no hubo rotation (B5: la sala termino con server_cerro)"
    r = rots[0]["meta"]
    assert r["reason"] == "cierre" and r.get("code") == codigo, r
    assert r["old_id"] == "c1" and r["new_id"] == "c2", r
    despues = [m for m in msgs if m["type"] == "text" and m["seq"] > rots[0]["seq"]]
    assert len(despues) >= 3, f"solo {len(despues)} textos despues de la reapertura"
    fin = [m for m in msgs if m["type"] == "session_end"]
    assert len(fin) == 1 and fin[0]["meta"]["reason"] == "fin_fuente", fin
    return r, despues


def test_cierre_1006_en_pleno_envio_reabre_con_rotation_cierre_y_sigue_emitiendo():
    res, msgs, fab, close, audio_s = correr(red_bloqueo_s=0.2)
    assert close["code"] == 1006 and close["by"] == "red" and audio_s == 39.6   # datos del casete B5
    assert fab.red_usada and fab.n >= 2   # c3 puede venir despues por el atasco propio del casete b1 EN
    r, despues = _chequear(msgs, 1006)
    assert r.get("pos_s", 0) >= 30.0
    assert any("envio" in e for e in res.errores)


def test_envio_colgado_sin_cierre_vence_el_tope_y_reabre():
    # la red se traba y el server nunca cierra: solo el tope de envio (ENVIO_TIMEOUT_S) destraba
    res, msgs, fab, _, _ = correr(red_bloqueo_s=None, envio_timeout_s=0.3)
    r, _ = _chequear(msgs, None)
    assert r.get("por") == "envio", r
