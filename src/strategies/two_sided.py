import asyncio
import logging
import time

from src.core.models import TradeSignal
from src.core.risk import RiskProfile

logger = logging.getLogger(__name__)


class TwoSidedManager:
    def __init__(self, api, risk_profile: RiskProfile, order_builder=None,
                 capital_guard=None, exchange_name: str = "kalshi",
                 maker_order_ttl_secs: int = 300):
        self.api = api
        self.order_builder = order_builder if order_builder is not None else api
        self._timeout_secs = risk_profile.two_sided_timeout_secs
        self._max_inventory = risk_profile.two_sided_max_inventory
        self._capital_guard = capital_guard
        self._exchange_name = exchange_name
        self._order_ttl_secs = maker_order_ttl_secs
        # ticker → {buy_id, sell_id, filled_side, quantity, posted_at}
        self._positions: dict[str, dict] = {}
        self._unwind_order_ids: set[str] = set()

    @property
    def total_inventory(self) -> int:
        return sum(p["quantity"] for p in self._positions.values())

    async def post(self, signal: TradeSignal) -> bool:
        ticker = signal.event_ticker
        if ticker in self._positions:
            return False

        remaining = self._max_inventory - self.total_inventory
        quantity = min(signal.quantity, remaining)
        if quantity <= 0:
            return False

        if self._capital_guard:
            cost = sum(price for _, price in signal.legs) * quantity
            if not self._capital_guard.can_execute(self._exchange_name, cost):
                logger.info("capital_limit: skipping two-sided on %s", signal.event_ticker)
                return False

        exp_ts = int(time.time()) + self._order_ttl_secs
        buy_leg, sell_leg = signal.legs
        orders = [
            self.order_builder.build_buy_order(buy_leg[0], buy_leg[1], quantity,
                                               expiration_ts=exp_ts),
            self.order_builder.build_sell_order(sell_leg[0], sell_leg[1], quantity,
                                                expiration_ts=exp_ts),
        ]
        resp = await self.api.batch_create_orders(orders)
        order_list = resp.get("orders", [])
        if len(order_list) < 2:
            return False

        buy_inner = order_list[0].get("order", order_list[0])
        sell_inner = order_list[1].get("order", order_list[1])

        self._positions[ticker] = {
            "buy_id": buy_inner.get("order_id"),
            "sell_id": sell_inner.get("order_id"),
            "filled_side": None,
            "quantity": quantity,
            "posted_at": time.time(),
        }
        if self._capital_guard:
            cost = buy_leg[1] * quantity + sell_leg[1] * quantity
            self._capital_guard.commit(
                self._exchange_name,
                f"twosided_{ticker}",
                cost,
            )
        logger.info("Two-sided posted on %s bid=%.2f ask=%.2f qty=%d",
                    ticker, buy_leg[1], sell_leg[1], quantity)
        return True

    def owns_order(self, order_id: str) -> bool:
        if order_id in self._unwind_order_ids:
            return True
        for pos in self._positions.values():
            if pos["buy_id"] == order_id or pos["sell_id"] == order_id:
                return True
        return False

    async def handle_fill(self, order_id: str, fill_price: float, quantity: int):
        for ticker, pos in list(self._positions.items()):
            if pos["buy_id"] == order_id:
                pos["filled_side"] = "buy"
                self._positions.pop(ticker, None)
                if self._capital_guard:
                    self._capital_guard.release(self._exchange_name, f"twosided_{ticker}")
                await self.api.cancel_order(pos["sell_id"])
                await self._unwind_long(ticker, fill_price, quantity)
                return
            if pos["sell_id"] == order_id:
                pos["filled_side"] = "sell"
                self._positions.pop(ticker, None)
                if self._capital_guard:
                    self._capital_guard.release(self._exchange_name, f"twosided_{ticker}")
                await self.api.cancel_order(pos["buy_id"])
                await self._unwind_short(ticker, fill_price, quantity)
                return

    async def _unwind_long(self, ticker: str, bought_at: float, quantity: int):
        price = round(min(0.99, bought_at + 0.01), 2)
        order = self.order_builder.build_sell_order(ticker, price, quantity,
                                                    expiration_ts=int(time.time()) + 60)
        resp = await self.api.batch_create_orders([order])
        oid = self.order_builder.unwrap_order(resp.get("orders", [{}])[0]).get("order_id", "")
        if oid:
            self._unwind_order_ids.add(oid)

    async def _unwind_short(self, ticker: str, sold_at: float, quantity: int):
        price = round(max(0.01, sold_at - 0.01), 2)
        order = self.order_builder.build_buy_order(ticker, price, quantity,
                                                   expiration_ts=int(time.time()) + 60)
        resp = await self.api.batch_create_orders([order])
        oid = self.order_builder.unwrap_order(resp.get("orders", [{}])[0]).get("order_id", "")
        if oid:
            self._unwind_order_ids.add(oid)

    async def _check_timeouts(self):
        now = time.time()
        for ticker, pos in list(self._positions.items()):
            if pos["filled_side"] is None and now - pos["posted_at"] > self._timeout_secs:
                logger.info("Two-sided timeout on %s — cancelling both sides", ticker)
                await self.api.cancel_order(pos["buy_id"])
                await self.api.cancel_order(pos["sell_id"])
                self._positions.pop(ticker, None)
                if self._capital_guard:
                    self._capital_guard.release(self._exchange_name, f"twosided_{ticker}")

    async def timeout_loop(self):
        while True:
            await asyncio.sleep(10)
            try:
                await self._check_timeouts()
            except Exception:
                logger.exception("Two-sided timeout loop error")
