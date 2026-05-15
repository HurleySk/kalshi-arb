import asyncio
import logging
import time

from src.models import TradeSignal
from src.risk import RiskProfile

logger = logging.getLogger(__name__)


class TwoSidedManager:
    def __init__(self, api, risk_profile: RiskProfile):
        self.api = api
        self._timeout_secs = risk_profile.two_sided_timeout_secs
        self._max_inventory = risk_profile.two_sided_max_inventory
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

        buy_leg, sell_leg = signal.legs
        orders = [
            self.api.build_buy_order(ticker=buy_leg[0], yes_price=buy_leg[1], quantity=quantity),
            self.api.build_sell_order(ticker=sell_leg[0], yes_price=sell_leg[1], quantity=quantity),
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
                await self.api.cancel_order(pos["sell_id"])
                await self._unwind_long(ticker, fill_price, quantity)
                self._positions.pop(ticker, None)
                return
            if pos["sell_id"] == order_id:
                pos["filled_side"] = "sell"
                await self.api.cancel_order(pos["buy_id"])
                await self._unwind_short(ticker, fill_price, quantity)
                self._positions.pop(ticker, None)
                return

    async def _unwind_long(self, ticker: str, bought_at: float, quantity: int):
        price = round(min(0.99, bought_at + 0.01), 2)
        order = self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=quantity)
        resp = await self.api.batch_create_orders([order])
        oid = self.api.unwrap_order(resp.get("orders", [{}])[0]).get("order_id", "")
        if oid:
            self._unwind_order_ids.add(oid)

    async def _unwind_short(self, ticker: str, sold_at: float, quantity: int):
        price = round(max(0.01, sold_at - 0.01), 2)
        order = self.api.build_buy_order(ticker=ticker, yes_price=price, quantity=quantity)
        resp = await self.api.batch_create_orders([order])
        oid = self.api.unwrap_order(resp.get("orders", [{}])[0]).get("order_id", "")
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

    async def timeout_loop(self):
        while True:
            await asyncio.sleep(10)
            try:
                await self._check_timeouts()
            except Exception:
                logger.exception("Two-sided timeout loop error")
