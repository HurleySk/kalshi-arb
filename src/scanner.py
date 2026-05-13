import asyncio
import inspect
import json
import logging
from typing import Callable

import websockets

from src.auth import KalshiAuth
from src.models import Orderbook

logger = logging.getLogger(__name__)


class OrderbookManager:
    def __init__(self):
        self._books: dict[str, Orderbook] = {}
        self._event_markets: dict[str, list[str]] = {}
        self._market_to_event: dict[str, str] = {}

    def register_event(self, event_ticker: str, market_tickers: list[str]):
        self._event_markets[event_ticker] = market_tickers
        for t in market_tickers:
            self._market_to_event[t] = event_ticker

    def unregister_event(self, event_ticker: str):
        tickers = self._event_markets.pop(event_ticker, [])
        for t in tickers:
            self._market_to_event.pop(t, None)
            self._books.pop(t, None)

    def get_event_for_market(self, market_ticker: str) -> str | None:
        return self._market_to_event.get(market_ticker)

    def apply_snapshot(self, ticker: str, snapshot: dict):
        yes_bids = {
            round(float(p) * 100): float(q)
            for p, q in snapshot.get("yes_dollars_fp", [])
        }
        no_bids = {
            round(float(p) * 100): float(q)
            for p, q in snapshot.get("no_dollars_fp", [])
        }
        self._books[ticker] = Orderbook(yes_bids=yes_bids, no_bids=no_bids)

    def apply_delta(self, ticker: str, delta: dict):
        book = self._books.get(ticker)
        if book is None:
            return

        price_cents = round(float(delta["price_dollars"]) * 100)
        delta_qty = float(delta["delta_fp"])
        side = delta["side"]
        levels = book.yes_bids if side == "yes" else book.no_bids

        new_qty = levels.get(price_cents, 0) + delta_qty
        if new_qty <= 0:
            levels.pop(price_cents, None)
        else:
            levels[price_cents] = new_qty

    def get_orderbook(self, ticker: str) -> Orderbook | None:
        return self._books.get(ticker)

    def get_event_markets(self, event_ticker: str) -> list[str]:
        return self._event_markets.get(event_ticker, [])

    def get_registered_market_count(self, event_ticker: str) -> int:
        return len(self._event_markets.get(event_ticker, []))

    def get_event_orderbooks(self, event_ticker: str) -> dict[str, Orderbook]:
        tickers = self._event_markets.get(event_ticker, [])
        result = {}
        for t in tickers:
            book = self._books.get(t)
            if book:
                result[t] = book
        return result


class MarketScanner:
    def __init__(
        self,
        ws_url: str,
        auth: KalshiAuth,
        orderbook_mgr: OrderbookManager,
        on_orderbook_update: Callable[[str], None] | None = None,
        on_fill: Callable[[dict], None] | None = None,
    ):
        self.ws_url = ws_url
        self.auth = auth
        self.orderbook_mgr = orderbook_mgr
        self.on_orderbook_update = on_orderbook_update
        self.on_fill = on_fill
        self._ws = None
        self._sub_id = 0
        self._running = False
        self._stopping = False
        self._subscribed_tickers: set[str] = set()
        self._fills_subscribed = False

    async def connect(self):
        delay = 5
        attempt = 0
        while not self._stopping:
            attempt += 1
            try:
                headers = self.auth.build_headers("GET", "/trade-api/ws/v2")
                self._ws = await websockets.connect(self.ws_url, additional_headers=headers)
                self._running = True
                if attempt > 1:
                    logger.info("WebSocket connected after %d attempts", attempt)
                else:
                    logger.info("WebSocket connected")
                return
            except Exception as e:
                if self._stopping:
                    return
                logger.warning(
                    "WebSocket connect attempt %d failed (%s). Retrying in %ds",
                    attempt, e, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def subscribe(self, market_tickers: list[str], chunk_size: int = 500):
        self._subscribed_tickers.update(market_tickers)
        if not self._ws:
            return
        for i in range(0, len(market_tickers), chunk_size):
            chunk = market_tickers[i:i + chunk_size]
            self._sub_id += 1
            msg = {
                "id": self._sub_id,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": chunk,
                },
            }
            await self._ws.send(json.dumps(msg))
        logger.info(f"Subscribed to orderbook_delta for {len(market_tickers)} markets")

    async def subscribe_fills(self):
        if not self._ws:
            return
        self._sub_id += 1
        msg = {
            "id": self._sub_id,
            "cmd": "subscribe",
            "params": {"channels": ["fill"]},
        }
        await self._ws.send(json.dumps(msg))
        self._fills_subscribed = True
        logger.info("Subscribed to fill channel")

    async def _reconnect(self):
        logger.info("Reconnecting to WebSocket...")
        self._ws = None
        await self.connect()
        if self._stopping:
            return
        if self._fills_subscribed:
            await self.subscribe_fills()
        if self._subscribed_tickers:
            await self.subscribe(list(self._subscribed_tickers))

    async def _fire_orderbook_update(self, ticker: str):
        if self.on_orderbook_update:
            result = self.on_orderbook_update(ticker)
            if inspect.isawaitable(result):
                await result

    async def listen(self):
        if not self._ws:
            return
        while self._running:
            try:
                raw = await self._ws.recv()
                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "orderbook_snapshot":
                    ticker = data["msg"]["market_ticker"]
                    self.orderbook_mgr.apply_snapshot(ticker, data["msg"])
                    await self._fire_orderbook_update(ticker)

                elif msg_type == "orderbook_delta":
                    ticker = data["msg"]["market_ticker"]
                    self.orderbook_mgr.apply_delta(ticker, data["msg"])
                    await self._fire_orderbook_update(ticker)

                elif msg_type == "fill":
                    if self.on_fill:
                        self.on_fill(data["msg"])

            except websockets.ConnectionClosed:
                logger.warning("WebSocket disconnected")
                await self._reconnect()

            except Exception:
                logger.exception("Error processing WebSocket message")

    async def close(self):
        self._stopping = True
        self._running = False
        if self._ws:
            await self._ws.close()
