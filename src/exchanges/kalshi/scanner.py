import asyncio
import inspect
import json
import logging
from typing import Callable

import websockets

from src.exchanges.kalshi.auth import KalshiAuth
from src.core.orderbook_manager import OrderbookManager

logger = logging.getLogger(__name__)


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
                self._ws = await websockets.connect(
                    self.ws_url, additional_headers=headers,
                    ping_interval=20, ping_timeout=60,
                )
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

    def stop(self):
        self._stopping = True
        self._running = False

    async def _reconnect(self):
        logger.info("Reconnecting to WebSocket...")
        self._ws = None
        try:
            await asyncio.wait_for(self.connect(), timeout=30)
        except asyncio.TimeoutError:
            logger.error("WebSocket reconnect timed out after 30s")
            raise
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
                    snapshot = data["msg"]
                    bids = {round(float(p) * 100): float(q) for p, q in snapshot.get("yes_dollars_fp", [])}
                    no_bids = {round(float(p) * 100): float(q) for p, q in snapshot.get("no_dollars_fp", [])}
                    asks = {100 - cents: qty for cents, qty in no_bids.items()}
                    self.orderbook_mgr.apply_snapshot(ticker, {"bids": bids, "asks": asks})
                    await self._fire_orderbook_update(ticker)

                elif msg_type == "orderbook_delta":
                    ticker = data["msg"]["market_ticker"]
                    delta_msg = data["msg"]
                    price_dollars = float(delta_msg["price_dollars"])
                    delta_qty = float(delta_msg["delta_fp"])
                    side = delta_msg["side"]
                    if side == "yes":
                        price_cents = round(price_dollars * 100)
                        core_side = "bid"
                    else:
                        price_cents = round((1.0 - price_dollars) * 100)
                        core_side = "ask"
                    self.orderbook_mgr.apply_delta(ticker, {
                        "price_cents": price_cents,
                        "delta_qty": delta_qty,
                        "side": core_side,
                    })
                    await self._fire_orderbook_update(ticker)

                elif msg_type == "fill":
                    if self.on_fill:
                        self.on_fill(data["msg"])

            except websockets.ConnectionClosed:
                logger.warning("WebSocket disconnected")
                try:
                    await self._reconnect()
                except Exception:
                    logger.exception("WebSocket reconnect failed — retrying in 5s")
                    await asyncio.sleep(5)

            except Exception:
                if self._ws is None:
                    logger.warning("WebSocket not connected — attempting reconnect")
                    try:
                        await self._reconnect()
                    except Exception:
                        logger.exception("WebSocket reconnect failed — retrying in 5s")
                        await asyncio.sleep(5)
                else:
                    logger.exception("Error processing WebSocket message")

    async def close(self):
        self._stopping = True
        self._running = False
        if self._ws:
            await self._ws.close()
