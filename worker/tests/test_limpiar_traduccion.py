r"""B7: entidades HTML y caracteres de control en traducciones (adversario B5, reportes/adversario-b5.md 1.5).

Los casos son las lineas `translation` REALES de la corrida larga de B5, copiadas sin editar con:
  grep '"kind": "translation"' qa/out/smoke-b5r-qa-b5r-en.casete.jsonl | grep -E '&[a-z]+;|\\u00[01][0-9a-f]'
    > fixtures/casetes/b5r-en-traducciones-defectuosas.jsonl
Se re-arma la respuesta del modelo (JSON array, como la devuelve generar) y se pasa por parsear.
"""
import json
import re
from pathlib import Path

from worker.traductor import limpiar, parsear

CASOS = Path(__file__).resolve().parents[2] / "fixtures" / "casetes" / "b5r-en-traducciones-defectuosas.jsonl"
ENTIDAD = re.compile(r"&[a-zA-Z]+;|&#\d+;")
CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def _textos_reales():
    out = []
    for linea in CASOS.read_text(encoding="utf-8").splitlines():
        d = json.loads(linea)
        for it in d["payload"]["items"]:
            if it.get("ok") and it.get("text") and (ENTIDAD.search(it["text"]) or CONTROL.search(it["text"])):
                out.append(it["text"])
    return out


def test_casos_reales_b5r_salen_sin_entidades_ni_controles():
    crudos = _textos_reales()
    assert len(crudos) >= 4, crudos                   # 4 lineas; 6 textos con defecto
    assert any("\x10" in x for x in crudos) and any("&eacute;" in x for x in crudos)
    limpios = parsear(json.dumps(crudos), len(crudos))
    assert limpios is not None and len(limpios) == len(crudos)
    for x in limpios:
        assert not ENTIDAD.search(x) and not CONTROL.search(x), repr(x)
    assert "a través de estos millones" in limpios
    assert "Qué pasó con la IA?" in limpios


def test_limpiar_conserva_salto_de_linea_y_quita_control_codificado():
    assert limpiar("uno\ndos") == "uno\ndos"
    assert limpiar("a\tb\rc\x7fd") == "abcd"
    assert limpiar("&#16;&#16;Hola &amp; chau") == "Hola & chau"   # desescapa y DESPUES quita controles
    assert limpiar("sin cambios: ¿qué?") == "sin cambios: ¿qué?"
