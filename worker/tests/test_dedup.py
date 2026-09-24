"""B4: dedup de costuras A NIVEL DE CARACTERES (worker/dedup.py) contra costuras REALES.

No hay transcripciones escritas en este archivo: los textos salen de fixtures/casetes/ (lineas emit
de corridas reales). Una costura = sufijo del texto anterior repetido como prefijo del siguiente
(solape de 400 ms entre ventanas, o reenvio al reabrir).
"""
import json
from pathlib import Path

from worker.dedup import _norm_con_indices, dedup_costura, solape
from worker.tests.test_reabrir import ES, correr

CASETES = Path(__file__).resolve().parents[2] / "fixtures" / "casetes"


def _textos(nombre: str) -> list[str]:
    L = [json.loads(x) for x in (CASETES / nombre).read_text(encoding="utf-8").splitlines() if x.strip()]
    return [e["payload"]["text"] for e in L[1:] if e.get("dir") == "emit" and e.get("kind") == "text"]


def _norm(s: str) -> str:
    return _norm_con_indices(s)[0].strip()


def test_costuras_reales_b1_en_60s_rederivado_entrada_duplicada_salida_sin_duplicado():
    tx = _textos("b1-en-60s-rederivado.jsonl")
    costuras = 0
    for prev, nuevo in zip(tx, tx[1:]):
        out, k = dedup_costura(prev, nuevo)
        if not k:
            assert out == nuevo                       # sin costura no se toca nada
            continue
        costuras += 1
        assert solape(prev, nuevo) > 0                # entrada: duplicada en la costura
        assert solape(prev, out) == 0                 # salida: sin duplicado
        assert nuevo.endswith(out) and 0 < len(out) < len(nuevo)   # solo se quita el principio
        quitado = _norm(nuevo[: len(nuevo) - len(out)])
        assert quitado and _norm(prev).endswith(quitado)          # lo quitado es la cola del previo
    assert costuras == 4                              # medido en el casete: 4 costuras de 10 pares


def test_costuras_reales_largo_es_y_sin_falsos_en_pares_sin_costura():
    tx = _textos("b1-nerdearla-es-intento2-cancelled.jsonl")
    con = [(p, n) for p, n in zip(tx, tx[1:]) if dedup_costura(p, n)[1]]
    assert len(con) == 12                             # medido: 12 costuras en 68 pares
    for p, n in con:
        out, _ = dedup_costura(p, n)
        assert solape(p, out) == 0 and n.endswith(out)


def test_duplicado_entero_real_no_queda_nada():
    # reenvio al reabrir: la conexion nueva repite ENTERO el ultimo texto de la vieja
    for t in _textos("b1-nerdearla-es-intento2-cancelled.jsonl")[:10]:
        out, k = dedup_costura(t, t)
        assert out == "" and k == len(_norm(t))


def test_costura_de_reapertura_en_el_worker_casete_real():
    """SessionWorker + transporte de casete (ES real, sesion muda desde 60 s => reapertura con
    reenvio): sin dedup quedan costuras duplicadas en la salida; con dedup, ninguna, y no se pierde
    ningun texto (solo se recortan prefijos)."""
    res0, msgs0, _ = correr(ES, 110.0, mudo=60.0, dedup=False)
    res1, msgs1, _ = correr(ES, 110.0, mudo=60.0, dedup=True)

    def costuras(msgs):
        tx = [m["text"] for m in msgs if m["type"] == "text"]
        return sum(1 for a, b in zip(tx, tx[1:]) if solape(a, b))

    assert any(r["reason"] == "atasco" for r in res1.rotaciones)
    assert costuras(msgs0) >= 3
    assert costuras(msgs1) == 0 and res1.dedup_recortados >= 3
    assert res1.textos + res1.dedup_descartados == res0.textos
