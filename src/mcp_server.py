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
            close_orders = []
            for ticker, qty in open_pos:
                if qty < 0:
                    close_orders.append({
                        "ticker": ticker,
                        "action": "buy",
                        "side": "yes",
                        "type": "limit",
                        "yes_price": 99,
                        "count": abs(qty),
                    })
                else:
                    close_orders.append({
                        "ticker": ticker,
                        "action": "sell",
                        "side": "yes",
                        "type": "limit",
                        "yes_price": 1,
                        "count": qty,
                    })

            resp = await api.batch_create_orders(close_orders)
            for o in resp.get("orders", []):
                inner = o.get("order", o)
                status = inner.get("status")
                fill = inner.get("fill_count_fp", "0")
                total = inner.get("initial_count_fp", "0")
                results.append(f"  {inner['ticker']}: {status} (fill {fill}/{total})")
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

        if qty < 0:
            order = {
                "ticker": ticker,
                "action": "buy",
                "side": "yes",
                "type": "limit",
                "yes_price": 99,
                "count": abs(qty),
            }
        else:
            order = {
                "ticker": ticker,
                "action": "sell",
                "side": "yes",
                "type": "limit",
                "yes_price": 1,
                "count": qty,
            }

        resp = await api.batch_create_orders([order])
        inner = resp.get("orders", [{}])[0]
        inner = inner.get("order", inner)
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


if __name__ == "__main__":
    mcp.run()
