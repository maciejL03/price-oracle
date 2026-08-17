from app.domain.models import CheaperMarket, PriceComparison, SkinportPrice, SteamPrice


def compare_prices(
    market_hash_name: str,
    steam: SteamPrice,
    skinport: SkinportPrice,
) -> PriceComparison:
    """Compare Steam lowest listing against Skinport minimum listing."""
    steam_price = steam.lowest_price
    skinport_price = skinport.min_price

    difference = abs(steam_price - skinport_price)
    baseline = min(steam_price, skinport_price)
    percentage = (difference / baseline * 100) if baseline > 0 else 0.0

    if steam_price < skinport_price:
        cheaper = CheaperMarket.STEAM
    elif skinport_price < steam_price:
        cheaper = CheaperMarket.SKINPORT
    else:
        cheaper = CheaperMarket.SAME

    return PriceComparison(
        market_hash_name=market_hash_name,
        steam=steam,
        skinport=skinport,
        cheaper=cheaper,
        difference=round(difference, 2),
        percentage=round(percentage, 2),
    )
