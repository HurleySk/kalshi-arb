import asyncio
import logging
from typing import Callable

from src.core.models import Orderbook
from src.core.orderbook_manager import OrderbookManager
from src.exchanges.predictit.anti_detect import random_delay

logger = logging.getLogger(__name__)


class PredictItScanner:
    def __init__(
        self,
        scraper,
        orderbook_mgr: OrderbookManager,
        on_orderbook_update: Callable[[str], None] | None = None,
        on_fill: Callable[[dict], None] | None = None,
        poll_interval_secs: int = 60,
    ):
        self.scraper = scraper
        self.orderbook_mgr = orderbook_mgr
        self.on_orderbook_update = on_orderbook_update
        self.on_fill = on_fill
        self.poll_interval_secs = poll_interval_secs
        self._subscribed_tickers: set[str] = set()
        self._stopping = False
        self._running = False
        self._previous_data: dict[str, dict] = {}

    async def connect(self) -> None:
        self._running = True
        logger.info("PredictIt scanner connected (polling mode)")

    async def subscribe(self, market_tickers: list[str]) -> None:
        self._subscribed_tickers.update(market_tickers)
        logger.info("PredictIt scanner: subscribed to %d markets", len(market_tickers))

    async def subscribe_fills(self) -> None:
        logger.info("PredictIt scanner: fill subscription (browser-based, handled by API layer)")

    def _build_orderbook(self, contract: dict) -> Orderbook:
        bids: dict[int, float] = {}
        asks: dict[int, float] = {}
        best_sell_yes = contract.get("bestSellYesCost")
        if best_sell_yes is not None:
            bids[round(best_sell_yes * 100)] = 1.0
        best_buy_yes = contract.get("bestBuyYesCost")
        if best_buy_yes is not None:
            asks[round(best_buy_yes * 100)] = 1.0
        return Orderbook(bids=bids, asks=asks)

    def _detect_changes(
        self, old: dict[str, dict], new: dict[str, dict]
    ) -> set[str]:
        changed = set()
        for ticker in self._subscribed_tickers:
            old_contract = old.get(ticker)
            new_contract = new.get(ticker)
            if new_contract is None:
                continue
            if old_contract is None or old_contract != new_contract:
                changed.add(ticker)
        return changed

    def _build_contract_map(self, data: dict) -> dict[str, dict]:
        result = {}
        for market in data.get("markets", []):
            market_id = market["id"]
            for contract in market.get("contracts", []):
                ticker = f"PI-{market_id}-{contract['id']}"
                if ticker in self._subscribed_tickers:
                    result[ticker] = contract
        return result

    async def listen(self) -> None:
        while not self._stopping:
            try:
                data = await self.scraper.fetch()
                current = self._build_contract_map(data)
                changed = self._detect_changes(self._previous_data, current)
                self._previous_data = current

                for ticker in changed:
                    contract = current[ticker]
                    book = self._build_orderbook(contract)
                    self.orderbook_mgr.apply_snapshot(ticker, {
                        "bids": book.bids,
                        "asks": book.asks,
                    })
                    if self.on_orderbook_update:
                        self.on_orderbook_update(ticker)

                if changed:
                    logger.debug("PredictIt poll: %d markets updated", len(changed))

            except Exception:
                logger.exception("PredictIt scanner poll failed")

            delay = random_delay(
                min_secs=self.poll_interval_secs * 0.9,
                max_secs=self.poll_interval_secs * 1.1,
            )
            await asyncio.sleep(delay)

    async def close(self) -> None:
        self._running = False
        logger.info("PredictIt scanner closed")

    def stop(self) -> None:
        self._stopping = True
