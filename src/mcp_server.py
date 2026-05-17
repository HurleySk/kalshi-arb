"""MCP server for Kalshi arb bot portfolio management."""
import asyncio
import json
import logging

from mcp.server.fastmcp import FastMCP

from src.config import load_config
from src.exchanges.kalshi.auth import KalshiAuth
from src.exchanges.kalshi.api import KalshiAPI

logger = logging.getLogger(__name__)

mcp = FastMCP("kalshi-arb")

CONFIG_PATH = "config.yaml"


def _db_kwargs(cfg) -> dict:
    """Build kwargs for Analytics/ReplayEngine from config."""
    session_dir = getattr(cfg, "recording_session_dir", None)
    if session_dir and cfg.recording_enabled:
        import os
        if os.path.isdir(session_dir):
            return {"session_dir": session_dir}
    db_path = cfg.recording_db_path if cfg.recording_enabled else "data/arb_history.db"
    return {"db_path": db_path}


async def _get_api() -> KalshiAPI:
    cfg = load_config(CONFIG_PATH)
    auth = KalshiAuth(api_key_id=cfg.api_key_id, private_key_path=cfg.private_key_path)
    return KalshiAPI(base_url=cfg.rest_base_url, auth=auth)


@mcp.tool()
async def close_all_positions() -> str:
    """Cancel all open orders and close all positions. Use in emergencies or to go flat."""
    api = await _get_api()
    results = []
    try:
        orders_resp = await api.get_open_orders()
        orders = orders_resp.get("orders", [])
        resting = [o for o in orders if o.get("status") in ("resting", "pending", "open")]
        if resting:
            order_ids = [o["order_id"] for o in resting]
            await api.batch_cancel_orders(order_ids)
            results.append(f"Cancelled {len(resting)} open orders")
        else:
            results.append("No open orders")

        positions_resp = await api.get_positions()
        market_positions = positions_resp.get("market_positions", [])

        from src.core.reservation_store import ReservationStore
        store = ReservationStore(path="data/reservations.json")

        open_pos = []
        for mp in market_positions:
            qty = int(float(mp.get("position_fp", "0")))
            if qty != 0:
                if store.is_reserved(mp["ticker"]):
                    results.append(f"  SKIPPED {mp['ticker']} (reserved)")
                    continue
                open_pos.append((mp["ticker"], qty))

        if not open_pos:
            results.append("No open positions")
        else:
            close_orders = [api.build_close_order(ticker, qty) for ticker, qty in open_pos]

            resp = await api.batch_create_orders(close_orders)
            for o in resp.get("orders", []):
                inner = api.unwrap_order(o)
                status = inner.get("status")
                fill = inner.get("fill_count_fp", "0")
                total = inner.get("initial_count_fp", "0")
                results.append(f"  {inner.get('ticker', 'unknown')}: {status} (fill {fill}/{total})")
            results.append(f"Sent {len(close_orders)} close orders")

        balance = await api.get_balance()
        cash = balance.get("balance", 0) / 100
        portfolio = balance.get("portfolio_value", 0) / 100
        results.append(f"Balance: ${cash:.2f} cash, ${portfolio:.2f} portfolio")
    finally:
        await api.close()

    return "\n".join(results)


@mcp.tool()
async def close_position(ticker: str) -> str:
    """Close a specific position by market ticker.

    Args:
        ticker: The market ticker to close (e.g. KXWTAMATCH-26MAY12GAUAND-AND)
    """
    api = await _get_api()
    try:
        positions_resp = await api.get_positions()
        market_positions = positions_resp.get("market_positions", [])

        target = None
        for mp in market_positions:
            if mp.get("ticker") == ticker:
                target = mp
                break

        if not target:
            return f"No position found for {ticker}"

        from src.core.reservation_store import ReservationStore
        store = ReservationStore(path="data/reservations.json")
        if store.is_reserved(ticker):
            reserved_qty = store.get_reserved_quantity(ticker, "yes")
            return (f"WARNING: {ticker} is reserved ({reserved_qty}x). "
                    f"Use release_position first if you want to close it.")

        qty = int(float(target.get("position_fp", "0")))
        if qty == 0:
            return f"Position for {ticker} is already flat (qty=0)"

        order = api.build_close_order(ticker, qty)
        resp = await api.batch_create_orders([order])
        inner = api.unwrap_order(resp.get("orders", [{}])[0])
        status = inner.get("status")
        fill = inner.get("fill_count_fp", "0")
        total = inner.get("initial_count_fp", "0")

        balance = await api.get_balance()
        cash = balance.get("balance", 0) / 100

        return (f"Closed {ticker}: {order['action']} {order['count']}x "
                f"@ ${order['yes_price']/100:.2f} → {status} (fill {fill}/{total})\n"
                f"Balance: ${cash:.2f}")
    finally:
        await api.close()


@mcp.tool()
async def get_positions() -> str:
    """View all current positions and balance without making changes."""
    api = await _get_api()
    try:
        positions_resp = await api.get_positions()
        market_positions = positions_resp.get("market_positions", [])

        lines = []
        for mp in market_positions:
            qty = float(mp.get("position_fp", "0"))
            if qty != 0:
                ticker = mp["ticker"]
                exposure = mp.get("market_exposure_dollars", "0")
                lines.append(f"  {ticker}: {int(qty)} contracts, exposure ${exposure}")

        if not lines:
            lines.append("No open positions")

        balance = await api.get_balance()
        cash = balance.get("balance", 0) / 100
        portfolio = balance.get("portfolio_value", 0) / 100
        lines.append(f"\nBalance: ${cash:.2f} cash, ${portfolio:.2f} portfolio")
        return "\n".join(lines)
    finally:
        await api.close()


@mcp.tool()
async def get_risk_profile() -> str:
    """Show the active risk profile and all thresholds."""
    from src.core.risk import load_risk_profile
    cfg = load_config(CONFIG_PATH)
    profile = load_risk_profile(cfg.risk_mode, cfg.strategy_overrides)

    lines = [
        f"Risk mode: {cfg.risk_mode}",
        f"  min_volume_24h: {profile.min_volume_24h}",
        f"  min_bid_depth: {profile.min_bid_depth}",
        f"  min_profit_pct: {profile.min_profit_pct}%",
        f"  require_recent_trades: {profile.require_recent_trades}",
        f"  max_exposure_ratio: {profile.max_exposure_ratio}",
        f"  near_term_hours: {profile.near_term_hours}",
        f"  hurdle_rate_annual_pct: {profile.hurdle_rate_annual_pct}%",
        f"  unwind_phase1_secs: {profile.unwind_phase1_secs}",
        f"  unwind_phase2_secs: {profile.unwind_phase2_secs}",
        f"  unwind_price_step_cents: {profile.unwind_price_step_cents}",
    ]

    if cfg.strategy_overrides:
        lines.append(f"\nOverrides applied: {cfg.strategy_overrides}")

    return "\n".join(lines)


@mcp.tool()
async def get_performance_report(days: int = 7) -> str:
    """Get strategy performance report for the last N days.
    Includes per-strategy PnL, rejection funnel, fill rates, and balance curve.

    Args:
        days: Number of days to look back (default: 7)
    """
    import time as _time
    from src.core.analytics import Analytics
    cfg = load_config(CONFIG_PATH)
    analytics = Analytics(**_db_kwargs(cfg))
    end = _time.time()
    start = end - (days * 86400)
    report = analytics.full_report(start=start, end=end)
    analytics.close()
    return report


@mcp.tool()
async def get_parameter_sensitivity(
    parameter: str,
    range_start: float,
    range_end: float,
    step: float,
    days: int = 7,
) -> str:
    """Run a parameter sweep and return sensitivity analysis.
    Shows signal count and theoretical profit at each parameter value.
    Highlights plateau regions for robust parameter selection.

    Args:
        parameter: Parameter name (e.g. min_profit_pct, min_bid_depth)
        range_start: Start of sweep range
        range_end: End of sweep range
        step: Step size
        days: Days of data to use (default: 7)
    """
    import time as _time
    from src.core.replay import ReplayEngine
    cfg = load_config(CONFIG_PATH)
    engine = ReplayEngine(**_db_kwargs(cfg), risk_mode=cfg.risk_mode)

    values = []
    v = range_start
    while v <= range_end + step / 2:
        values.append(round(v, 6))
        v += step

    end = _time.time()
    start = end - (days * 86400)
    results = engine.sweep({parameter: values}, start=start, end=end)

    lines = [f"Parameter Sensitivity: {parameter}", "-" * 50]
    max_profit = max((abs(r["theoretical_profit"]) for r in results), default=1) or 1
    for r in results:
        val = r["params"][parameter]
        sc = r["signal_count"]
        profit = r["theoretical_profit"]
        bar_len = int(abs(profit) / max_profit * 20)
        bar = "█" * bar_len
        lines.append(f"  {val:8.2f} │ {sc:3d} signals │ ${profit:8.4f} │ {bar}")

    plateaus = engine.find_plateaus(results, parameter)
    if plateaus:
        lines.append(f"\nPlateau regions (robust values):")
        for lo, hi in plateaus:
            lines.append(f"  {lo} → {hi}")

    engine.close()
    return "\n".join(lines)


@mcp.tool()
async def get_near_misses(
    strategy: str = "all",
    threshold_pct: float = 0.5,
    days: int = 1,
) -> str:
    """Get signals that nearly fired but were rejected.
    Useful for identifying if thresholds are too tight.

    Args:
        strategy: Filter by strategy type, or "all" (default: "all")
        threshold_pct: How close to firing threshold to include (default: 0.5%)
        days: Days to look back (default: 1)
    """
    import time as _time
    from src.core.analytics import Analytics
    cfg = load_config(CONFIG_PATH)
    analytics = Analytics(**_db_kwargs(cfg))
    end = _time.time()
    start = end - (days * 86400)
    nm = analytics.near_miss_analysis(start=start, end=end)
    analytics.close()

    lines = [f"Near-Miss Analysis (last {days} day(s))", "-" * 50]
    lines.append(f"Total near-misses: {nm['total_near_misses']}")
    if nm["by_strategy"]:
        for strat, count in sorted(nm["by_strategy"].items(), key=lambda x: -x[1]):
            if strategy == "all" or strategy == strat:
                lines.append(f"  {strat}: {count}")
    if nm["best_miss"]:
        lines.append(f"\nBest missed opportunity:")
        lines.append(f"  {nm['best_miss']['event_ticker']} (bid_sum={nm['best_miss']['bid_sum']:.4f})")

    return "\n".join(lines)


@mcp.tool()
async def get_signal_history(
    strategy: str = "all",
    outcome: str = "all",
    days: int = 7,
    limit: int = 50,
) -> str:
    """Get historical signal evaluations with filtering.

    Args:
        strategy: Filter by strategy type (taker, buy_side, near_expiry, monotone, maker, two_sided, or "all")
        outcome: Filter by outcome (fire, reject, near_miss, or "all")
        days: Days to look back (default: 7)
        limit: Max results to return (default: 50)
    """
    import sqlite3
    import time as _time
    from datetime import datetime, timezone
    cfg = load_config(CONFIG_PATH)
    kwargs = _db_kwargs(cfg)

    end = _time.time()
    start = end - (days * 86400)
    conditions = ["ts >= ?", "ts <= ?"]
    params: list = [start, end]
    if strategy != "all":
        conditions.append("strategy = ?")
        params.append(strategy)
    if outcome != "all":
        conditions.append("outcome = ?")
        params.append(outcome)

    where = " AND ".join(conditions)
    sql = f"SELECT ts, event_ticker, strategy, outcome, bid_sum, profit_pct FROM signal_evaluations WHERE {where} ORDER BY ts DESC LIMIT ?"
    if "session_dir" in kwargs:
        from src.session_reader import SessionReader
        rows = SessionReader(kwargs["session_dir"]).query_across(sql, tuple(params + [limit]), start=start, end=end)
    else:
        db_path = kwargs.get("db_path", "data/arb_history.db")
        conn = sqlite3.connect(db_path)
        rows = conn.execute(sql, params + [limit]).fetchall()
        conn.close()

    lines = [f"Signal History (last {days} day(s), {strategy}/{outcome})", "-" * 70]
    for ts, event, strat, out, bid_sum, profit_pct in rows:
        dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")
        bid_str = f"bid={bid_sum:.4f}" if bid_sum else "bid=N/A"
        pct_str = f"pct={profit_pct:.2f}%" if profit_pct else ""
        lines.append(f"  {dt_str} │ {strat:12s} │ {out:9s} │ {event} │ {bid_str} {pct_str}")

    lines.append(f"\n{len(rows)} results (limit {limit})")
    return "\n".join(lines)


@mcp.tool()
async def get_replay_comparison(
    parameter: str,
    current_value: float,
    proposed_value: float,
    days: int = 7,
) -> str:
    """Compare current vs proposed parameter values using replay.
    Shows side-by-side signal counts, theoretical profit, and risk metrics.
    Uses train/test split automatically (first half train, second half test).

    Args:
        parameter: Parameter to compare
        current_value: Current parameter value
        proposed_value: Proposed new value
        days: Days of data to use (default: 7)
    """
    import time as _time
    from src.core.replay import ReplayEngine
    cfg = load_config(CONFIG_PATH)
    engine = ReplayEngine(**_db_kwargs(cfg), risk_mode=cfg.risk_mode)

    end = _time.time()
    start = end - (days * 86400)
    midpoint = start + (end - start) / 2

    results = engine.sweep(
        {parameter: [current_value, proposed_value]},
        start=start, end=end,
        train_end=midpoint, test_start=midpoint,
    )
    engine.close()

    if len(results) < 2:
        return "Insufficient data for comparison."

    current = results[0]
    proposed = results[1]

    lines = [
        f"Replay Comparison: {parameter}",
        "=" * 60,
        f"{'':20s} │ {'Current':>12s} │ {'Proposed':>12s}",
        "-" * 60,
        f"{'Value':20s} │ {current_value:12.2f} │ {proposed_value:12.2f}",
        f"{'Train signals':20s} │ {current['train_signal_count']:12d} │ {proposed['train_signal_count']:12d}",
        f"{'Train profit':20s} │ ${current['train_theoretical_profit']:11.4f} │ ${proposed['train_theoretical_profit']:11.4f}",
        f"{'Test signals':20s} │ {current['test_signal_count']:12d} │ {proposed['test_signal_count']:12d}",
        f"{'Test profit':20s} │ ${current['test_theoretical_profit']:11.4f} │ ${proposed['test_theoretical_profit']:11.4f}",
        f"{'Total signals':20s} │ {current['signal_count']:12d} │ {proposed['signal_count']:12d}",
        f"{'Total profit':20s} │ ${current['theoretical_profit']:11.4f} │ ${proposed['theoretical_profit']:11.4f}",
        "=" * 60,
    ]

    delta_signals = proposed["signal_count"] - current["signal_count"]
    delta_profit = proposed["theoretical_profit"] - current["theoretical_profit"]
    lines.append(f"Delta: {delta_signals:+d} signals, ${delta_profit:+.4f} profit")
    if proposed["test_theoretical_profit"] < current["test_theoretical_profit"]:
        lines.append("WARNING: Proposed value performs WORSE on out-of-sample test data")

    return "\n".join(lines)


@mcp.tool()
async def reserve_position(
    ticker: str,
    side: str,
    quantity: int,
    exchange: str = "kalshi",
    note: str = "",
) -> str:
    """Reserve a position as user-owned. The bot will not close, unwind, or interfere with it.

    Args:
        ticker: Market ticker (e.g. KXBTC-25MAY16-T55000)
        side: Position side ("yes" or "no")
        quantity: Number of contracts to reserve
        exchange: Exchange name (default: kalshi)
        note: Optional annotation for this reservation
    """
    from src.core.reservation_store import ReservationStore
    store = ReservationStore(path="data/reservations.json")
    store.reserve(ticker, side, quantity, exchange, note)
    all_res = store.list_all()
    lines = [f"Reserved {quantity}x {side} on {ticker} ({exchange})"]
    if note:
        lines.append(f"  Note: {note}")
    lines.append(f"\nAll reservations ({len(all_res)}):")
    for r in all_res:
        lines.append(f"  {r.ticker}: {r.quantity}x {r.side} on {r.exchange}" +
                     (f" — {r.note}" if r.note else ""))
    return "\n".join(lines)


@mcp.tool()
async def release_position(ticker: str) -> str:
    """Release a previously reserved position. The bot may now manage this position.

    Args:
        ticker: Market ticker to release
    """
    from src.core.reservation_store import ReservationStore
    store = ReservationStore(path="data/reservations.json")
    if not store.is_reserved(ticker):
        return f"No reservation found for {ticker}"
    store.release(ticker)
    all_res = store.list_all()
    lines = [f"Released reservation on {ticker}"]
    lines.append(f"\nRemaining reservations ({len(all_res)}):")
    for r in all_res:
        lines.append(f"  {r.ticker}: {r.quantity}x {r.side} on {r.exchange}" +
                     (f" — {r.note}" if r.note else ""))
    return "\n".join(lines)


@mcp.tool()
async def list_reservations() -> str:
    """List all active position reservations."""
    from src.core.reservation_store import ReservationStore
    store = ReservationStore(path="data/reservations.json")
    all_res = store.list_all()
    if not all_res:
        return "No active reservations."
    lines = [f"Active reservations ({len(all_res)}):"]
    for r in all_res:
        lines.append(
            f"  {r.ticker}: {r.quantity}x {r.side} on {r.exchange}"
            + (f" — {r.note}" if r.note else "")
            + f" (since {r.created_at[:10]})"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
