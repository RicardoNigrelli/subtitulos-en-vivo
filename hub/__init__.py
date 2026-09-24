"""Hub de subtitulos: recibe mensajes del contrato (contracts/) por /ingest y los reparte por sesion.

    python -m hub                                  # levanta el hub (8100)
    python -m hub.inyectar <archivo.jsonl>         # empuja un JSONL/casete al hub, rotulado replay
    python -m hub.cliente_ws <sesion> --lang es    # mira una sesion desde la terminal
"""
