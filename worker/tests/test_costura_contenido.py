"""ROJO 4 (verificacion final): texto duplicado en la costura de una rotacion por atasco.

Casetes REALES de la corrida de verificacion final (copiados de qa/out/smoke-final-vf-{en,es}.casete.jsonl
a fixtures/casetes/vf-smoke-final-{en,es}.jsonl, sin editar). Son de DOS conexiones (c1 vieja, c2 nueva);
el transporte de casete (worker/transporte_casete.py) reproduce casetes de UNA conexion, asi que aca se
reinyecta, en el orden real, lo que el Mapeador de cada conexion le entrego a
SessionWorker._emitir_asignaciones (texto crudo del server = dedup.original si hubo recorte, ventanas,
audio_start/audio_end, conexion) con la rotacion real del casete (corte_s, pos_s). Es la capa donde se
decide aceptar o descartar; no hay API ni texto inventado. Rotulo de test: todo sale replay:true.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

from worker.mapeo import Asignacion
from worker.session import SOLAPE_S, SessionWorker, _Conexion

CASETES = Path(__file__).resolve().parents[2] / "fixtures" / "casetes"
EN = CASETES / "vf-smoke-final-en.jsonl"
ES = CASETES / "vf-smoke-final-es.jsonl"


class _Bus:
    def __init__(self):
        self.msgs = []

    def publicar(self, m):
        self.msgs.append(m)


def _compacto(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFC", s or "").casefold() if c.isalnum())


def reinyectar(casete: Path):
    """Devuelve (textos emitidos [(conexion, text, a0, a1)], worker)."""
    evs = [json.loads(l) for l in casete.read_text(encoding="utf-8").splitlines() if l.strip()]
    bus = _Bus()
    w = SessionWorker("vf", "xx", None, None, emisor=bus, reloj=lambda: 0.0, log=lambda s: None,
                      rotulo="test-costura-casete")
    cons = {}

    def con(cid):
        if cid not in cons:
            cons[cid] = _Conexion(int(cid[1:]), None, 0.0)
        return cons[cid]

    w.con = con("c1")
    for e in evs:
        p = e.get("payload") or {}
        if e.get("dir") == "emit" and e.get("kind") == "rotation":
            m = p["meta"]
            vieja, nueva = con(m["old_id"]), con(m["new_id"])
            corte = m["corte_s"]
            # lo mismo que hace SessionWorker._rotar al rotar
            vieja.limite_audio_end = round(corte + SOLAPE_S + 0.05, 3)
            vieja.corte_nueva = corte
            nueva.corte_s = corte
            w.viejas.append(vieja)
            w.con = nueva
            w.res.rotaciones.append(dict(m))
        elif e.get("dir") == "emit" and e.get("kind") == "text":
            crudo = (e.get("dedup") or {}).get("original") or p["text"]
            a = Asignacion(t=e["t"], text=crudo, audio_start=p["audio_start"], audio_end=p["audio_end"],
                           t_captured=p["t_captured"], ventanas=e.get("ventanas") or [])
            w._emitir_asignaciones([a], con(e["conexion"]))
        elif e.get("dir") == "client" and e.get("kind") in ("descartado_vieja", "dedup_descartado"):
            raise AssertionError("el casete tiene descartes: ampliar la reinyeccion")
    textos = [m for m in bus.msgs if m["type"] == "text"]
    return textos, w


def _ocurrencias(textos, frase):
    return sum(1 for m in textos if _compacto(frase) in _compacto(m["text"]))


def _palabras(textos):
    return {w for m in textos for w in re.findall(r"\w+", m["text"].casefold())}


def _perdidas(antes: set, despues: set) -> set:
    """Palabras de la corrida real que no estan en la reinyeccion. Un token FUSIONADO por el server en la
    costura ("itAll", "seasu") que ahora sale partido en dos palabras presentes no cuenta como perdido."""
    return {w for w in antes - despues
            if not any(w[:k] in despues and w[k:] in despues for k in range(1, len(w)))}


def _palabras_casete(casete: Path):
    """Palabras de lo que la corrida REAL emitio (con el duplicado): la referencia de 'nada perdido'."""
    evs = [json.loads(l) for l in casete.read_text(encoding="utf-8").splitlines() if l.strip()]
    return _palabras([e["payload"] for e in evs if e.get("dir") == "emit" and e.get("kind") == "text"])


def _resumen(nombre, casete, frase):
    textos, w = reinyectar(casete)
    lineas = [f"== {nombre}: {casete.name}"]
    for m in textos:
        lineas.append(f"seq={m['seq']} {m['audio_start']}-{m['audio_end']} {m['text']!r}")
    lineas.append(f"textos emitidos={len(textos)} unicos(compacto)={len({_compacto(m['text']) for m in textos})}")
    lineas.append(f"apariciones de {frase!r}={_ocurrencias(textos, frase)}")
    antes = _palabras_casete(casete)
    lineas.append(f"palabras distintas: corrida real={len(antes)} reinyeccion={len(_palabras(textos))} "
                  f"perdidas={sorted(_perdidas(antes, _palabras(textos)))} "
                  f"(tokens fusionados partidos: {sorted((antes - _palabras(textos)) - _perdidas(antes, _palabras(textos)))})")
    lineas.append(f"descartados_vieja={w.res.descartados_vieja} dedup_descartados={w.res.dedup_descartados} "
                  f"dedup_recortados={w.res.dedup_recortados}")
    return lineas, textos


# textos legitimos esperados (vistos en el casete ANTES del arreglo): cada uno tiene que seguir saliendo
LEGITIMOS_EN = ["was the function. Now, you may say", "abstraction did not come about until the",
                "All it requires two or three more",
                "instructions and those were in the days", "microcomputers as well"]
LEGITIMOS_ES = ["Con un diagramita lo podríamos ver", "bajo alguna de las definiciones no sea",
                "podemos ir porque si empezó ayer, posiblemente",
                "su alega, sí, pero podría sereste Brownfield", "uno vaya a producción mucho más rápido."]


def test_en_rotacion_atasco_sin_duplicado_y_sin_perder_texto():
    textos, w = reinyectar(EN)
    assert _ocurrencias(textos, "astonishinglycontroversial") == 1
    for frase in LEGITIMOS_EN:
        assert _ocurrencias(textos, frase) == 1, frase
    assert [m["seq"] for m in textos] == list(range(1, len(textos) + 1))
    assert _perdidas(_palabras_casete(EN), _palabras(textos)) == set()   # ninguna palabra legitima perdida


def test_es_rotacion_atasco_sin_duplicado_y_sin_perder_texto():
    textos, w = reinyectar(ES)
    assert _ocurrencias(textos, "sereste Brownfield") == 1
    assert _ocurrencias(textos, "decirdecir uno que empezó ayer") == 1
    for frase in LEGITIMOS_ES:
        assert _ocurrencias(textos, frase) == 1, frase
    assert _perdidas(_palabras_casete(ES), _palabras(textos)) == set()


def test_fuera_de_costura_una_frase_repetida_legitima_se_emite():
    """Sin rotacion no se compara contenido: una frase dicha dos veces sale dos veces."""
    bus = _Bus()
    w = SessionWorker("vf", "en", None, None, emisor=bus, reloj=lambda: 0.0, log=lambda s: None,
                      rotulo="test-costura-casete")
    w.con = c = _Conexion(1, None, 0.0)
    frases = ["thank you very much everyone", "and now the next topic", "thank you very much everyone"]
    for i, f in enumerate(frases):
        w._emitir_asignaciones([Asignacion(0.0, f, 3.0 * i, 3.0 * i + 2.5, 0.0, [i])], c)
    assert [m["text"] for m in bus.msgs if m["type"] == "text"] == frases


def test_en_costura_texto_nuevo_distinto_se_emite_y_contenido_se_descarta():
    bus = _Bus()
    w = SessionWorker("vf", "en", None, None, emisor=bus, reloj=lambda: 0.0, log=lambda s: None,
                      rotulo="test-costura-casete")
    vieja, nueva = _Conexion(1, None, 0.0), _Conexion(2, None, 9.0)
    w.con = nueva
    vieja.corte_nueva, vieja.limite_audio_end = 9.0, 9.45
    w.res.rotaciones.append({"corte_s": 9.0, "pos_s": 21.2})
    w._emitir_asignaciones([Asignacion(0.0, "the 1940s 1950s and even then it was", 9.0, 12.0, 0.0, [4])], nueva)
    # de la nueva, texto distinto dentro de la costura: sale
    w._emitir_asignaciones([Asignacion(0.0, "considered to be an astonishing idea", 11.6, 14.0, 0.0, [5])], nueva)
    # de la vieja, contenido ya emitido: no sale
    w._emitir_asignaciones([Asignacion(0.0, "1950s, and even then", 7.0, 12.0, 0.0, [3, 4])], vieja)
    # de la vieja, empieza en/despues del corte: no sale
    w._emitir_asignaciones([Asignacion(0.0, "something else entirely", 9.0, 9.4, 0.0, [4])], vieja)
    textos = [m["text"] for m in bus.msgs if m["type"] == "text"]
    assert textos == ["the 1940s 1950s and even then it was", "considered to be an astonishing idea"]
    assert w.res.descartados_vieja == 1 and w.res.dedup_descartados == 1


if __name__ == "__main__":
    salida = []
    for nombre, casete, frase in (("EN", EN, "astonishinglycontroversial"), ("ES", ES, "sereste Brownfield")):
        lineas, _ = _resumen(nombre, casete, frase)
        salida += lineas + [""]
    print("\n".join(salida))
    sys.exit(0)
