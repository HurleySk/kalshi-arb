import logging
import time

import httpx

from src.exchanges.predictit.anti_detect import get_headers, random_delay

logger = logging.getLogger(__name__)

PREDICTIT_API_URL = "https://www.predictit.org/api/marketdata/all/"


class PredictItScraper:
    def __init__(self, proxy_url: str | None):
        self.proxy_url = proxy_url
        self._last_fetch_time: float = 0

    def fetch(self) -> dict:
        transport = None
        if self.proxy_url:
            transport = httpx.HTTPTransport(proxy=self.proxy_url)

        with httpx.Client(transport=transport, timeout=30.0, follow_redirects=True) as client:
            response = client.get(PREDICTIT_API_URL, headers=get_headers())
            response.raise_for_status()
            self._last_fetch_time = time.time()
            return response.json()

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
