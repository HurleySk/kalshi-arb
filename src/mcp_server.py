"""MCP server for Kalshi arb bot portfolio management."""
import asyncio
import json
import logging

from mcp.server.fastmcp import FastMCP

from src.config import load_config
from src.auth import KalshiAuth
from src.api import KalshiAPI

logger = logging.getLogger(__name__)

mcp = FastMCP("kalshi-arb")

CONFIG_PATH = "config.yaml"


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

        open_pos = []
        for mp in market_positions:
            qty = int(float(mp.get("position_fp", "0")))
            if qty != 0:
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
    from src.risk import load_risk_profile
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


if __name__ == "__main__":
    mcp.run()
