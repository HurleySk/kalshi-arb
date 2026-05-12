import asyncio
from unittest.mock import AsyncMock
from src.api import KalshiAPI


def test_get_market_trades_returns_trades():
    api = KalshiAPI.__new__(KalshiAPI)
    api._get = AsyncMock(return_value={
        "trades": [
            {"ticker": "M1", "count": 5, "yes_price": 40, "created_time": "2026-05-12T17:00:00Z"},
        ],
        "cursor": "",
    })
    result = asyncio.get_event_loop().run_until_complete(api.get_market_trades("M1"))
    assert len(result.get("trades", [])) == 1
    api._get.assert_called_once_with("/markets/trades", params={"ticker": "M1", "limit": "10"})
