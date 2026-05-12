import logging
import time
from dataclasses import dataclass, field

from src.api import KalshiAPI
from src.models import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class MakerEvent:
    signal: TradeSignal
    order_ids: dict[str, str] = field(default_factory=dict)
    order_prices: dict[str, float] = field(default_factory=dict)
    filled: dict[str, float] = field(default_factory=dict)
    last_reprice_time: float = 0.0


class MakerManager:
    REPRICE_THROTTLE_SECS = 1.0

    def __init__(self, api: KalshiAPI, fill_mode: str = "cancel_and_take",
                 max_events: int = 3):
        self.api = api
        self.fill_mode = fill_mode
        self.max_events = max_events
        self._active: dict[str, MakerEvent] = {}
        self._order_to_event: dict[str, str] = {}

    def active_event_count(self) -> int:
        return len(self._active)

    def owns_order(self, order_id: str) -> bool:
        return order_id in self._order_to_event

    def is_event_active(self, event_ticker: str) -> bool:
        return event_ticker in self._active

    async def post(self, signal: TradeSignal):
        if signal.event_ticker in self._active:
            return
        if len(self._active) >= self.max_events:
            return

        orders = [
            self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=1)
            for ticker, price in signal.legs
        ]

        response = await self.api.batch_create_orders(orders)
        order_list = response.get("orders", [])

        event = MakerEvent(signal=signal)
        for o in order_list:
            inner = o.get("order", o)
            oid = inner.get("order_id", "")
            ticker = inner.get("ticker", "")
            price = float(inner.get("yes_price_dollars", 0))
            event.order_ids[ticker] = oid
            event.order_prices[ticker] = price
            self._order_to_event[oid] = signal.event_ticker

        self._active[signal.event_ticker] = event
        logger.info("Posted maker orders on %s: %d legs", signal.event_ticker, len(order_list))

    async def cancel_event(self, event_ticker: str):
        event = self._active.pop(event_ticker, None)
        if not event:
            return
        unfilled_oids = [
            oid for oid in event.order_ids.values()
            if oid not in event.filled
        ]
        for oid in event.order_ids.values():
            self._order_to_event.pop(oid, None)
        if unfilled_oids:
            await self.api.batch_cancel_orders(unfilled_oids)
        logger.info("Cancelled maker orders on %s", event_ticker)

    async def cancel_all(self):
        for event_ticker in list(self._active.keys()):
            await self.cancel_event(event_ticker)

    async def handle_fill(self, order_id: str, ticker: str, price: float, quantity: int):
        event_ticker = self._order_to_event.get(order_id)
        if not event_ticker:
            return
        event = self._active.get(event_ticker)
        if not event:
            return

        event.filled[order_id] = price
        logger.info("Maker fill: %s @ %.2f on %s (%d/%d legs)",
                     ticker, price, event_ticker, len(event.filled), len(event.order_ids))

        if len(event.filled) == len(event.order_ids):
            profit = sum(event.order_prices.values()) - 1.0
            logger.info("ALL MAKER LEGS FILLED on %s — profit $%.4f (0%% fees!)",
                         event_ticker, profit)
            self._cleanup_event(event_ticker)
            return

        if self.fill_mode == "cancel_and_take":
            await self._complete_cancel_and_take(event_ticker, event)

    async def _complete_cancel_and_take(self, event_ticker: str, event: MakerEvent):
        unfilled_tickers = [
            (ticker, event.order_prices[ticker])
            for ticker, oid in event.order_ids.items()
            if oid not in event.filled
        ]
        unfilled_oids = [
            oid for oid in event.order_ids.values()
            if oid not in event.filled
        ]

        for oid in unfilled_oids:
            await self.api.cancel_order(oid)

        if unfilled_tickers:
            taker_orders = [
                self.api.build_sell_order(ticker=t, yes_price=p, quantity=1)
                for t, p in unfilled_tickers
            ]
            await self.api.batch_create_orders(taker_orders)
            logger.info("Placed taker orders for %d remaining legs on %s",
                         len(taker_orders), event_ticker)

        self._cleanup_event(event_ticker)

    async def on_orderbook_update(self, event_ticker: str, orderbooks: dict):
        event = self._active.get(event_ticker)
        if not event:
            return

        bid_prices = []
        for ticker in event.order_ids:
            book = orderbooks.get(ticker)
            if not book:
                return
            best_bid = book.best_yes_bid()
            if best_bid is None:
                await self.cancel_event(event_ticker)
                return
            bid_prices.append((ticker, best_bid))

        gross_profit = sum(p for _, p in bid_prices) - 1.0
        if gross_profit <= 0:
            logger.info("Maker arb on %s no longer profitable (sum=%.2f), cancelling",
                         event_ticker, sum(p for _, p in bid_prices))
            await self.cancel_event(event_ticker)
            return

        now = time.time()
        if now - event.last_reprice_time < self.REPRICE_THROTTLE_SECS:
            return

        for ticker, new_price in bid_prices:
            old_price = event.order_prices.get(ticker, 0)
            oid = event.order_ids.get(ticker, "")
            if oid in event.filled:
                continue
            if abs(new_price - old_price) > 1e-9:
                await self.api.cancel_order(oid)
                new_order = [self.api.build_sell_order(ticker=ticker, yes_price=new_price, quantity=1)]
                resp = await self.api.batch_create_orders(new_order)
                new_inner = resp.get("orders", [{}])[0].get("order", {})
                new_oid = new_inner.get("order_id", "")
                self._order_to_event.pop(oid, None)
                self._order_to_event[new_oid] = event_ticker
                event.order_ids[ticker] = new_oid
                event.order_prices[ticker] = new_price

        event.last_reprice_time = now

    def _cleanup_event(self, event_ticker: str):
        event = self._active.pop(event_ticker, None)
        if event:
            for oid in event.order_ids.values():
                self._order_to_event.pop(oid, None)
