import logging

import httpx

from src.exchanges.predictit.anti_detect import get_headers

logger = logging.getLogger(__name__)

PREDICTIT_API_URL = "https://www.predictit.org/api/marketdata/all/"


class PredictItScraper:
    def __init__(self, proxy_url: str | None):
        self.proxy_url = proxy_url
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            transport = None
            if self.proxy_url:
                transport = httpx.AsyncHTTPTransport(proxy=self.proxy_url)
            self._client = httpx.AsyncClient(
                transport=transport, timeout=30.0, follow_redirects=True,
            )
        return self._client

    async def fetch(self) -> dict:
        client = self._get_client()
        response = await client.get(PREDICTIT_API_URL, headers=get_headers())
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def parse_markets(self, data: dict) -> list[dict]:
        results = []
        for market in data.get("markets", []):
            if market.get("status") != "Open":
                continue
            contracts = market.get("contracts", [])
            if len(contracts) < 2:
                continue
            results.append(market)
        return results
