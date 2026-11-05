from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


def _validate_market_hash_name(value: str) -> str:
    if not value.strip():
        raise ValueError("market_hash_name must not be empty")
    if value != value.strip():
        raise ValueError("market_hash_name must not have leading or trailing whitespace")
    return value


MarketHashName = Annotated[str, AfterValidator(_validate_market_hash_name)]


class Item(BaseModel):
    """A market-tradable CS2 item identified by its Steam market hash name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    market_hash_name: MarketHashName
