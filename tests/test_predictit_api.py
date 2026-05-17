import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from src.exchanges.predictit.api import PredictItAPI


def make_api(browser=None):
    if browser is None:
        browser = MagicMock()
        browser.navigate_to_market = AsyncMock()
        browser.page = MagicMock()
        browser._page.query_selector = AsyncMock(return_value=None)
        browser._page.query_selector_all = AsyncMock(return_value=[])
        browser._page.evaluate = AsyncMock(return_value=None)
        browser.close = AsyncMock()
    return PredictItAPI(browser=browser)


def test_api_init():
    api = make_api()
    assert api._browser is not None


def test_batch_create_orders_calls_browser():
    api = make_api()
    orders = [
        {
            "ticker": "PI-100-200",
            "market_id": 100,
            "contract_id": 200,
            "market_url": "https://www.predictit.org/markets/detail/100",
            "action": "buy",
            "outcome": "yes",
            "shares": 5,
            "price": 45,
        }
    ]
    result = asyncio.run(api.batch_create_orders(orders))
    assert "orders" in result
    assert len(result["orders"]) == 1


def test_get_balance_returns_dict():
    api = make_api()
    result = asyncio.run(api.get_balance())
    assert "balance" in result


def test_get_positions_returns_dict():
    api = make_api()
    result = asyncio.run(api.get_positions())
    assert "market_positions" in result


def test_close_delegates_to_browser():
    browser = MagicMock()
    browser.close = AsyncMock()
    api = PredictItAPI(browser=browser)
    asyncio.run(api.close())
    browser.close.assert_awaited_once()
