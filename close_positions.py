#!/usr/bin/env python3
"""Emergency: cancel all open orders and close all positions."""
import asyncio
import json
import sys

from src.config import load_config
from src.auth import KalshiAuth
from src.api import KalshiAPI


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)
    auth = KalshiAuth(api_key_id=cfg.api_key_id, private_key_path=cfg.private_key_path)
    api = KalshiAPI(base_url=cfg.rest_base_url, auth=auth)

    try:
        print(f"=== Emergency close ({cfg.mode.upper()} mode) ===\n")

        # 1. Cancel all open/resting orders
        orders_resp = await api.get_open_orders()
        orders = orders_resp.get("orders", [])
        resting = [o for o in orders if o.get("status") in ("resting", "pending", "open")]
        if resting:
            order_ids = [o["order_id"] for o in resting]
            print(f"Cancelling {len(resting)} open orders...")
            for o in resting:
                print(f"  {o['ticker']} {o['action']} {o['side']} "
                      f"@ ${o.get('yes_price_dollars', '?')} "
                      f"x{o.get('remaining_count_fp', '?')} [{o['status']}]")
            await api.batch_cancel_orders(order_ids)
            print("Done.\n")
        else:
            print("No open orders.\n")

        # 2. Get positions (field is position_fp, a string like "-3.00")
        positions_resp = await api.get_positions()
        market_positions = positions_resp.get("market_positions", [])

        open_pos = []
        for mp in market_positions:
            qty = int(float(mp.get("position_fp", "0")))
            if qty != 0:
                open_pos.append((mp["ticker"], qty))

        if not open_pos:
            print("No open positions. You're flat.")
        else:
            print(f"Found {len(open_pos)} open positions:")
            close_orders = []
            for ticker, qty in open_pos:
                print(f"  {ticker}: {qty} contracts")
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

            print(f"\nClosing {len(close_orders)} positions...")
            resp = await api.batch_create_orders(close_orders)
            for o in resp.get("orders", []):
                inner = o.get("order", o)
                status = inner.get("status")
                fill = inner.get("fill_count_fp", "0")
                total = inner.get("initial_count_fp", "0")
                print(f"  {inner['ticker']}: {status} (fill {fill}/{total})")

        # 3. Final state
        print(f"\n=== Final state ===")
        balance = await api.get_balance()
        print(f"Cash: ${balance.get('balance', 0) / 100:.2f}")
        print(f"Portfolio value: ${balance.get('portfolio_value', 0) / 100:.2f}")

    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
