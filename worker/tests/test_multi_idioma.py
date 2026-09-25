"""Varios idiomas de traduccion por sala (25/09): `--traducir-a es,pt,fr`, un Traductor por idioma con
la MISMA key, el MISMO transporte y el MISMO Limitador; un `translation` por idioma (meta.lang_to) y
`session_start.meta.translations_langs` con la lista completa. Sin API: el ASR sale de un casete real
(transporte de casete rotulado) y el modelo de texto es un FALSO ROTULADO que marca el idioma pedido.
"""
import asyncio
import json
import re

import pytest

from contracts import errores
from worker import run
from worker.ingesta import trocear
from worker.session import ConfigReabrir, SessionWorker
from worker.tests.test_reabrir import ES, _Bus, _voz
from worker.traductor import IDIOMAS, Limitador, Traductor, prompt_lote
from worker.transporte_casete import FabricaCasete


def _a(*extra, lang="en"):
    return run._args(["--archivo", "x.wav", "--sesion", "t-multi", "--lang", lang, *extra])


# ---- parseo ------------------------------------------------------------------------------------
@pytest.mark.parametrize("valor,lang,esperado", [
    ("es,pt,fr", "en", ["es", "pt", "fr"]), ("auto", "en", ["es"]), ("auto", "es", ["en"]),
    ("auto", "pt", ["es"]), ("none", "en", []), ("", "en", []), (" es , pt ", "en", ["es", "pt"]),
    ("auto,pt,es", "en", ["es", "pt"]), ("en,es", "en", ["es"]), ("pt-BR,es", "es", ["pt-BR"]),
])
def test_destinos(valor, lang, esperado):
    assert run.destinos(valor, lang) == esperado


@pytest.mark.parametrize("valor", ["espanol", "ES", "es_AR", "es,xx1"])
def test_destinos_invalidos(valor):
    with pytest.raises(ValueError):
        run.destinos(valor, "en")
    with pytest.raises(SystemExit):
        _a("--traducir-a", valor)


def test_lang_ampliado():
    for x in ["en", "es", "pt", "fr", "de", "it"]:
        assert _a(lang=x).lang == x
    with pytest.raises(SystemExit):
        _a(lang="ru")
    assert set(run.LANGS_PROBADOS) == {"en", "es"}


def test_idiomas_del_prompt():
    for c, n in [("pt", "Portuguese"), ("fr", "French"), ("de", "German"), ("it", "Italian")]:
        assert IDIOMAS[c] == n and f"from English to {n}" in prompt_lote(["hi"], "en", c)


def test_armar_traductores_misma_key_transporte_y_limitador():
    ts = run.armar_traductores(_a("--traducir-a", "es,pt,fr", "--key", "GEMINI_API_KEY_B"))
    assert [t.a for t in ts] == ["es", "pt", "fr"] and {t.de for t in ts} == {"en"}
    assert len({id(t.tr) for t in ts}) == 1 and len({id(t.lim) for t in ts}) == 1
    assert {t.key for t in ts} == {"GEMINI_API_KEY_B"} and ts[0].lim.reservas is not None
    assert run.armar_traductores(_a("--traducir-a", "none")) == []
    assert run.armar_traductor(_a("--traducir-a", "pt,es")).a == "pt"     # compat: el primero


# ---- emision multi-idioma en la sala (casete real + texto FALSO ROTULADO) ----------------------
class TextoFalsoPorIdioma:
    """FALSO, SOLO TESTS: devuelve "[FALSO-<Idioma>] <entrada>" con el idioma que pide el prompt."""

    def __init__(self):
        self.llamadas = []

    async def generar(self, modelo, prompt):
        if "Input:\n" not in prompt:                  # limpieza de turno mergeado: no se ejercita aca
            raise RuntimeError("falso: solo traduccion")
        dst = re.search(r" to (\w+)\.\n", prompt).group(1)
        entrada = json.loads(prompt.split("Input:\n", 1)[1])
        self.llamadas.append((modelo, dst, len(entrada)))
        return json.dumps([f"[FALSO-{dst}] {x}" for x in entrada], ensure_ascii=False)


def _correr(tmp_path, langs):
    fab = FabricaCasete(str(ES), 25.0)
    bus = _Bus()
    T0 = 1_790_000_000.0
    chs = list(trocear(_voz(30.0), t0_epoch=T0))
    tr = TextoFalsoPorIdioma()
    lim = Limitador(["m-a", "m-b"], log=None)
    trads = [Traductor("es", x, transporte=tr, modelos=["m-a", "m-b"], log=tmp_path / "cuota-texto.log",
                       limitador=lim, logger=lambda s: None) for x in langs]
    cfg = ConfigReabrir()
    cfg.drenaje_vieja_s, cfg.tick_s = 0.3, 0.05
    caja = {}

    async def fuente():
        for c in chs:
            yield c
            await asyncio.sleep(0)

    async def dormir(_s):
        await asyncio.sleep(0)

    async def main():
        w = SessionWorker("sala-multi", "es", fab(0.0), fuente(), emisor=bus, heartbeat_s=0.05,
                          espera_final_s=0.3, reloj=lambda: T0 + caja["w"].pos_s, dormir=dormir,
                          log=lambda s: None, fabrica=fab, reabrir=cfg, titulo="multi",
                          traductor=trads)
        caja["w"] = w
        return await w.correr()

    asyncio.run(main())
    return bus.msgs, tr


def test_sala_emite_un_translation_por_idioma(tmp_path):
    msgs, tr = _correr(tmp_path, ["en", "pt"])
    st = next(m for m in msgs if m["type"] == "session_start")
    assert st["meta"]["translations_langs"] == ["en", "pt"]
    textos = {m["seq"]: m["text"] for m in msgs if m["type"] == "text"}
    assert textos
    por_lang = {"en": {}, "pt": {}}
    for m in msgs:
        if m["type"] == "translation":
            for it in m["items"]:
                por_lang[m["meta"]["lang_to"]][it["seq"]] = it
    nombres = {"en": "English", "pt": "Portuguese"}
    for lang, items in por_lang.items():
        assert set(items) == set(textos), lang                       # cada text, en cada idioma
        for seq, it in items.items():
            assert it["ok"] and it["text"] == f"[FALSO-{nombres[lang]}] {textos[seq]}"
    assert {d for _m, d, _n in tr.llamadas} == {"English", "Portuguese"}
    malos = [(m["type"], errores(m)) for m in msgs if errores(m)]
    assert not malos, malos[:3]


def test_sala_un_idioma_como_antes(tmp_path):
    msgs, _ = _correr(tmp_path, ["en"])
    st = next(m for m in msgs if m["type"] == "session_start")
    assert st["meta"]["translations_langs"] == ["en"]
    assert {m["meta"]["lang_to"] for m in msgs if m["type"] == "translation"} == {"en"}
