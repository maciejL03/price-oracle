"""Executable entry point for ``python -m engine``."""

from __future__ import annotations

import asyncio
import logging

from engine.bootstrap import create_market_engine
from engine.config import EngineSettings
from engine.runtime import run_market_engine

logger = logging.getLogger(__name__)


def main() -> int:
    """Load configuration and own the engine for the lifetime of this process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = EngineSettings()
        engine = create_market_engine(settings)
        asyncio.run(run_market_engine(engine))
    except KeyboardInterrupt:
        logger.info("Price Oracle engine process was interrupted")
        return 130
    except Exception:
        logger.exception("Price Oracle engine process stopped with an error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
