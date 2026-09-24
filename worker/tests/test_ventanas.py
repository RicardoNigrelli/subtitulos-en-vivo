"""Cortador de ventanas: corte en valle, corte forzado, umbral p25 de los primeros 10 s, gap de 0,7 s,
solape de 400 ms reenviado en rafaga SIN dormir (a nivel SessionWorker).

Senal sintetica (no es voz ni transcripcion): tramos de tono fuerte separados por valles de ruido bajo.
"""
import asyncio

import numpy as np

from worker.ingesta import CHUNK_BYTES, SR, Chunk, rms_pcm16, trocear
from worker.session import SessionWorker
from worker.ventanas import Cortador

rng = np.random.default_rng(1234)


def _pcm(segmentos):
    """segmentos: lista de (segundos, amplitud). Tono 220 Hz * amplitud + ruido chico."""
    partes = []
    for seg, amp in segmentos:
        n = int(round(seg * SR))
        t = np.arange(n) / SR
        x = amp * np.sin(2 * np.pi * 220 * t) + rng.normal(0, 30, n)
        partes.append(np.clip(x, -32768, 32767).astype("<i2"))
    return np.concatenate(partes).tobytes()


def _voz_con_valles(total_s=40.0, voz_s=2.5, valle_s=0.3):
    segs, t = [], 0.0
    while t < total_s:
        segs += [(voz_s, 4000), (valle_s, 0)]
        t += voz_s + valle_s
    return _pcm(segs)


def _cortar(pcm):
    c = Cortador()
    acciones = []
    for ch in trocear(pcm, t0_epoch=1000.0):
        acciones += c.push(ch)
    acciones += c.cerrar()
    return c, acciones


def test_chunk_es_100ms_3200_bytes():
    assert CHUNK_BYTES == 3200
    chs = list(trocear(b"\x00\x00" * 16000))
    assert len(chs) == 10 and all(len(c.data) == 3200 for c in chs)
    assert chs[-1].audio_end == 1.0


def test_corte_en_valle_y_duraciones():
    c, acc = _cortar(_voz_con_valles())
    ends = [a.ventana for a in acc if a.kind == "end"]
    assert len(ends) >= 10
    for v in ends[:-1]:  # la ultima puede ser "fin"
        assert v.motivo in ("valle", "max")
        assert 2.2 - 1e-6 <= v.dur <= 3.8 + 1e-6, v.resumen()
        if v.motivo == "valle":
            # el ultimo chunk de la ventana es un valle: RMS <= umbral vigente al cortar
            assert v.chunks[-1].rms <= v.umbral + 1e-6
    assert sum(1 for v in ends if v.motivo == "valle") >= len(ends) - 2


def test_corte_forzado_al_maximo_sin_valles():
    # 10 s con muchos valles (umbral bajo) y despues voz continua: sin valles -> corte a 3,8 s
    pcm = _voz_con_valles(total_s=10.0, voz_s=0.5, valle_s=0.5) + _pcm([(20.0, 4000)])
    c, acc = _cortar(pcm)
    ends = [a.ventana for a in acc if a.kind == "end"]
    tardias = [v for v in ends if v.audio_start > 12.0 and v.motivo != "fin"]
    assert tardias, "no hubo ventanas en el tramo continuo"
    assert all(v.motivo == "max" and abs(v.dur - 3.8) < 1e-6 for v in tardias), \
        [v.resumen() for v in tardias]


def test_umbral_percentil_25_de_los_primeros_10_s_y_despues_fijo():
    pcm = _voz_con_valles(total_s=30.0)
    chs = list(trocear(pcm))
    c = Cortador()
    for ch in chs[:100]:
        c.push(ch)
    esperado = float(np.percentile([ch.rms for ch in chs[:100]], 25))
    assert c.umbral_congelado
    assert abs(c.umbral() - esperado) < 1e-6
    for ch in chs[100:]:
        c.push(ch)
    assert abs(c.umbral() - esperado) < 1e-6  # no cambia despues de los 10 s


def test_gap_solape_y_rafaga_sin_perder_audio():
    pcm = _voz_con_valles(total_s=30.0)
    chs = list(trocear(pcm))
    c, acc = _cortar(pcm)
    ventanas = [a.ventana for a in acc if a.kind == "end"]
    for prev, nxt in zip(ventanas, ventanas[1:]):
        # solape: los primeros 4 chunks de la siguiente son los ultimos 4 de la anterior
        assert [x.idx for x in nxt.chunks[:4]] == [x.idx for x in prev.chunks[-4:]]
        # gap: 7 chunks (0,7 s) que llegaron entre activity_end y activity_start van en la rafaga
        gap_idx = [x.idx for x in nxt.chunks[4:11]]
        if nxt.motivo == "fin" and nxt.burst_chunks < 11:
            # la fuente termino durante el gap: la ventana final lleva solo lo que llego
            assert gap_idx == list(range(prev.chunks[-1].idx + 1, prev.chunks[-1].idx + 1 + len(gap_idx)))
            continue
        assert gap_idx == list(range(prev.chunks[-1].idx + 1, prev.chunks[-1].idx + 8))
        assert nxt.burst_chunks == 11
    # las acciones de audio de la rafaga estan marcadas burst=True y salen juntas tras el start
    for i, a in enumerate(acc):
        if a.kind == "start" and a.ventana.idx > 0:
            siguientes = acc[i + 1: i + 1 + a.ventana.burst_chunks]
            assert siguientes and all(s.kind == "audio" and s.burst for s in siguientes)
    # no se pierde audio: todo chunk de entrada se envio al menos una vez
    enviados = {a.chunk.idx for a in acc if a.kind == "audio"}
    assert enviados == {ch.idx for ch in chs}


class _TransporteRegistrador:
    """Doble de transporte para probar el ENVIO. No devuelve ningun texto (no simula a Gemini)."""

    def __init__(self, reloj):
        self.ops = []
        self.reloj = reloj
        self.q = asyncio.Queue()

    async def conectar(self):
        return {"setupComplete": {}}

    async def enviar_audio(self, data):
        self.ops.append(("audio", self.reloj()))

    async def activity_start(self):
        self.ops.append(("start", self.reloj()))

    async def activity_end(self):
        self.ops.append(("end", self.reloj()))

    async def recibir(self):
        while True:
            m = await self.q.get()
            yield m
            if "_close" in m:
                return

    async def cerrar(self):
        await self.q.put({"_close": {"code": 1000, "reason": "", "by": "client"}})


def test_session_worker_rafaga_sin_dormir_y_gap_de_07s():
    reloj_falso = [1000.0]
    dormidas = []

    def reloj():
        return reloj_falso[0]

    async def dormir(s):
        dormidas.append((len(tr.ops), s))
        reloj_falso[0] += s
        await asyncio.sleep(0)

    tr = _TransporteRegistrador(reloj)
    chs = list(trocear(_voz_con_valles(total_s=20.0), t0_epoch=1000.0))

    async def fuente():
        for ch in chs:  # sin tiempo real: los chunks llegan de golpe
            yield ch

    async def main():
        w = SessionWorker("test-rafaga", "en", tr, fuente(), dormir=dormir, reloj=reloj,
                          espera_final_s=0.1, log=lambda s: None)
        return await w.correr()

    res = asyncio.run(main())
    assert res.ventanas >= 5
    kinds = [k for k, _ in tr.ops]
    # cada dormida ocurre justo antes de un activity_start (la del gap), nunca entre audios
    for pos, s in dormidas:
        assert kinds[pos] == "start", (pos, kinds[pos - 2: pos + 2])
        assert s <= 0.7 + 1e-6
    # gap >= 0,7 s de reloj entre cada activity_end y el activity_start siguiente
    t_end = None
    for k, t in tr.ops:
        if k == "end":
            t_end = t
        elif k == "start" and t_end is not None:
            assert t - t_end >= 0.7 - 1e-6
            t_end = None
    # la rafaga (solape + gap = 11 chunks) sale entera sin que avance el reloj
    n_rafagas = 0
    for i, (k, t) in enumerate(tr.ops):
        if k == "start" and i > 0:
            rafaga = []
            for kk, tt in tr.ops[i + 1: i + 12]:
                if kk != "audio":
                    break
                rafaga.append(tt)
            assert rafaga and all(tt == t for tt in rafaga)
            n_rafagas += len(rafaga) == 11
    assert n_rafagas >= res.ventanas - 2
