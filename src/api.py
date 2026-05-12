import logging
from typing import Any

import aiohttp

from src.auth import KalshiAuth
from src.models import Event, Market

logger = logging.getLogger(__name__)


class KalshiAPI:
    def __init__(self, base_url: str, auth: KalshiAuth):
        self.base_url = base_url
        from urllib.parse import urlparse
        self._sign_path_prefix = urlparse(base_url).path
        self.auth = auth
        self._session: aiohttp.ClientSession | None = None

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

    async def _get(self, path: str, params: dict | None = None) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        sign_path = f"{self._sign_path_prefix}{path}"
        headers = self._headers("GET", sign_path)
        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, body: dict) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        sign_path = f"{self._sign_path_prefix}{path}"
        headers = self._headers("POST", sign_path)
        async with session.post(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _delete(self, path: str, body: dict | None = None) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        sign_path = f"{self._sign_path_prefix}{path}"
        headers = self._headers("DELETE", sign_path)
        async with session.delete(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            return await resp.json()

    def parse_events(self, raw: dict) -> list[Event]:
        events = []
        for e in raw.get("events", []):
            if not e.get("mutually_exclusive", False):
                continue
            markets = [
                Market(
                    ticker=m["ticker"],
                    event_ticker=m["event_ticker"],
                    title=m["title"],
                    status=m["status"],
                )
                for m in e.get("markets", [])
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
                )
            )
        return events

    async def fetch_events(self) -> list[Event]:
        all_events: list[Event] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"with_nested_markets": "true", "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            raw = await self._get("/events", params=params)
            all_events.extend(self.parse_events(raw))
            cursor = raw.get("cursor", "")
            if not cursor:
                break
        return all_events

    async def get_orderbook(self, ticker: str) -> dict:
        return await self._get(f"/markets/{ticker}/orderbook")

    def build_sell_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price_cents": round(yes_price * 100),
            "count": quantity,
        }

    async def batch_create_orders(self, orders: list[dict]) -> dict:
        return await self._post("/portfolio/orders/batched", {"orders": orders})

    async def cancel_order(self, order_id: str) -> dict:
        return await self._delete(f"/portfolio/orders/{order_id}")

    async def batch_cancel_orders(self, order_ids: list[str]) -> dict:
        return await self._delete("/portfolio/orders/batched", {"ids": order_ids})

    async def get_positions(self) -> dict:
        return await self._get("/portfolio/positions")

    async def get_open_orders(self) -> dict:
        return await self._get("/portfolio/orders")

    async def get_balance(self) -> dict:
        return await self._get("/portfolio/balance")
