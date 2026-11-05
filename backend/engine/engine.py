"""Backward-compatible lifecycle imports.

New code should import :class:`MarketEngine` from ``engine.market_engine``.
"""

from .market_engine import MarketEngine

# The repository previously exposed an empty OracleEngine placeholder. Retain
# the name as an alias without maintaining a second lifecycle implementation.
OracleEngine = MarketEngine

__all__ = ["MarketEngine", "OracleEngine"]
