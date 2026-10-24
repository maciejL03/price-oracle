"""Compatibility ASGI entry point for the engine-backed HTTP API."""

import logging

from engine.api import create_app

engine_logger = logging.getLogger("engine")
engine_logger.setLevel(logging.INFO)
engine_handler = logging.StreamHandler()
engine_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
engine_logger.handlers = [engine_handler]
engine_logger.propagate = False

app = create_app()
