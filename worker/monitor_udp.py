"""Monitor de audio por UDP (--monitor-udp HOST:PUERTO): cada chunk PCM s16le 16 kHz mono de 100 ms
que la sala lee de la fuente y manda a Gemini sale TAMBIEN, tal cual, como un datagrama UDP local.
Una vez por chunk de la fuente y en orden: ni el solape ni el reenvio por rotacion. Socket no
bloqueante; cualquier error se ignora (nunca frena ni rompe el envio a Gemini). Solo loopback."""
from __future__ import annotations

import socket

HOSTS_LOCALES = {"127.0.0.1", "localhost", "::1"}


def parsear(valor: str) -> tuple[str, int]:
    """'HOST:PUERTO' -> (host, puerto). HOST solo 127.0.0.1/localhost/::1 (tambien '[::1]:P').
    ValueError si no cumple."""
    v = (valor or "").strip()
    if v.startswith("["):
        host, _, resto = v[1:].partition("]")
        if not resto.startswith(":"):
            raise ValueError(f"{valor!r}: se espera [::1]:PUERTO")
        puerto_s = resto[1:]
    else:
        host, sep, puerto_s = v.rpartition(":")
        if not sep:
            raise ValueError(f"{valor!r}: se espera HOST:PUERTO")
    host = host.strip().lower()
    if host not in HOSTS_LOCALES:
        raise ValueError(f"host {host!r} no permitido: solo 127.0.0.1, localhost o ::1")
    try:
        puerto = int(puerto_s)
    except ValueError:
        raise ValueError(f"puerto {puerto_s!r} invalido") from None
    if not 1 <= puerto <= 65535:
        raise ValueError(f"puerto {puerto} fuera de rango")
    return host, puerto


class MonitorUDP:
    def __init__(self, host: str, puerto: int):
        if host == "localhost":
            host = "127.0.0.1"
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.destino = (host, puerto)
        self.enviados = 0
        self.errores = 0
        self.sock: socket.socket | None = None
        try:
            self.sock = socket.socket(fam, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
        except OSError:
            self.sock = None

    def __call__(self, data: bytes) -> None:
        if self.sock is None:
            return
        try:
            self.sock.sendto(data, self.destino)
            self.enviados += 1
        except Exception:   # sin receptor (ICMP -> WinError 10054), buffer lleno, etc.: se ignora
            self.errores += 1

    def cerrar(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
