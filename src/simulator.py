import random
from dataclasses import dataclass, field


@dataclass
class FaultConfig:
    partial_fill_rate: float = 0.0
    ws_race_rate: float = 0.0
    seed: int | None = None


class SimulatedAPI:
    def __init__(self, fault_config: FaultConfig | None = None, balance_cents: int = 10000):
        self._faults = fault_config or FaultConfig()
        self._rng = random.Random(self._faults.seed)
        self._order_counter = 0
        self._balance_cents = balance_cents
        self._cancelled: set[str] = set()
        self.pending_ws_fills: list[dict] = []

    def _next_oid(self) -> str:
        self._order_counter += 1
        return f"sim-{self._order_counter:06d}"

    def build_sell_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
        return {
            "ticker": ticker, "action": "sell", "side": "yes",
            "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
        }

    def build_buy_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
        return {
            "ticker": ticker, "action": "buy", "side": "yes",
            "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
        }

    @staticmethod
    def unwrap_order(raw: dict) -> dict:
        return raw.get("order", raw)

    async def batch_create_orders(self, orders: list[dict]) -> dict:
        results = []
        for order in orders:
            oid = self._next_oid()
            price_cents = order.get("yes_price", 50)
            price_dollars = f"{price_cents / 100:.4f}"
            qty = order.get("count", 1)
            ticker = order.get("ticker", "")
            action = order.get("action", "sell")

            rests = self._rng.random() < self._faults.partial_fill_rate
            status = "resting" if rests else "executed"
            fill_count = "0.00" if rests else f"{qty:.2f}"

            inner = {
                "order_id": oid,
                "ticker": ticker,
                "status": status,
                "yes_price_dollars": price_dollars,
                "fill_count_fp": fill_count,
                "action": action,
                "side": order.get("side", "yes"),
                "initial_count_fp": f"{qty:.2f}",
            }
            results.append({"order": inner})

            if status == "executed" and self._rng.random() < self._faults.ws_race_rate:
                self.pending_ws_fills.append({
                    "order_id": oid,
                    "market_ticker": ticker,
                    "yes_price_dollars": price_dollars,
                    "count_fp": f"{qty:.2f}",
                    "action": action,
                    "side": "yes",
                    "outcome_side": "yes",
                })

        return {"orders": results}

    async def batch_cancel_orders(self, order_ids: list[str]) -> dict:
        self._cancelled.update(order_ids)
        return {}

    async def cancel_order(self, order_id: str) -> dict:
        self._cancelled.add(order_id)
        return {}

    async def get_balance(self) -> dict:
        return {"balance": self._balance_cents}
