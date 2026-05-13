import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.main import ArbBot
from src.risk import load_risk_profile


def _make_mock_bot(mode="conservative"):
    bot = ArbBot.__new__(ArbBot)
    bot.api = MagicMock()
    bot.api.get_market_trades = AsyncMock()
    bot.risk_profile = load_risk_profile(mode, {})
    return bot


def test_recent_trades_rejects_stale_market():
    bot = _make_mock_bot()
    bot.api.get_market_trades = AsyncMock(return_value={"trades": [], "cursor": ""})

    result = asyncio.run(
        bot._validate_recent_trades(["M1", "M2"])
    )
    assert result is False


def test_recent_trades_accepts_active_market():
    bot = _make_mock_bot()
    bot.api.get_market_trades = AsyncMock(return_value={
        "trades": [{"ticker": "M1", "count": 5, "created_time": "2026-05-12T17:00:00Z"}],
        "cursor": "",
    })

    result = asyncio.run(
        bot._validate_recent_trades(["M1", "M2"])
    )
    assert result is True


def test_recent_trades_skipped_in_aggressive_mode():
    bot = _make_mock_bot(mode="aggressive")
    bot.api.get_market_trades = AsyncMock(side_effect=AssertionError("should not be called"))

    result = asyncio.run(
        bot._validate_recent_trades(["M1"])
    )
    assert result is True
