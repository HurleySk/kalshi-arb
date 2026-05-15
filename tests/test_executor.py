import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.executor import ExecutionManager, TimeoutConfig
from src.models import TradeSignal
from src.risk import load_risk_profile


_FAST_TIMEOUTS = TimeoutConfig(batch_create=0.1, batch_cancel=0.1, balance=0.1, monitor_poll=0.01)


def _make_executor(fill_timeout=0):
    api = MagicMock()
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "open"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "open"}},
            {"order": {"order_id": "o3", "ticker": "M3", "status": "open"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    order_builder = MagicMock()
    order_builder.unwrap_order = lambda raw: raw.get("order", raw)
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price": round(yes_price * 100),
        "count": quantity,
    })
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(api=api, order_builder=order_builder, positions=positions, fill_timeout_secs=fill_timeout, timeouts=_FAST_TIMEOUTS), api, positions


def test_executor_accepts_risk_profile_directly():
    profile = load_risk_profile("conservative", {})
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    positions = MagicMock()
    executor = ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=30, risk_profile=profile,
    )
    assert executor._unwind_phase1_secs == profile.unwind_phase1_secs
    assert executor._unwind_phase2_secs == profile.unwind_phase2_secs
    assert executor._unwind_price_step_cents == profile.unwind_price_step_cents


def test_build_orders_from_signal():
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    orders = executor.build_orders(signal, quantity=10)
    assert len(orders) == 3
    assert orders[0]["ticker"] == "M1"
    assert orders[0]["yes_price"] == 40
    assert orders[0]["count"] == 10


def test_execute_calls_batch_create():
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=10))
    api.batch_create_orders.assert_called_once()


def test_is_executing_flag():
    executor, _, _ = _make_executor()
    assert not executor.is_executing()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=10))
    # After execution completes, flag should be cleared
    assert not executor.is_executing()


# --- RiskProfile + Unwind tests ---

def _make_executor_with_profile(mode="conservative", fill_timeout=0):
    profile = load_risk_profile(mode, {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
    })
    api = MagicMock()
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.46", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.99", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    order_builder = MagicMock()
    order_builder.unwrap_order = lambda raw: raw.get("order", raw)
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    order_builder.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "buy", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(
        api=api, order_builder=order_builder, positions=positions,
        fill_timeout_secs=fill_timeout, risk_profile=profile,
        timeouts=_FAST_TIMEOUTS,
    ), api, positions


def test_partial_fill_triggers_unwind():
    executor, api, positions = _make_executor_with_profile()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    assert executor.is_event_blacklisted("E1")
    # batch_create_orders: (1) original arb, (2+) unwind phases
    assert api.batch_create_orders.call_count >= 2


def test_immediate_fills_are_tracked():
    executor, api, positions = _make_executor_with_profile()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    positions.record_fill.assert_called()
    call_args = [c.kwargs for c in positions.record_fill.call_args_list]
    tickers_filled = [c.get("ticker", "") for c in call_args]
    assert "M2" in tickers_filled


def test_unwind_places_buy_order():
    executor, api, _ = _make_executor_with_profile()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # Find the unwind call (not the first batch_create_orders call)
    assert api.batch_create_orders.call_count >= 2
    unwind_call = api.batch_create_orders.call_args_list[1]
    unwind_orders = unwind_call[0][0]
    assert unwind_orders[0]["action"] == "buy"
    assert unwind_orders[0]["ticker"] == "M2"


def test_buy_side_resting_leg_immediate_cancel():
    """When a buy-side arb has a resting leg in the batch response,
    cancel immediately and unwind filled legs — don't wait for fill_timeout."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
    })
    api = MagicMock()
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    api.get_balance = AsyncMock(return_value={"balance": 10000})
    order_builder = MagicMock()
    order_builder.unwrap_order = lambda raw: raw.get("order", raw)
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    order_builder.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "buy", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    positions = MagicMock()
    positions.record_fill = MagicMock()

    api.batch_create_orders = AsyncMock(side_effect=[
        # Original batch: KIA resting, SAM filled
        {"orders": [
            {"order": {"order_id": "o1", "ticker": "KIA", "status": "resting",
                       "yes_price_dollars": "0.24", "fill_count_fp": "0.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "SAM", "status": "executed",
                       "yes_price_dollars": "0.66", "fill_count_fp": "1.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
        ]},
        # Unwind phase 1 fills immediately
        {"orders": [{"order": {"order_id": "u1", "ticker": "SAM",
                                "status": "executed", "yes_price_dollars": "0.63",
                                "fill_count_fp": "1.00", "action": "sell", "side": "yes"}}]},
    ])

    executor = ExecutionManager(
        api=api, order_builder=order_builder, positions=positions,
        fill_timeout_secs=60,  # Long timeout — should NOT be reached
        risk_profile=profile,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("KIA", 0.24), ("SAM", 0.66)],
        net_profit=0.07, profit_pct=7.15, exposure_ratio=0.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )

    import time
    start = time.time()
    asyncio.run(executor.execute(signal, quantity=1))
    elapsed = time.time() - start

    # Should complete well under fill_timeout_secs (60s)
    assert elapsed < 5.0, f"Took {elapsed:.1f}s — should have cancelled immediately, not waited for timeout"
    # Resting leg should have been cancelled
    api.batch_cancel_orders.assert_called_once_with(["o1"])
    # Event should be blacklisted
    assert executor.is_event_blacklisted("E1")


def test_sell_side_resting_still_waits_for_fills():
    """Sell-side arbs with resting legs should still use _monitor_fills,
    not the immediate cancel path."""
    executor, api, positions = _make_executor_with_profile()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # Should still go through normal flow (timeout-based cancel)
    assert executor.is_event_blacklisted("E1")


def test_build_orders_defaults_to_sell_when_no_leg_actions():
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
    )
    api = MagicMock()
    order_builder = MagicMock()
    order_builder.build_sell_order.return_value = {"action": "sell"}
    executor = ExecutionManager(api=api, order_builder=order_builder, positions=MagicMock(),
                                fill_timeout_secs=10,
                                risk_profile=load_risk_profile("aggressive", {}))
    orders = executor.build_orders(signal, quantity=1)
    assert order_builder.build_sell_order.call_count == 2
    assert not hasattr(order_builder.build_buy_order, 'called') or order_builder.build_buy_order.call_count == 0


def test_build_orders_buy_when_leg_action_is_buy():
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.35), ("M2", 0.40)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )
    api = MagicMock()
    order_builder = MagicMock()
    order_builder.build_buy_order.return_value = {"action": "buy"}
    order_builder.build_sell_order.return_value = {"action": "sell"}
    executor = ExecutionManager(api=api, order_builder=order_builder, positions=MagicMock(),
                                fill_timeout_secs=10,
                                risk_profile=load_risk_profile("aggressive", {}))
    orders = executor.build_orders(signal, quantity=1)
    assert order_builder.build_buy_order.call_count == 2
    assert order_builder.build_sell_order.call_count == 0


def test_unwind_sell_side_graduated_phases():
    """Sell-side arb partial fill: one leg filled as a sell, unwind by buying back.
    Should try 5 graduated prices before reaching the $0.99 ceiling."""
    executor, api, positions = _make_executor_with_profile()
    unwind_responses = [
        {"orders": [{"order": {"order_id": f"u{i}", "ticker": "M2",
                                "status": "resting", "yes_price_dollars": "0.50",
                                "fill_count_fp": "0.00", "action": "buy", "side": "yes"}}]}
        for i in range(4)
    ]
    unwind_responses.append(
        {"orders": [{"order": {"order_id": "u4", "ticker": "M2",
                                "status": "executed", "yes_price_dollars": "0.99",
                                "fill_count_fp": "1.00", "action": "buy", "side": "yes"}}]}
    )
    api.batch_create_orders = AsyncMock(side_effect=[
        {"orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.46", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.99", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]},
    ] + unwind_responses)

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # 1 original + 5 unwind phases = 6 total batch_create_orders calls
    assert api.batch_create_orders.call_count == 6


def test_unwind_buy_side_graduated_prices():
    """Buy-side arb partial fill: one leg filled as a buy at $0.66, unwind by selling.
    Phase 3 should NOT be $0.01 — it should be fill_price - 4*step."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
    })
    api = MagicMock()
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    api.get_balance = AsyncMock(return_value={"balance": 10000})
    order_builder = MagicMock()
    order_builder.unwrap_order = lambda raw: raw.get("order", raw)
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    order_builder.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "buy", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    positions = MagicMock()
    positions.record_fill = MagicMock()

    sell_prices_submitted = []
    original_build_sell = order_builder.build_sell_order.side_effect
    def tracking_build_sell(ticker, yes_price, quantity):
        sell_prices_submitted.append(yes_price)
        return original_build_sell(ticker, yes_price, quantity)
    order_builder.build_sell_order.side_effect = tracking_build_sell

    api.batch_create_orders = AsyncMock(side_effect=[
        # Original arb batch: KIA resting, SAM filled
        {"orders": [
            {"order": {"order_id": "o1", "ticker": "KIA", "status": "resting",
                       "yes_price_dollars": "0.24", "fill_count_fp": "0.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "SAM", "status": "executed",
                       "yes_price_dollars": "0.66", "fill_count_fp": "1.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
        ]},
        # 5 unwind phase responses (all resting to see all prices)
        *[{"orders": [{"order": {"order_id": f"u{i}", "ticker": "SAM",
                                  "status": "resting", "yes_price_dollars": "0.50",
                                  "fill_count_fp": "0.00", "action": "sell", "side": "yes"}}]}
          for i in range(5)],
    ])

    executor = ExecutionManager(
        api=api, order_builder=order_builder, positions=positions,
        fill_timeout_secs=1, risk_profile=profile,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("KIA", 0.24), ("SAM", 0.66)],
        net_profit=0.07, profit_pct=7.15, exposure_ratio=0.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # step=0.03, fill=0.66
    # Phase 1: 0.66 - 0.03 = 0.63
    # Phase 2: 0.66 - 0.06 = 0.60
    # Phase 3: 0.66 - 0.12 = 0.54
    # Phase 4: min(max(0.66*0.5, 0.01), max(0.66-0.12, 0.01)) = min(0.33, 0.54) = 0.33
    # Phase 5: 0.01
    assert sell_prices_submitted == [0.63, 0.60, 0.54, 0.33, 0.01]


def test_buy_side_all_legs_resting_no_blacklist():
    """When ALL buy-side legs are resting (none filled), cancel all and return
    without blacklisting or unwinding — no exposure to unwind."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
    })
    api = MagicMock()
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.get_balance = AsyncMock(return_value={"balance": 10000})
    order_builder = MagicMock()
    order_builder.unwrap_order = lambda raw: raw.get("order", raw)
    order_builder.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "buy", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    positions = MagicMock()
    positions.record_fill = MagicMock()

    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "KIA", "status": "resting",
                       "yes_price_dollars": "0.24", "fill_count_fp": "0.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "SAM", "status": "resting",
                       "yes_price_dollars": "0.66", "fill_count_fp": "0.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
        ],
    })

    executor = ExecutionManager(
        api=api, order_builder=order_builder, positions=positions,
        fill_timeout_secs=60, risk_profile=profile,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("KIA", 0.24), ("SAM", 0.66)],
        net_profit=0.07, profit_pct=7.15, exposure_ratio=0.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )
    asyncio.run(executor.execute(signal, quantity=1))

    api.batch_cancel_orders.assert_called_once_with(["o1", "o2"])
    assert not executor.is_event_blacklisted("E1")
    # batch_create_orders called only once (original arb), no unwind calls
    api.batch_create_orders.assert_called_once()


def test_build_orders_mixed_actions():
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.60), ("M2", 0.35)],
        net_profit=0.03,
        profit_pct=3.0,
        exposure_ratio=0.0,
        signal_type="monotone",
        leg_actions=["sell", "buy"],
    )
    api = MagicMock()
    order_builder = MagicMock()
    order_builder.build_buy_order.return_value = {"action": "buy"}
    order_builder.build_sell_order.return_value = {"action": "sell"}
    executor = ExecutionManager(api=api, order_builder=order_builder, positions=MagicMock(),
                                fill_timeout_secs=10,
                                risk_profile=load_risk_profile("aggressive", {}))
    orders = executor.build_orders(signal, quantity=1)
    assert order_builder.build_sell_order.call_count == 1
    assert order_builder.build_buy_order.call_count == 1
