"""Compatibility ASGI entry point for the engine-backed HTTP API."""

from engine.api import create_app

app = create_app()
