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

    def _cleanup_event(self, event_ticker: str):
        event = self._active.pop(event_ticker, None)
        if event:
            for oid in event.order_ids.values():
                self._order_to_event.pop(oid, None)
