"""Shared marketplace contract and explicit instance registry."""

from .base import Marketplace
from .registry import MarketplaceRegistry

__all__ = ["Marketplace", "MarketplaceRegistry"]
