"""ops/salas.py -- supervisor de N salas (R21/C3, ver README "Cómo escalar a más sesiones").

Lee un JSON con la lista de salas (ver ops/salas.ejemplo.json) y lanza un `python -m worker.run`
por sala contra un mismo --hub, con:

- ARRANQUE ESCALONADO: cada sala arranca --escalon-s segundos después de la anterior (default 20 s,
  ver worker/README.md "Escalonar el arranque de las salas 20-30 s").
- Reparto de keys por sala tal como venga en el JSON (campo "key": NOMBRE de la variable de entorno,
  se pasa tal cual a `worker.run --key`).
- REINICIO AUTOMÁTICO con backoff si un worker muere solo (no si lo mata el supervisor al cerrar):
  1, 2, 4, 8... s (tope --backoff-max-s), hasta --reintentos por sala (default 5). Se cuenta por
  sala, no global.
- Logs por sala en --logs-dir/<id>.log (stdout+stderr del proceso).
- Parada limpia: Ctrl+C o SIGTERM manda la señal a TODOS los hijos (CTRL_BREAK_EVENT en Windows,
  SIGTERM en POSIX) y espera a que terminen antes de salir.
- --dry-run: imprime los comandos que lanzaría, uno por línea, y sale sin ejecutar nada.
- --transporte casete:<archivo.jsonl> (u otro valor aceptado por worker.run): se agrega TAL CUAL a
  TODAS las salas, para probar el supervisor sin gastar cuota de Gemini (skill cuota-gemini).

Uso:
    python -m ops.salas ops/salas.ejemplo.json --hub ws://127.0.0.1:8192/ingest \
        --transporte casete:fixtures/casetes/evidencia-25-09/video-vivo-en-050903.jsonl \
        --escalon-s 2 --logs-dir reportes/ops-salas/logs

Validación de ids: cada "id" tiene que matchear ^[a-z0-9][a-z0-9_-]{0,63}$ (mismo patrón que
contracts/esquema.json#session_id); un id inválido corta ANTES de lanzar nada (exit 2).

Sin dependencias nuevas: sólo librería estándar (argparse, json, subprocess, signal, threading, time).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TIPOS_FUENTE = ("archivo", "url", "mic")


class SalasError(Exception):
    pass


@dataclass
class Sala:
    id: str
    titulo: str
    lang: str
    fuente: dict
    key: str | None = None
    vocab: str | None = None
    extra: list[str] = field(default_factory=list)  # argumentos crudos adicionales, opcional


def cargar_salas(ruta: str) -> list[Sala]:
    with open(ruta, "r", encoding="utf-8") as f:
        datos = json.load(f)
    if not isinstance(datos, list) or not datos:
        raise SalasError(f"{ruta}: se esperaba una lista de salas no vacía")
    salas: list[Sala] = []
    ids_vistos: set[str] = set()
    for i, d in enumerate(datos):
        if not isinstance(d, dict):
            raise SalasError(f"sala #{i}: no es un objeto")
        sid = d.get("id")
        if not isinstance(sid, str) or not ID_RE.match(sid):
            raise SalasError(
                f"sala #{i}: id inválido {sid!r} (tiene que matchear ^[a-z0-9][a-z0-9_-]{{0,63}}$)"
            )
        if sid in ids_vistos:
            raise SalasError(f"id duplicado: {sid!r}")
        ids_vistos.add(sid)
        lang = d.get("lang")
        if lang not in ("en", "es"):
            raise SalasError(f"sala {sid!r}: lang inválido {lang!r} (tiene que ser 'en' o 'es')")
        fuente = d.get("fuente")
        if not isinstance(fuente, dict) or fuente.get("tipo") not in TIPOS_FUENTE:
            raise SalasError(
                f"sala {sid!r}: fuente inválida {fuente!r} (tipo tiene que ser {TIPOS_FUENTE})"
            )
        if not fuente.get("valor"):
            raise SalasError(f"sala {sid!r}: fuente.valor vacío")
        titulo = d.get("titulo") or sid
        salas.append(
            Sala(
                id=sid,
                titulo=titulo,
                lang=lang,
                fuente=fuente,
                key=d.get("key") or None,
                vocab=d.get("vocab") or None,
            )
        )
    return salas


def comando_worker(
    sala: Sala, *, hub: str, transporte: str | None, python: str, traducir_a: str | None = None,
    duracion_s: float | None = None,
) -> list[str]:
    cmd = [python, "-m", "worker.run", "--sesion", sala.id, "--lang", sala.lang,
           "--titulo", sala.titulo, "--hub", hub]
    tipo = sala.fuente["tipo"]
    valor = sala.fuente["valor"]
    if tipo == "archivo":
        cmd += ["--archivo", valor]
    elif tipo == "url":
        cmd += ["--fuente", "url", "--url", valor]
    elif tipo == "mic":
        cmd += ["--fuente", "mic", "--dispositivo", valor]
    if sala.key:
        cmd += ["--key", sala.key]
    if sala.vocab:
        cmd += ["--vocab", sala.vocab]
    if transporte:
        cmd += ["--transporte", transporte]
    if traducir_a:
        cmd += ["--traducir-a", traducir_a]
    if duracion_s is not None:
        cmd += ["--duracion", str(duracion_s)]
    return cmd


def enviar_parada(proc: subprocess.Popen) -> None:
    """Señal de parada limpia: CTRL_BREAK_EVENT en Windows (mismo grupo, ver CREATE_NEW_PROCESS_GROUP
    al lanzar), SIGTERM en POSIX. Reutilizado por Supervisor.parar y por ops/control.py."""
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception:
        pass


def detener_proceso(proc: subprocess.Popen, timeout_s: float = 10.0) -> int:
    """Pide parada limpia y espera hasta timeout_s; si no cerró, mata. Devuelve el returncode."""
    enviar_parada(proc)
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


class Supervisor:
    """Lanza y vigila un `worker.run` por sala; reinicia con backoff si muere solo."""

    def __init__(
        self,
        salas: list[Sala],
        *,
        hub: str,
        transporte: str | None,
        escalon_s: float,
        reintentos: int,
        backoff_inicial_s: float,
        backoff_max_s: float,
        logs_dir: Path,
        python: str,
        traducir_a: str | None = None,
    ) -> None:
        self.salas = salas
        self.hub = hub
        self.transporte = transporte
        self.escalon_s = escalon_s
        self.reintentos = reintentos
        self.backoff_inicial_s = backoff_inicial_s
        self.backoff_max_s = backoff_max_s
        self.logs_dir = logs_dir
        self.python = python
        self.traducir_a = traducir_a
        self.procesos: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._parando = False
        self._hilos: list[threading.Thread] = []

    def _log_path(self, sala_id: str) -> Path:
        return self.logs_dir / f"{sala_id}.log"

    def _lanzar_uno(self, sala: Sala) -> None:
        intento = 0
        backoff = self.backoff_inicial_s
        while not self._parando:
            intento += 1
            cmd = comando_worker(sala, hub=self.hub, transporte=self.transporte, python=self.python,
                                  traducir_a=self.traducir_a)
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_path(sala.id)
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n--- intento {intento} {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                log.write(f"$ {' '.join(cmd)}\n")
                log.flush()
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                proc = subprocess.Popen(
                    cmd, cwd=str(RAIZ), stdout=log, stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            with self._lock:
                if self._parando:
                    self._matar(proc)
                    return
                self.procesos[sala.id] = proc
            print(f"[salas] {sala.id}: PID {proc.pid} (intento {intento}/{self.reintentos}) -> {log_path}")
            codigo = proc.wait()
            with self._lock:
                self.procesos.pop(sala.id, None)
            if self._parando:
                return
            if codigo == 0:
                print(f"[salas] {sala.id}: terminó solo con exit 0 (no se reinicia)")
                return
            print(f"[salas] {sala.id}: murió con exit {codigo}")
            if intento >= self.reintentos:
                print(f"[salas] {sala.id}: {self.reintentos} reintentos agotados, no se relanza más")
                return
            print(f"[salas] {sala.id}: reintentando en {backoff:.1f}s")
            if self._espera_interrumpible(backoff):
                return
            backoff = min(backoff * 2, self.backoff_max_s)

    def _espera_interrumpible(self, segundos: float) -> bool:
        """True si hubo que parar durante la espera."""
        paso = 0.2
        transcurrido = 0.0
        while transcurrido < segundos:
            if self._parando:
                return True
            time.sleep(min(paso, segundos - transcurrido))
            transcurrido += paso
        return self._parando

    def arrancar(self) -> None:
        for i, sala in enumerate(self.salas):
            if i > 0 and self.escalon_s > 0:
                if self._espera_interrumpible(self.escalon_s):
                    break
            if self._parando:
                break
            h = threading.Thread(target=self._lanzar_uno, args=(sala,), daemon=True)
            h.start()
            self._hilos.append(h)

    def _matar(self, proc: subprocess.Popen) -> None:
        enviar_parada(proc)

    def parar(self) -> None:
        self._parando = True
        with self._lock:
            vivos = list(self.procesos.values())
        for proc in vivos:
            self._matar(proc)
        for proc in vivos:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for h in self._hilos:
            h.join(timeout=1)

    def esperar(self) -> None:
        try:
            while not self._parando:
                time.sleep(0.3)
                with self._lock:
                    if not self.procesos and all(not h.is_alive() for h in self._hilos):
                        break
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Supervisor de N salas: un worker.run por sala, arranque escalonado, "
                    "reinicio con backoff, parada limpia (Ctrl+C/SIGTERM)."
    )
    ap.add_argument("salas_json", help="ruta al JSON con la lista de salas (ver ops/salas.ejemplo.json)")
    ap.add_argument("--hub", required=True, help="ws://host:puerto/ingest, se pasa a cada worker.run")
    ap.add_argument("--escalon-s", type=float, default=20.0, help="segundos entre el arranque de una sala y la siguiente (default 20)")
    ap.add_argument("--reintentos", type=int, default=5, help="máximo de reintentos por sala si muere sola (default 5)")
    ap.add_argument("--backoff-inicial-s", type=float, default=1.0, help="espera antes del primer reintento (default 1)")
    ap.add_argument("--backoff-max-s", type=float, default=30.0, help="tope del backoff exponencial (default 30)")
    ap.add_argument("--logs-dir", default=None, help="carpeta de logs por sala (default $TEMP/ops-salas-logs)")
    ap.add_argument("--transporte", default=None, help="se agrega TAL CUAL a --transporte de CADA worker.run (p.ej. casete:archivo.jsonl), para probar sin API")
    ap.add_argument("--traducir-a", default=None, help="se agrega TAL CUAL a --traducir-a de CADA worker.run (p.ej. 'none' para no llamar al modelo de texto en una prueba sin API)")
    ap.add_argument("--python", default=sys.executable, help="intérprete a usar para 'python -m worker.run' (default: el mismo de este proceso)")
    ap.add_argument("--dry-run", action="store_true", help="imprime los comandos, uno por línea, y sale sin ejecutar nada")
    args = ap.parse_args(argv)

    try:
        salas = cargar_salas(args.salas_json)
    except (SalasError, OSError, json.JSONDecodeError) as e:
        print(f"[salas] ERROR: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        for sala in salas:
            cmd = comando_worker(sala, hub=args.hub, transporte=args.transporte, python=args.python,
                                  traducir_a=args.traducir_a)
            print(" ".join(cmd))
        return 0

    logs_dir = Path(args.logs_dir) if args.logs_dir else Path(os.environ.get("TEMP", "/tmp")) / "ops-salas-logs"

    sup = Supervisor(
        salas,
        hub=args.hub,
        transporte=args.transporte,
        escalon_s=args.escalon_s,
        reintentos=args.reintentos,
        backoff_inicial_s=args.backoff_inicial_s,
        backoff_max_s=args.backoff_max_s,
        logs_dir=logs_dir,
        python=args.python,
        traducir_a=args.traducir_a,
    )

    def _manejar_senal(signum, frame):
        print(f"\n[salas] señal {signum}: parando {len(sup.salas)} salas...")
        sup.parar()

    signal.signal(signal.SIGINT, _manejar_senal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _manejar_senal)
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break
        signal.signal(signal.SIGBREAK, _manejar_senal)

    print(f"[salas] {len(salas)} salas, escalón {args.escalon_s}s, logs en {logs_dir}")
    sup.arrancar()
    sup.esperar()
    sup.parar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
