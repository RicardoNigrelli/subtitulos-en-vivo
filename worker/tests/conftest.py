"""Los tests previos al 25/09 (latencia) fijan la configuracion con la que se escribieron: lotes 2/4 s y
rotacion 3.5 <-> 3.1. Los defaults nuevos (inmediato, preferencia) se prueban EXPLICITOS en
test_inmediato.py (modo="inmediato", rotar=False)."""
import os

os.environ.setdefault("TRADUCTOR_MODO", "lote")
os.environ.setdefault("TRADUCTOR_ROTAR", "1")
