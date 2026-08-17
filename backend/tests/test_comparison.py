from app.domain.comparison import compare_prices
from app.domain.models import CheaperMarket, SkinportPrice, SteamPrice


def test_skinport_cheaper():
    result = compare_prices(
        "AK-47 | Redline (Field-Tested)",
        SteamPrice(lowest_price=30.0, median_price=30.5, volume=82),
        SkinportPrice(
            min_price=22.39,
            median_price=25.0,
            mean_price=26.0,
            max_price=40.0,
            suggested_price=24.0,
            quantity=10,
            item_page=None,
            market_page=None,
        ),
    )

    assert result.cheaper == CheaperMarket.SKINPORT
    assert result.difference == 7.61
    assert result.percentage == round(7.61 / 22.39 * 100, 2)


def test_steam_cheaper():
    result = compare_prices(
        "Test Item",
        SteamPrice(lowest_price=10.0, median_price=None, volume=None),
        SkinportPrice(
            min_price=12.0,
            median_price=None,
            mean_price=None,
            max_price=None,
            suggested_price=None,
            quantity=None,
            item_page=None,
            market_page=None,
        ),
    )

    assert result.cheaper == CheaperMarket.STEAM
    assert result.difference == 2.0
    assert result.percentage == 20.0


def test_same_price():
    result = compare_prices(
        "Test Item",
        SteamPrice(lowest_price=15.0, median_price=None, volume=None),
        SkinportPrice(
            min_price=15.0,
            median_price=None,
            mean_price=None,
            max_price=None,
            suggested_price=None,
            quantity=None,
            item_page=None,
            market_page=None,
        ),
    )

    assert result.cheaper == CheaperMarket.SAME
    assert result.difference == 0.0
    assert result.percentage == 0.0
