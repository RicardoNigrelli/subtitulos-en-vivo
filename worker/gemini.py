"""Cliente de Gemini para el worker (unico modulo que toca la API, skill cuota-gemini).

Uso:
    python -m worker.gemini --listar            # modelos live/transcribe (sin gastar audio)

La key sale de `.env` (python-dotenv). NUNCA se imprime ni se escribe en archivos.
"""
from __future__ import annotations

import argparse
import os
import ssl
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Modelo de transcripcion live. Se confirma contra models.list() (reportes/audio-pipeline-b1-modelos.txt).
MODELO_LIVE_DEFAULT = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.5-transcribe-live")


def _cargar_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(RAIZ / ".env", override=False)


def api_key(nombre: str = "GEMINI_API_KEY") -> str:
    _cargar_env()
    k = os.environ.get(nombre, "").strip()
    if not k:
        raise RuntimeError(f"{nombre} no esta definida en .env ni en el entorno")
    return k


def preparar_ca() -> str | None:
    """Avast intercepta HTTPS con su propia raiz (skill ingesta). Esa raiz esta en el almacen de
    Windows pero NO en certifi ni en el SSL_CERT_FILE del usuario (medido 24/09 13:08: verify falla
    con cafile=SSL_CERT_FILE y pasa con el almacen de Windows). El SDK arma su contexto con
    cafile=SSL_CERT_FILE, asi que se genera EN $TEMP un bundle certifi + almacen de Windows y se
    apunta SSL_CERT_FILE a ese archivo SOLO dentro de este proceso. La verificacion NO se desactiva."""
    if sys.platform != "win32" or os.environ.get("VIBEATHON_CA_LISTO") == "1":
        return os.environ.get("SSL_CERT_FILE")
    import tempfile
    try:
        import certifi
        partes = [Path(certifi.where()).read_text(encoding="ascii", errors="ignore")]
    except Exception:
        partes = []
    for store in ("ROOT", "CA"):
        try:
            for der, enc, _trust in ssl.enum_certificates(store):
                if enc == "x509_asn":
                    partes.append(ssl.DER_cert_to_PEM_cert(der))
        except Exception:
            pass
    destino = Path(tempfile.gettempdir()) / "vibeathon-ca-bundle.pem"
    # B8: dos workers arrancando a la vez escribian el MISMO archivo y uno leia un PEM a medio
    # escribir ("SSLError: [X509] PEM lib", reportes/audio-pipeline-b8-glosario-sin-intento1-*.err).
    # Se escribe a un archivo propio del proceso y se reemplaza atomicamente.
    tmp = destino.with_name(f"vibeathon-ca-bundle.{os.getpid()}.tmp")
    tmp.write_text(chr(10).join(partes), encoding="ascii")
    try:
        os.replace(tmp, destino)
    except OSError:                      # Windows: destino abierto por otro proceso -> usar el propio
        destino = tmp
    os.environ["SSL_CERT_FILE"] = str(destino)
    os.environ["REQUESTS_CA_BUNDLE"] = str(destino)
    os.environ["VIBEATHON_CA_LISTO"] = "1"
    return str(destino)


def _parche_ssl_avast() -> None:
    """Skill ingesta: Avast intercepta HTTPS y Python 3.13 activa VERIFY_X509_STRICT.
    Si GEMINI_SSL_RELAX=1, se relaja SOLO ese flag (la cadena se sigue verificando)."""
    preparar_ca()
    if os.environ.get("GEMINI_SSL_RELAX") != "1":
        return
    orig = ssl.create_default_context

    def ctx(*a, **kw):
        c = orig(*a, **kw)
        c.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return c

    ssl.create_default_context = ctx  # type: ignore[assignment]


def cliente(nombre_key: str = "GEMINI_API_KEY"):
    _parche_ssl_avast()
    from google import genai

    return genai.Client(api_key=api_key(nombre_key))


def listar_modelos(out=sys.stdout) -> int:
    c = cliente()
    todos = list(c.models.list())
    print(f"# models.list(): {len(todos)} modelos en total", file=out)
    print("# filtro: nombre contiene 'live' o 'transcri' o 'native-audio', o soporta bidiGenerateContent", file=out)
    n = 0
    for m in todos:
        nombre = m.name or ""
        acciones = list(getattr(m, "supported_actions", None) or [])
        low = nombre.lower()
        if ("live" in low or "transcri" in low or "native-audio" in low
                or any("bidi" in a.lower() for a in acciones)):
            n += 1
            print(f"{nombre} | display={m.display_name!r} | actions={acciones} | "
                  f"in_tokens={getattr(m, 'input_token_limit', None)}", file=out)
    print(f"# coincidencias: {n}", file=out)
    print("# modelos de texto candidatos (flash-lite, para B2):", file=out)
    for m in todos:
        if "flash-lite" in (m.name or ""):
            print(f"{m.name} | actions={list(getattr(m, 'supported_actions', None) or [])}", file=out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.gemini")
    ap.add_argument("--listar", action="store_true", help="listar modelos live/transcribe")
    a = ap.parse_args(argv)
    if a.listar:
        return listar_modelos()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
