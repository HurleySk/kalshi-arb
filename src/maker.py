import asyncio
import logging
import time
from dataclasses import dataclass, field

import aiohttp

from src.api import KalshiAPI
from src.models import TradeSignal

logger = logging.getLogger(__name__)

COMPLETED_COOLDOWN_SECS = 60.0


@dataclass
class MakerEvent:
    signal: TradeSignal
    order_ids: dict[str, str] = field(default_factory=dict)
    order_prices: dict[str, float] = field(default_factory=dict)
    filled: dict[str, float] = field(default_factory=dict)
    last_reprice_time: float = 0.0
    completing: bool = False


class MakerManager:
    REPRICE_THROTTLE_SECS = 1.0

    VALID_FILL_MODES = {"cancel_and_take", "tighten_on_fill"}

    def __init__(self, api: KalshiAPI, fill_mode: str = "cancel_and_take",
                 max_events: int = 3, risk_profile=None,
                 tighten_phase1_secs: int = 15, tighten_phase2_secs: int = 30,
                 tighten_step_cents: int = 3, track_fill_id=None):
        self.api = api
        self._track_fill_id = track_fill_id or (lambda oid: None)
        if fill_mode not in self.VALID_FILL_MODES:
            logger.warning("Unknown fill_mode %r, falling back to cancel_and_take", fill_mode)
            fill_mode = "cancel_and_take"
        self.fill_mode = fill_mode
        self.max_events = max_events
        if risk_profile is not None:
            self._tighten_phase1_secs = risk_profile.unwind_phase1_secs
            self._tighten_phase2_secs = risk_profile.unwind_phase2_secs
            self._tighten_step_cents = risk_profile.unwind_price_step_cents
        else:
            self._tighten_phase1_secs = tighten_phase1_secs
            self._tighten_phase2_secs = tighten_phase2_secs
            self._tighten_step_cents = tighten_step_cents
        self._active: dict[str, MakerEvent] = {}
        self._order_to_event: dict[str, str] = {}
        self._completed: dict[str, float] = {}
        self._posting: set[str] = set()
        self._lock = asyncio.Lock()

    def active_event_count(self) -> int:
        return len(self._active)

    def owns_order(self, order_id: str) -> bool:
        return order_id in self._order_to_event

    def is_event_active(self, event_ticker: str) -> bool:
        if event_ticker in self._active or event_ticker in self._posting:
            return True
        completed_at = self._completed.get(event_ticker)
        if completed_at:
            if time.time() - completed_at < COMPLETED_COOLDOWN_SECS:
                return True
            del self._completed[event_ticker]
        return False

    async def post(self, signal: TradeSignal) -> bool:
        if signal.event_ticker in self._active or signal.event_ticker in self._posting:
            return False
        if signal.event_ticker in self._completed:
            if time.time() - self._completed[signal.event_ticker] < COMPLETED_COOLDOWN_SECS:
                return False
        if len(self._active) >= self.max_events:
            return False

        self._posting.add(signal.event_ticker)
        try:
            async with self._lock:
                if signal.event_ticker in self._active:
                    return False

                orders = [
                    self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=1)
                    for ticker, price in signal.legs
                ]

                response = await self.api.batch_create_orders(orders)
                order_list = response.get("orders", [])

                event = MakerEvent(signal=signal)
                for o in order_list:
                    inner = self.api.unwrap_order(o)
                    oid = inner.get("order_id", "")
                    ticker = inner.get("ticker", "")
                    if not oid or not ticker:
                        continue
                    price = float(inner.get("yes_price_dollars", 0))
                    event.order_ids[ticker] = oid
                    event.order_prices[ticker] = price
                    self._order_to_event[oid] = signal.event_ticker

                n_created = len(event.order_ids)
                n_expected = len(orders)

                if n_created == 0:
                    logger.warning("No orders created for %s", signal.event_ticker)
                    self._completed[signal.event_ticker] = time.time()
                    return False

                if n_created < n_expected:
                    logger.warning(
                        "Partial orders for %s (%d/%d legs), cancelling",
                        signal.event_ticker, n_created, n_expected,
                    )
                    orphan_ids = list(event.order_ids.values())
                    for oid in orphan_ids:
                        self._order_to_event.pop(oid, None)
                    try:
                        await self.api.batch_cancel_orders(orphan_ids)
                    except Exception:
                        logger.exception("Failed to cancel partial orders on %s", signal.event_ticker)
                    self._completed[signal.event_ticker] = time.time()
                    return False

                self._active[signal.event_ticker] = event
                logger.info("Posted maker orders on %s: %d legs", signal.event_ticker, len(event.order_ids))
                return True
        except Exception:
            raise
        finally:
            self._posting.discard(signal.event_ticker)

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
            try:
                await self.api.batch_cancel_orders(unfilled_oids)
            except Exception:
                logger.exception("Failed to cancel orders on %s", event_ticker)
        self._completed[event_ticker] = time.time()
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

        if event.completing:
            return
        event.completing = True

        try:
            if self.fill_mode == "tighten_on_fill":
                await self._complete_tighten(event_ticker, event)
            else:
                await self._complete_cancel_and_take(event_ticker, event)
        except Exception:
            logger.exception("Error completing maker arb on %s", event_ticker)
            self._cleanup_event(event_ticker)

    async def _safe_cancel_order(self, order_id: str):
        if not order_id:
            return
        try:
            await self.api.cancel_order(order_id)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                logger.debug("Order %s already gone (404)", order_id)
            else:
                raise

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
            await self._safe_cancel_order(oid)

        if unfilled_tickers:
            taker_orders = [
                self.api.build_sell_order(ticker=t, yes_price=p, quantity=1)
                for t, p in unfilled_tickers
            ]
            resp = await self.api.batch_create_orders(taker_orders)
            for o in resp.get("orders", []):
                oid = self.api.unwrap_order(o).get("order_id", "")
                if oid:
                    self._track_fill_id(oid)
            logger.info("Placed taker orders for %d remaining legs on %s",
                         len(taker_orders), event_ticker)

        self._cleanup_event(event_ticker)

    async def _tighten_unfilled(self, event: MakerEvent, event_ticker: str, step: float, phase: int):
        unfilled = [
            (ticker, event.order_ids[ticker], event.order_prices[ticker])
            for ticker in event.order_ids
            if event.order_ids[ticker] not in event.filled
        ]
        for ticker, oid, price in unfilled:
            await self._safe_cancel_order(oid)
            new_price = max(price - step, 0.01)
            new_order = [self.api.build_sell_order(ticker=ticker, yes_price=new_price, quantity=1)]
            resp = await self.api.batch_create_orders(new_order)
            inner = self.api.unwrap_order(resp.get("orders", [{}])[0])
            new_oid = inner.get("order_id", "")
            if inner.get("status") == "executed":
                if new_oid:
                    self._track_fill_id(new_oid)
                event.filled[new_oid] = float(inner.get("yes_price_dollars", 0))
                logger.info("Tighten phase %d filled for %s @ %.2f", phase, ticker, new_price)
            elif new_oid:
                self._order_to_event.pop(oid, None)
                self._order_to_event[new_oid] = event_ticker
                event.order_ids[ticker] = new_oid
                event.order_prices[ticker] = new_price
        logger.info("Tighten phase %d: repriced %d legs on %s", phase, len(unfilled), event_ticker)

    async def _complete_tighten(self, event_ticker: str, event: MakerEvent):
        has_unfilled = any(oid not in event.filled for oid in event.order_ids.values())
        if not has_unfilled:
            self._cleanup_event(event_ticker)
            return

        step = self._tighten_step_cents / 100.0

        for phase, wait_secs in [(1, 0), (2, self._tighten_phase1_secs)]:
            await self._tighten_unfilled(event, event_ticker, step, phase)
            if len(event.filled) == len(event.order_ids):
                profit = sum(event.order_prices.values()) - 1.0
                logger.info("ALL LEGS FILLED after tighten phase %d on %s — profit $%.4f",
                            phase, event_ticker, profit)
                self._cleanup_event(event_ticker)
                return
            if wait_secs > 0:
                await asyncio.sleep(wait_secs)

        wait = self._tighten_phase2_secs - self._tighten_phase1_secs
        if wait > 0:
            await asyncio.sleep(wait)

        logger.info("Tighten phase 3: crossing spread on %s", event_ticker)
        await self._complete_cancel_and_take(event_ticker, event)

    async def on_orderbook_update(self, event_ticker: str, orderbooks: dict):
        event = self._active.get(event_ticker)
        if not event or event.completing:
            return
        if not event.order_ids:
            return

        now = time.time()
        if now - event.last_reprice_time < self.REPRICE_THROTTLE_SECS:
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

        async with self._lock:
            for ticker, new_price in bid_prices:
                old_price = event.order_prices.get(ticker, 0)
                oid = event.order_ids.get(ticker, "")
                if oid in event.filled:
                    continue
                if abs(new_price - old_price) > 1e-9:
                    await self._safe_cancel_order(oid)
                    new_order = [self.api.build_sell_order(ticker=ticker, yes_price=new_price, quantity=1)]
                    resp = await self.api.batch_create_orders(new_order)
                    new_inner = self.api.unwrap_order(resp.get("orders", [{}])[0])
                    new_oid = new_inner.get("order_id", "")
                    if new_oid and new_inner.get("status") == "executed":
                        self._track_fill_id(new_oid)
                    if new_oid:
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
        self._completed[event_ticker] = time.time()
