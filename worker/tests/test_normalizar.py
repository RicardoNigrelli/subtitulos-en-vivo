"""Normalizacion de pegados del servicio degradado (casete muestra-en-20260925-094954, 09:50).

Los strings reales se leen DEL CASETE (byte a byte) y ademas se comprueba que los literales copiados
de abajo esten en el. La limpieza con el modelo de texto se prueba con un transporte FALSO ROTULADO
(sin API): valida el camino (cola, orden de seq, validacion, caidas), no lo que diria Gemini.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.mapeo import Asignacion
from worker.normalizar import marcas_pegado, normalizar, salida_limpieza, validar_limpieza
from worker.session import SessionWorker
from worker.traductor import Limitador

CASETE = Path(__file__).resolve().parents[2] / "fixtures" / "casetes" / "muestra-en-20260925-094954.jsonl"
VOCAB = ["Nerdearla", "Grady Booch"]


def _server(campo):
    out = []
    for linea in CASETE.read_text(encoding="utf-8").splitlines():
        d = json.loads(linea)
        it = ((d.get("payload") or {}).get("serverContent") or {}).get(campo)
        if d.get("dir") == "server" and it:
            out.append(it["text"])
    return out


def finales():
    return _server("inputTranscription")


def interinos():
    return _server("interimInputTranscription")


# ---- strings reales del casete ----------------------------------------------------------------
def test_literales_estan_en_el_casete():
    crudo = CASETE.read_text(encoding="utf-8")
    for s in ["obvious.1940s", "case?you", "astonishinglycontroversial", "morein", "extremelyThis",
              "saysay", "always always19", "build build", "thoseIn", "theWell"]:
        assert s in crudo, s


def test_casete_final_1_punto_pegado():
    t1 = finales()[0]
    out, c = normalizar(t1, VOCAB)
    assert "obvious.1940s" in t1 and "obvious. 1940s" in out
    assert c["puntuacion"] == 1 and c["camel"] == 0
    assert "saysay" in out                      # regla 4: sin marca, no se toca


def test_casete_final_2_merge():
    t2 = finales()[1]
    out, c = normalizar(t2, VOCAB, mergeado=True, repetidas=False)
    assert "the case? you do" in out
    assert "extremely This this trade-off" in out
    assert "those In those days" in out and "for the Well, with" in out
    for s in ["astonishinglycontroversial", "morein", "thatbuild build", "humansalways", "moremore",
              "inhappen"]:
        assert s in out, s                      # sin marca: comportamiento del servicio degradado
    assert "PDP-11 60 series" in out
    assert c == {"puntuacion": 1, "camel": 3, "repetidas": 0}
    assert marcas_pegado(t2, VOCAB)


def test_casete_parcial_always_always19():
    p = [x for x in interinos() if "always always19" in x][0]
    out, c = normalizar(p, VOCAB)               # regla 3 apagada por defecto
    assert "always always19" in out and c["repetidas"] == 0


def test_casete_sano_no_se_toca():
    n = 0
    for f in ["b1-en-60s", "b4-trad-vivo-en"]:
        ruta = CASETE.with_name(f + ".jsonl")
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            d = json.loads(linea)
            if d.get("dir") == "emit" and d.get("kind") == "text":
                t = d["payload"]["text"]
                assert normalizar(t, VOCAB)[0] == t, t
                n += 1
    assert n > 20


# ---- regla 1: negativos y positivos -----------------------------------------------------------
@pytest.mark.parametrize("s", [
    "It costs 3.5 dollars", "at 10:30 in the morning", "e.g. this one", "the U.S. and U.S.A",
    "go to nerdearla.org now", "https://nerdearla.org/agenda?dia=2", "www.nerdearla.org",
    "mail me at a.b@x.org", "1,000 users", "std::vector", "ASP.NET and .NET", "version 2.0.1",
    "file.py and Node.js", "wait... what", "i.e. So", "16:9 video", "C:\\Users\\x",
])
def test_puntuacion_negativos(s):
    assert normalizar(s)[0] == s


@pytest.mark.parametrize("s,esperado", [
    ("obvious.1940s", "obvious. 1940s"), ("case?you", "case? you"), ("hi!Now", "hi! Now"),
    ("first,second", "first, second"), ("one;two", "one; two"), ("note:this", "note: this"),
    ("Mr.Smith", "Mr. Smith"), ("wait...So", "wait... So"), ("¿Qué?¿Sí?", "¿Qué? ¿Sí?"),
    ("dijo.Entonces", "dijo. Entonces"), ("es así,pero", "es así, pero"),
])
def test_puntuacion_positivos(s, esperado):
    assert normalizar(s)[0] == esperado


# ---- regla 2 ------------------------------------------------------------------------------------
@pytest.mark.parametrize("s", ["YouTube", "GitHub", "JavaScript", "iPhone", "OpenAI", "DevOps",
                               "PowerShell", "macOS", "iOS", "eBay", "McDonald", "PDP-11",
                               "useState", "LinkedIn"])
def test_camel_negativos(s):
    assert normalizar(f"I use {s} daily")[0] == f"I use {s} daily"


def test_camel_vocab_de_la_sala():
    assert normalizar("we run kubeCtl here")[0] == "we run kube Ctl here"
    assert normalizar("we run kubeCtl here", vocab=["kubeCtl", "Nerdearla"])[0] == "we run kubeCtl here"


@pytest.mark.parametrize("s,esperado", [
    ("extremelyThis", "extremely This"), ("extremadamenteEste", "extremadamente Este"),
    ("laBueno", "la Bueno"), ("humansAlways", "humans Always"),
])
def test_camel_positivos(s, esperado):
    assert normalizar(s)[0] == esperado


# ---- regla 3: apagada por defecto; nunca fuera de un turno mergeado ------------------------------
def test_repetidas_apagada_por_defecto(monkeypatch):
    monkeypatch.delenv("NORMALIZAR_REPETIDAS", raising=False)
    assert normalizar("we build build things", mergeado=True)[0] == "we build build things"


def test_repetidas_prendida_solo_en_mergeado(monkeypatch):
    monkeypatch.setenv("NORMALIZAR_REPETIDAS", "1")
    assert normalizar("we build build things", mergeado=True)[0] == "we build things"
    assert normalizar("Wasn't always always19", mergeado=True)[0] == "Wasn't always19"
    assert normalizar("we build build things", mergeado=False)[0] == "we build build things"
    assert normalizar("a a b", mergeado=True)[0] == "a a b"          # < 3 letras


def test_repetidas_no_distingue_habla_real(monkeypatch):
    """Por que la regla 3 esta apagada: en el MISMO turno mergeado del casete, "This this" es habla
    real (los casetes sanos del mismo clip la tienen) y la regla la borraria."""
    sano = [json.loads(x)["payload"]["text"]
            for x in CASETE.with_name("b1-en-60s.jsonl").read_text(encoding="utf-8").splitlines()
            if '"dir": "emit", "kind": "text"' in x]
    assert any("this this tradeoff" in t for t in sano)
    monkeypatch.setenv("NORMALIZAR_REPETIDAS", "1")
    out = normalizar(finales()[1], VOCAB, mergeado=True)[0]
    assert "This this" not in out                # se perderia una repeticion real


# ---- validacion de la limpieza ------------------------------------------------------------------
T2 = ("be an astonishinglycontroversial idea. Why is that the case?you do a function call that "
      "requires two or three morein the days of computing back then, extremelyThis this trade-off")


def test_validar_ok_separando_pegadas():
    bien = ("be an astonishingly controversial idea. Why is that the case? You do a function call "
            "that requires two or three more in the days of computing back then, extremely. This "
            "this trade-off")
    assert validar_limpieza(T2, bien, "en") == (True, "ok")


@pytest.mark.parametrize("malo,motivo", [
    (T2.replace("requires", "demands!"), "palabra nueva"),        # sinonimo, mismo largo
    ("ser una idea asombrosamente controvertida. ¿Por qué es el caso? Hacés una llamada a función "
     "que requiere dos o tres más en los días de entonces, extremadamente", "lengua"),
    ("be an astonishingly controversial idea.", "largo"),
    ("", "vacio"),
])
def test_validar_rechaza(malo, motivo):
    ok, porque = validar_limpieza(T2, malo, "en")
    assert not ok and porque.startswith(motivo), porque


def test_salida_real_de_la_limpieza():
    """Respuesta REAL del modelo de texto (10:03, reportes/audio-pipeline-normalizar-real2.json):
    vino envuelta en un arreglo JSON; se desenvuelve y pasa la validacion."""
    d = json.loads((CASETE.parents[2] / "reportes" / "audio-pipeline-normalizar-real2.json")
                   .read_text(encoding="utf-8"))
    it = d["items"][0]
    assert it["crudo"] == finales()[1]
    assert it["raw"].lstrip().startswith("[")
    lim = salida_limpieza(it["raw"])
    assert lim.startswith("be an astonishingly controversial idea.")
    assert validar_limpieza(it["conservador"], lim, "en") == (True, "ok")
    assert salida_limpieza('[ "hola mundo" ]') == "hola mundo"


def test_salida_limpieza_quita_envoltorio():
    assert salida_limpieza('```\n"hola  mundo"\n```') == "hola mundo"


# ---- camino en la sala con transporte FALSO ROTULADO -------------------------------------------
class TextoFalsoRotulado:
    """FALSO, rotulado: NO es Gemini. Devuelve la entrada con un pegado separado a mano (o lo que
    diga `modo`) para ejercitar cola, orden de seq y validacion."""
    SOURCE_REPLAY = "test-falso"

    def __init__(self, modo="bien", demora=0.05):
        self.modo, self.demora, self.prompts = modo, demora, []

    async def generar(self, modelo, prompt):
        self.prompts.append(prompt)
        await asyncio.sleep(self.demora)
        texto = prompt.split("Transcript:\n", 1)[1]
        if self.modo == "bien":
            return texto.replace("astonishinglycontroversial", "astonishingly controversial")
        if self.modo == "parafrasis":
            return "It was a very controversial idea back then, and costly too, we had a trade-off."
        raise RuntimeError("caida")


class Bus:
    def __init__(self):
        self.msgs = []

    def publicar(self, m):
        self.msgs.append(m)


def _sala(modo, limpieza=True, demora=0.05):
    tr = TextoFalsoRotulado(modo, demora)
    anot = []
    trad = SimpleNamespace(tr=tr, lim=Limitador(["m-a"], log=None), agregar=lambda *a: None,
                           _anotar=lambda *a: anot.append(a), a="es")
    bus = Bus()
    w = SessionWorker("s", "en", object(), None, emisor=bus, traductor=trad, vocab=VOCAB,
                      limpieza=limpieza, log=lambda s: None)
    return w, bus, tr, anot


def _asig(text, ventanas, a0):
    return Asignacion(0.0, text, a0, a0 + 3.0, 0.0, ventanas)


def _correr(w, asigs):
    async def go():
        w._emitir_asignaciones([asigs[0]])
        w._emitir_asignaciones(asigs[1:])        # llega detras mientras limpia: espera en la cola
        if w._limpiando is not None:
            await w._limpiando
    asyncio.run(go())


def _textos(bus):
    return [(m["seq"], m["text"]) for m in bus.msgs if m["type"] == "text"]


def test_sala_limpia_mergeado_y_respeta_orden():
    w, bus, tr, anot = _sala("bien")
    _correr(w, [_asig(T2, [1, 2, 3], 10.0), _asig("And then we moved on.", [4], 13.0)])
    textos = _textos(bus)
    assert [s for s, _ in textos] == [1, 2]
    assert "astonishingly controversial" in textos[0][1] and "case? you" in textos[0][1]
    assert textos[1][1] == "And then we moved on."
    assert len(tr.prompts) == 1 and anot[0][2] == "limpieza_ok" and w.res.limpiezas_ok == 1


def test_sala_parafrasis_se_descarta():
    w, bus, tr, _ = _sala("parafrasis")
    _correr(w, [_asig(T2, [1, 2], 10.0)])
    assert _textos(bus) == [(1, normalizar(T2, VOCAB)[0])] and w.res.limpiezas_fallidas == 1


def test_sala_caida_y_timeout_emiten_conservador(monkeypatch):
    import worker.session as S
    monkeypatch.setattr(S, "LIMPIEZA_TIMEOUT_S", 0.2)
    for modo, demora, estado in [("caida", 0.01, "limpieza_error:RuntimeError"),
                                 ("bien", 1.0, "limpieza_timeout")]:
        w, bus, _, anot = _sala(modo, demora=demora)
        _correr(w, [_asig(T2, [1, 2], 10.0)])
        assert _textos(bus) == [(1, normalizar(T2, VOCAB)[0])]
        assert anot[0][2] == estado


def test_sala_limpieza_apagada_y_turno_sano_sin_llamada():
    w, bus, tr, _ = _sala("bien", limpieza=False)
    _correr(w, [_asig(T2, [1, 2], 10.0)])
    assert tr.prompts == [] and _textos(bus) == [(1, normalizar(T2, VOCAB)[0])]
    w, bus, tr, _ = _sala("bien")
    _correr(w, [_asig("A normal sentence, nothing glued.", [7], 20.0)])
    assert tr.prompts == [] and _textos(bus) == [(1, "A normal sentence, nothing glued.")]


def test_env_apaga_limpieza(monkeypatch):
    monkeypatch.setenv("LIMPIEZA_MERGE", "0")
    tr = TextoFalsoRotulado()
    w = SessionWorker("s", "en", object(), None, traductor=SimpleNamespace(tr=tr), log=lambda s: None)
    assert w.limpieza is False
