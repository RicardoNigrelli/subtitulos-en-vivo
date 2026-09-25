"""B3: "reabrir con solape" (worker/session.py) contra casetes REALES, sin API.

Transporte: worker/transporte_casete.py (rotulado test/replay): entrega las lineas server del casete
sincronizadas con el audio que el SessionWorker envia. La fuente es una senal SINTETICA (tono con
valles, no es voz ni transcripcion) solo para que el Cortador arme ventanas; lo que "dice el server"
sale del casete real. Reloj virtual = posicion de audio de la fuente (sin tiempo real).
"""
import asyncio
from pathlib import Path

import numpy as np

from worker.ingesta import SR, trocear
from worker.session import ConfigReabrir, SessionWorker
from worker.transporte_casete import FabricaCasete

CASETES = Path(__file__).resolve().parents[2] / "fixtures" / "casetes"
EN = CASETES / "b1-nerdearla-en-intento2-quota.jsonl"
ES = CASETES / "b1-nerdearla-es-intento2-cancelled.jsonl"


def _voz(total_s: float) -> bytes:
    rng = np.random.default_rng(7)
    partes, t = [], 0.0
    while t < total_s:
        for seg, amp in ((1.8, 4000), (1.2, 0)):   # p25 cae en el valle: has_voice True
            n = int(round(seg * SR))
            x = amp * np.sin(2 * np.pi * 220 * np.arange(n) / SR) + rng.normal(0, 30, n)
            partes.append(np.clip(x, -32768, 32767).astype("<i2"))
            t += seg
    pcm = np.concatenate(partes).tobytes()
    return pcm[: int(total_s * SR) * 2]


class _Bus:
    def __init__(self):
        self.msgs = []

    def publicar(self, m):
        self.msgs.append(m)


def correr(casete: Path, dur_s: float, mudo=None, cfg: ConfigReabrir | None = None, dedup: bool = True):
    fab = FabricaCasete(str(casete), mudo)
    bus = _Bus()
    chs = list(trocear(_voz(dur_s), t0_epoch=0.0))
    caja = {}

    async def fuente():
        for c in chs:
            yield c

    async def dormir(_s):
        await asyncio.sleep(0)

    cfg = cfg or ConfigReabrir()
    cfg.drenaje_vieja_s, cfg.tick_s = 0.3, 0.05

    async def main():
        w = SessionWorker("sala-b3", "en", fab(0.0), fuente(), emisor=bus, heartbeat_s=10_000,
                          espera_final_s=0.3, reloj=lambda: caja["w"].pos_s, dormir=dormir,
                          log=lambda s: None, fabrica=fab, reabrir=cfg, dedup=dedup)
        caja["w"] = w
        return await w.correr()

    res = asyncio.run(main())
    return res, bus.msgs, fab


def _seq_continuo(msgs):
    seqs = [m["seq"] for m in msgs if m["seq"] is not None]
    assert seqs == list(range(1, len(seqs) + 1)), "seq con hueco o repetido"
    assert all(m["session_id"] == "sala-b3" for m in msgs)   # la sala publica no cambia
    return seqs


def _fin_casete(fab):
    return fab.guion.ventanas[-1][0] + 1.0


def test_en_quota_atasco_y_cierre_seq_continuo():
    fab0 = FabricaCasete(str(EN))
    res, msgs, fab = correr(EN, _fin_casete(fab0) + 8.0)   # la fuente pasa el cierre del casete
    rots = res.rotaciones
    razones = [r["reason"] for r in rots]
    assert "atasco" in razones and "cierre" in razones, razones
    # cierre real del casete: linea server close (1011 Resource exhausted, 449,8 s de edad)
    env_close = next(l.env for l in fab.guion.lineas if l.kind == "close")
    pos_close = fab.guion.pos_de_env(env_close)
    cierre = [r for r in rots if r["reason"] == "cierre"]
    assert abs(cierre[0]["pos_s"] - pos_close) <= 8.0, (cierre[0], pos_close)
    assert cierre[0]["code"] == 1011
    # primer atasco con los defaults: episodio 2 del casete (offset trabado en 55,0/60,1 s)
    at = [r for r in rots if r["reason"] == "atasco"]
    assert at[0]["pos_s"] <= 90.0, at[0]
    assert all(r["new_id"] != r["old_id"] for r in rots)
    tipos = [m["type"] for m in msgs]
    assert tipos.count("rotation") == len(rots)
    seqs = _seq_continuo(msgs)
    assert len(set(seqs)) == len(seqs)
    assert res.textos > 0


def _tramos_sin_texto(casete_salida: str, hasta_s: float = 1e9):
    """Tramos contiguos de ventanas CON VOZ enviadas y sin ningun `text` que las cubra
    (qa/cobertura.py::cobertura_casete), recortados a `hasta_s`."""
    from qa.cobertura import cobertura_casete
    iv = sorted((v["audio_start"], v["audio_end"]) for v in cobertura_casete(casete_salida)["ventanas_sin_texto"])
    tramos = []
    for a, b in iv:
        if tramos and a <= tramos[-1][1] + 0.05:
            tramos[-1][1] = max(tramos[-1][1], b)
        else:
            tramos.append([a, b])
    return [(a, min(b, hasta_s)) for a, b in tramos if a < hasta_s]


def test_es_cancelled_default_sin_tramo_mudo_mayor_a_15s_antes_de_la_muerte_del_server(tmp_path):
    """Con el default (22/8) se cumple 'ningun tramo con voz enviada y sin texto > 15 s' en 0-200 s del
    casete ES. Despues de ~217 s el server MUERE (cancelled) y el transporte de casete repite esa muerte en
    las conexiones nuevas: ese tramo es medicion documentada (reportes/audio-pipeline-umbral.md)."""
    from worker.umbrales import fila
    d = ConfigReabrir()
    fin_es = FabricaCasete(str(ES)).guion.ventanas[-1][0] + 1.0
    f = fila("es", ES, fin_es, d.atasco_umbral_s, d.atasco_sostenido_s, tmp_path)
    tramos = _tramos_sin_texto(f["casete_salida"], 200.0)
    assert all(b - a <= 15.0 for a, b in tramos), tramos
    assert f["reaperturas"] >= 1                     # se reporta, no se topea (ver reportes/audio-pipeline-umbral.md)


def _muda(mudo_desde: float, dur_s: float):
    res, msgs, fab = correr(ES, dur_s, mudo=mudo_desde)
    pos_mudo = fab.guion.pos_de_env(mudo_desde)
    # se mira el primer atasco DESPUES del mudo (con umbrales bajos hay uno antes, episodio ES ~37-62 s)
    at = [r for r in res.rotaciones if r["reason"] == "atasco" and r["pos_s"] > pos_mudo]
    assert at, res.rotaciones
    assert 0 < at[0]["pos_s"] - pos_mudo <= 15.0, (at[0], pos_mudo)
    assert any(c.descartadas_mudo > 0 for c in fab.conexiones) and not fab.conexiones[-1].muda
    return res, msgs, at[0]


def test_sesion_muda_desde_120_senal_mudo_en_tramo_sano():
    # 120 s de audio enviado cae en un tramo sano del casete ES: dispara la senal "mudo" (sin texto)
    _, msgs, r = _muda(120.0, 160.0)
    assert r["detalle"] == "mudo", r
    _seq_continuo(msgs)


def test_sesion_muda_desde_60_reabre_en_15s_y_la_nueva_anda():
    # 60 s cae en la cola del episodio ES de t~37-62 (offset trabado): dispara "atraso" (el offset
    # que en el casete destrababa a t~56 ya no llega), igual dentro de los 15 s
    res, msgs, r = _muda(60.0, 110.0)
    assert r["detalle"] in ("mudo", "atraso"), r
    # B4: la rotacion estima el audio que queda sin texto (atraso ~31 s > reenvio max 15 s)
    assert r["audio_lost_s"] > 0 and r["reenvio_s"] <= 15.0, r
    a, b = r["audio_lost_rango"]
    assert 0.0 <= a < b <= r["corte_s"] + 0.5, r
    # la sesion nueva anda: llegan textos despues de la rotacion
    i_rot = next(i for i, m in enumerate(msgs) if m["type"] == "rotation")
    assert any(m["type"] == "text" for m in msgs[i_rot + 1:])
    _seq_continuo(msgs)


def test_perfil_agresivo_6_4_detecta_en_temprano_pero_da_falso_positivo_en_es():
    """Documenta el conflicto del pedido (reportes/audio-pipeline-b3.md): con UMBRAL 6 / SOSTENIDO 4
    el episodio EN de los ~5 s se detecta en <= 15 s, pero el episodio ES de los ~37 s (que se
    recupero solo, cobertura 1,0) TAMBIEN dispara antes de los 200 s. No se puede tener las dos."""
    agresivo = ConfigReabrir(atasco_umbral_s=6.0, atasco_sostenido_s=4.0)
    res_en, msgs_en, _ = correr(EN, 60.0, cfg=agresivo)
    at_en = [r for r in res_en.rotaciones if r["reason"] == "atasco"]
    assert at_en and at_en[0]["pos_s"] <= 5.0 + 15.0, at_en[:1]
    _seq_continuo(msgs_en)
    agresivo_es = ConfigReabrir(atasco_umbral_s=6.0, atasco_sostenido_s=4.0)
    res_es, _, _ = correr(ES, 200.0, cfg=agresivo_es)
    assert any(r["reason"] == "atasco" and r["pos_s"] < 200.0 for r in res_es.rotaciones)


def test_medicion_vf2_en_modelo_documentada(tmp_path):
    """MEDICION DOCUMENTADA, no asercion de politica (reportes/audio-pipeline-umbral.md). vf2-en (ROJO 1
    de verificacion-final2): la conexion vieja reproduce las lineas REALES de c1 y las nuevas una sesion
    sana del mismo clip (worker/umbrales.py::fila_vf2). El modelo NO reproduce el vivo: el texto tardio
    de c1 queda anclado al audio enviado y llega antes de la rotacion, y la c2 real quedo muda. Solo se
    exige que el escenario corra y que el atraso de c1 dispare una reapertura por atasco."""
    from worker.umbrales import fila_vf2
    d = ConfigReabrir()
    f = fila_vf2(CASETES / "vf2-smoke-final2-en.jsonl", d.atasco_umbral_s, d.atasco_sostenido_s, tmp_path)
    assert any(p[0] == "atasco" for p in f["posiciones"]), f["posiciones"]
    print("vf2-en", f["perfil"], f["posiciones"], "tramo_max", f["tramo_max_s"], "final", f["sin_texto_al_final_s"])
