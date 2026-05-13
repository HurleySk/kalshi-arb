import asyncio
import logging
import time
from typing import Any

import aiohttp

from src.auth import KalshiAuth
from src.models import Event, Market

logger = logging.getLogger(__name__)

# Basic tier: 200 read tokens/sec, 100 write tokens/sec, 10 tokens per request
# Stay well under: ~10 reads/sec, ~5 writes/sec
MIN_REQUEST_INTERVAL = 0.1
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class KalshiAPI:
    def __init__(self, base_url: str, auth: KalshiAuth):
        self.base_url = base_url
        from urllib.parse import urlparse
        self._sign_path_prefix = urlparse(base_url).path
        self.auth = auth
        self._session: aiohttp.ClientSession | None = None
        self._last_request_time = 0.0

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, method: str, path: str) -> dict[str, str]:
        return {
            **self.auth.build_headers(method, path),
            "Content-Type": "application/json",
        }

    async def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    async def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        sign_path = f"{self._sign_path_prefix}{path}"
        headers = self._headers(method, sign_path)
        last_retryable_status = 503

        for attempt in range(3):
            await self._throttle()
            kwargs: dict[str, Any] = {"headers": headers}
            if params:
                kwargs["params"] = params
            if body is not None:
                kwargs["json"] = body

            try:
                async with session.request(method, url, **kwargs) as resp:
                    if resp.status in RETRYABLE_STATUSES:
                        last_retryable_status = resp.status
                        if attempt == 2:
                            break
                        wait = 2 ** attempt + 1
                        logger.warning("Retryable error %d on %s %s, backing off %ds (attempt %d/3)",
                                       resp.status, method, path, wait, attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status >= 400:
                        error_body = await resp.text()
                        logger.error("API error %d %s %s: %s", resp.status, method, path, error_body)
                        resp.raise_for_status()
                    return await resp.json()
            except aiohttp.ClientConnectionError:
                if attempt == 2:
                    raise
                wait = 2 ** attempt + 1
                logger.warning("Connection error on %s %s, backing off %ds (attempt %d/3)",
                               method, path, wait, attempt + 1)
                await asyncio.sleep(wait)

        raise aiohttp.ClientResponseError(
            request_info=None, history=(), status=last_retryable_status,
            message=f"Server error {last_retryable_status} after 3 retries",
        )

    async def _get(self, path: str, params: dict | None = None) -> dict:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, body: dict) -> dict:
        return await self._request("POST", path, body=body)

    async def _delete(self, path: str, body: dict | None = None) -> dict:
        return await self._request("DELETE", path, body=body)

    def parse_events(self, raw: dict) -> list[Event]:
        events = []
        for e in raw.get("events", []):
            if not e.get("mutually_exclusive", False):
                continue
            raw_markets = e.get("markets", [])
            markets = [
                Market(
                    ticker=m["ticker"],
                    event_ticker=m["event_ticker"],
                    title=m["title"],
                    status=m["status"],
                    close_time=m.get("close_time", ""),
                    expected_expiration_time=m.get("expected_expiration_time", ""),
                    volume_24h=float(m.get("volume_24h_fp", 0)),
                    open_interest=float(m.get("open_interest_fp", 0)),
                    liquidity=float(m.get("liquidity_dollars", 0)),
                )
                for m in raw_markets
                if m.get("status") == "active"
            ]
            if len(markets) < 2:
                continue
            events.append(
                Event(
                    event_ticker=e["event_ticker"],
                    title=e["title"],
                    series_ticker=e.get("series_ticker", ""),
                    mutually_exclusive=True,
                    markets=markets,
                    total_market_count=len(raw_markets),
                )
            )
        return events

    async def fetch_events_page(self, cursor: str = "") -> tuple[list[Event], str]:
        """Fetch one page of events. Returns (events, next_cursor)."""
        params: dict[str, Any] = {"with_nested_markets": "true", "limit": "100", "status": "open"}
        if cursor:
            params["cursor"] = cursor
        raw = await self._get("/events", params=params)
        return self.parse_events(raw), raw.get("cursor", "")

    async def get_orderbook(self, ticker: str) -> dict:
        return await self._get(f"/markets/{ticker}/orderbook")

    def build_sell_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price": round(yes_price * 100),
            "count": quantity,
        }

    def build_buy_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "buy",
            "side": "yes",
            "type": "limit",
            "yes_price": round(yes_price * 100),
            "count": quantity,
        }

    def build_close_order(self, ticker: str, qty: int) -> dict:
        # Kalshi API valid range: yes_price 1-99 (cents). 100 returns invalid_price error.
        if qty < 0:
            return {
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 99, "count": abs(qty),
            }
        return {
            "ticker": ticker, "action": "sell", "side": "yes",
            "type": "limit", "yes_price": 1, "count": qty,
        }

    @staticmethod
    def unwrap_order(raw: dict) -> dict:
        return raw.get("order", raw)

    async def batch_create_orders(self, orders: list[dict]) -> dict:
        return await self._post("/portfolio/orders/batched", {"orders": orders})

    async def cancel_order(self, order_id: str) -> dict:
        return await self._delete(f"/portfolio/orders/{order_id}")

    async def batch_cancel_orders(self, order_ids: list[str]) -> dict:
        # API enforces a per-request limit; chunk to avoid "too_many_orders_in_batch" 400s
        _CHUNK = 20
        results: dict = {}
        for i in range(0, len(order_ids), _CHUNK):
            resp = await self._delete("/portfolio/orders/batched", {"ids": order_ids[i:i + _CHUNK]})
            results.update(resp or {})
        return results

    async def get_positions(self) -> dict:
        return await self._get("/portfolio/positions")

    async def get_open_orders(self) -> dict:
        return await self._get("/portfolio/orders")

    async def get_balance(self) -> dict:
        return await self._get("/portfolio/balance")

    async def get_market_trades(self, ticker: str, limit: int = 10) -> dict:
        return await self._get("/markets/trades", params={"ticker": ticker, "limit": str(limit)})
