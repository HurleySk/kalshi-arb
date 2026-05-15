import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from src.models import Orderbook, TradeSignal


def _make_bot():
    """Create an ArbBot with all dependencies mocked."""
    from src.main import ArbBot
    with patch("src.main.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.api_key_id = "fake"
        cfg.private_key_path = "fake.pem"
        cfg.rest_base_url = "https://fake"
        cfg.ws_url = "wss://fake"
        cfg.risk_mode = "aggressive"
        cfg.strategy_overrides = {}
        cfg.fill_timeout_secs = 30
        cfg.event_poll_interval_secs = 60
        cfg.max_session_loss = 1.0
        cfg.circuit_breaker_on_any_loss = True
        cfg.maker_enabled = False
        cfg.maker_fill_mode = "cancel_and_take"
        cfg.max_maker_events = 3
        cfg.maker_max_horizon_hours = 2.0
        cfg.max_contracts_per_arb = 1
        cfg.log_level = "INFO"
        cfg.log_file = "/dev/null"
        cfg.recording_enabled = False
        cfg.recording_db_path = None
        cfg.recording_snapshot_interval_secs = 5
        cfg.recording_balance_poll_interval_secs = 300
        mock_cfg.return_value = cfg

        with patch("src.main.KalshiAuth"):
            with patch("src.main.KalshiAPI"):
                with patch("src.main.MarketScanner"):
                    return ArbBot("fake.yaml")


def _setup_boot_reconcile(bot, *, open_orders=None, positions=None):
    """Wire up API mocks for _boot_reconcile tests."""
    bot.api.get_open_orders = AsyncMock(return_value={"orders": open_orders or []})
    bot.api.get_positions = AsyncMock(return_value={"market_positions": positions or []})
    bot.api.batch_cancel_orders = AsyncMock(return_value={})
    bot.api.batch_create_orders = AsyncMock(return_value={"orders": [
        {"order": {"order_id": "x", "status": "executed", "ticker": "T", "yes_price_dollars": "0.99"}}
    ]})
    bot.api.build_close_order = MagicMock(return_value={"ticker": "T", "action": "buy"})
    bot.api.unwrap_order = MagicMock(return_value={"status": "executed"})
    bot.positions.load_position = MagicMock()


def test_boot_reconcile_clean_slate():
    bot = _make_bot()
    _setup_boot_reconcile(bot)
    asyncio.run(bot._boot_reconcile())
    bot.api.batch_cancel_orders.assert_not_called()
    bot.positions.load_position.assert_not_called()
    bot.api.batch_create_orders.assert_not_called()


def test_boot_reconcile_cancels_orphaned_orders():
    bot = _make_bot()
    resting = [{"order_id": "AAA", "status": "resting"}, {"order_id": "BBB", "status": "open"}]
    _setup_boot_reconcile(bot, open_orders=resting)
    asyncio.run(bot._boot_reconcile())
    bot.api.batch_cancel_orders.assert_called_once_with(["AAA", "BBB"])


def test_boot_reconcile_loads_longs():
    bot = _make_bot()
    positions = [{"ticker": "M1", "position_fp": "2"}, {"ticker": "M2", "position_fp": "1"}]
    _setup_boot_reconcile(bot, positions=positions)
    asyncio.run(bot._boot_reconcile())
    calls = {c.args[0]: c.args[2] for c in bot.positions.load_position.call_args_list}
    assert calls == {"M1": 2, "M2": 1}
    bot.api.batch_create_orders.assert_not_called()


def test_boot_reconcile_closes_shorts():
    bot = _make_bot()
    positions = [{"ticker": "M_SHORT", "position_fp": "-1"}]
    _setup_boot_reconcile(bot, positions=positions)
    asyncio.run(bot._boot_reconcile())
    bot.positions.load_position.assert_not_called()
    bot.api.build_close_order.assert_called_once_with("M_SHORT", -1)
    bot.api.batch_create_orders.assert_called_once()


def test_boot_reconcile_handles_mixed_state():
    bot = _make_bot()
    orders = [{"order_id": "ORD1", "status": "resting"}]
    positions = [
        {"ticker": "LONG1", "position_fp": "3"},
        {"ticker": "SHORT1", "position_fp": "-2"},
    ]
    _setup_boot_reconcile(bot, open_orders=orders, positions=positions)
    asyncio.run(bot._boot_reconcile())
    bot.api.batch_cancel_orders.assert_called_once_with(["ORD1"])
    bot.positions.load_position.assert_called_once_with("LONG1", "yes", 3)
    bot.api.build_close_order.assert_called_once_with("SHORT1", -1)


def test_pending_execution_prevents_duplicate():
    """If an event is in _pending_execution, _on_orderbook_update should not fire another execution."""
    bot = _make_bot()

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
    )
    bot.engine.evaluate = MagicMock(return_value=signal)
    bot.executor.is_executing = MagicMock(return_value=False)
    bot.executor.is_event_blacklisted = MagicMock(return_value=False)
    bot.executor.is_circuit_breaker_tripped = MagicMock(return_value=False)

    bot.orderbook_mgr.register_event("E1", ["M1", "M2", "M3"])
    bot.orderbook_mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.4000", "100"]], "no_dollars_fp": []})
    bot.orderbook_mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})
    bot.orderbook_mgr.apply_snapshot("M3", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})

    # Simulate: event already pending execution
    bot.dispatcher._pending_execution.add("E1")

    bot._on_orderbook_update("M1")

    # evaluate should not even be called because event is pending
    bot.engine.evaluate.assert_not_called()


def _fast_shutdown_bot(bot):
    """Set small timeouts so shutdown tests don't wait real seconds."""
    bot._shutdown_timeout = 0.5
    bot._shutdown_api_timeout = 0.05
    bot._shutdown_retry_delay = 0.01
    bot._shutdown_retry_backoff = 1.01


def test_emergency_shutdown_retries_on_rate_limit():
    """Emergency shutdown should retry the full sequence on 429."""
    import aiohttp
    bot = _make_bot()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None
    _fast_shutdown_bot(bot)

    call_count = {"n": 0}
    async def mock_batch_create(orders):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(),
                status=429, message="Too Many Requests",
            )
        return {"orders": []}

    bot.api.get_open_orders = AsyncMock(return_value={"orders": []})
    bot.api.get_positions = AsyncMock(return_value={"market_positions": [
        {"ticker": "T1", "position_fp": "1.00"},
    ]})
    bot.api.batch_create_orders = AsyncMock(side_effect=mock_batch_create)
    bot.api.batch_cancel_orders = AsyncMock(return_value={})
    bot.api.build_close_order = MagicMock(return_value={"ticker": "T1", "action": "buy"})

    asyncio.run(bot._emergency_shutdown())

    assert call_count["n"] == 2


def test_emergency_shutdown_cancel_failure_doesnt_block_close():
    """If cancelling orders fails, should still attempt to close positions."""
    bot = _make_bot()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None
    _fast_shutdown_bot(bot)

    bot.api.get_open_orders = AsyncMock(return_value={"orders": [
        {"order_id": "r1", "status": "resting"},
    ]})
    bot.api.batch_cancel_orders = AsyncMock(side_effect=Exception("cancel failed"))
    bot.api.get_positions = AsyncMock(return_value={"market_positions": [
        {"ticker": "T1", "position_fp": "1.00"},
    ]})
    bot.api.batch_create_orders = AsyncMock(return_value={"orders": []})
    bot.api.build_close_order = MagicMock(return_value={"ticker": "T1", "action": "buy"})

    asyncio.run(bot._emergency_shutdown())

    bot.api.batch_create_orders.assert_called_once()


def test_emergency_shutdown_does_not_hang():
    """Emergency shutdown must complete even if API calls hang."""
    bot = _make_bot()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None
    _fast_shutdown_bot(bot)

    async def _hang(*args, **kwargs):
        await asyncio.Event().wait()

    bot.api.get_open_orders = AsyncMock(side_effect=_hang)
    bot.api.get_positions = AsyncMock(side_effect=_hang)
    bot.api.batch_cancel_orders = AsyncMock(side_effect=_hang)
    bot.api.batch_create_orders = AsyncMock(side_effect=_hang)

    async def _run():
        try:
            await asyncio.wait_for(bot._emergency_shutdown(), timeout=5)
        except asyncio.TimeoutError:
            raise AssertionError("_emergency_shutdown hung — no overall timeout")

    asyncio.run(_run())


def test_emergency_shutdown_idempotent():
    """Second call to _emergency_shutdown should be a no-op."""
    bot = _make_bot()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None
    _fast_shutdown_bot(bot)
    bot.api.get_open_orders = AsyncMock(return_value={"orders": []})
    bot.api.get_positions = AsyncMock(return_value={"market_positions": []})

    async def _run():
        await bot._emergency_shutdown()
        await bot._emergency_shutdown()

    asyncio.run(_run())
    assert bot.api.get_open_orders.call_count == 1


def test_cleanup_expired_events():
    """Expired events should be removed from orderbook manager and metadata."""
    bot = _make_bot()

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    bot.discovery.event_tickers = {"E_EXPIRED", "E_ACTIVE"}
    bot.orderbook_mgr.register_event("E_EXPIRED", ["M_EXP1", "M_EXP2"])
    bot.orderbook_mgr.register_event("E_ACTIVE", ["M_ACT1", "M_ACT2"])
    bot.discovery.market_metadata.update({
        "M_EXP1": {"close_time": past},
        "M_EXP2": {"close_time": past},
        "M_ACT1": {"close_time": future},
        "M_ACT2": {"close_time": future},
    })

    bot.discovery.cleanup_expired()

    assert "E_EXPIRED" not in bot.discovery.event_tickers
    assert "E_ACTIVE" in bot.discovery.event_tickers
    assert "M_EXP1" not in bot.discovery.market_metadata
    assert "M_EXP2" not in bot.discovery.market_metadata
    assert "M_ACT1" in bot.discovery.market_metadata
    assert "M_ACT2" in bot.discovery.market_metadata
    assert bot.orderbook_mgr.get_event_for_market("M_EXP1") is None
    assert bot.orderbook_mgr.get_event_for_market("M_EXP2") is None
    assert bot.orderbook_mgr.get_event_for_market("M_ACT1") == "E_ACTIVE"
