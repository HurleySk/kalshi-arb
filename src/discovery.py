import asyncio
import logging
from datetime import datetime, timezone

from src.models import Event
from src.scanner import OrderbookManager

logger = logging.getLogger(__name__)


class EventDiscovery:
    def __init__(self, api, orderbook_mgr: OrderbookManager, scanner):
        self.api = api
        self.orderbook_mgr = orderbook_mgr
        self.scanner = scanner
        self.event_tickers: set[str] = set()
        self.market_metadata: dict[str, dict] = {}

    def register_events(self, events: list[Event]) -> list[str]:
        new_tickers = []
        for event in events:
            if event.event_ticker not in self.event_tickers:
                self.event_tickers.add(event.event_ticker)
                market_tickers = event.market_tickers()
                self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                new_tickers.extend(market_tickers)
            for m in event.markets:
                self.market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.expected_expiration_time,
                    "volume_24h": m.volume_24h,
                }
        return new_tickers

    def cleanup_expired(self) -> set[str]:
        now = datetime.now(timezone.utc)
        expired: set[str] = set()

        for event_ticker in list(self.event_tickers):
            market_tickers = self.orderbook_mgr._event_markets.get(event_ticker, [])
            if not market_tickers:
                self.event_tickers.discard(event_ticker)
                continue
            all_expired = True
            for mt in market_tickers:
                meta = self.market_metadata.get(mt, {})
                close_str = meta.get("close_time", "")
                if not close_str:
                    all_expired = False
                    break
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    if close_dt > now:
                        all_expired = False
                        break
                except (ValueError, TypeError):
                    all_expired = False
                    break
            if all_expired:
                expired.add(event_ticker)

        for event_ticker in expired:
            market_tickers = self.orderbook_mgr._event_markets.get(event_ticker, [])
            for mt in market_tickers:
                self.market_metadata.pop(mt, None)
            self.orderbook_mgr.unregister_event(event_ticker)
            self.event_tickers.discard(event_ticker)
            logger.info("Cleaned up expired event: %s (%d markets)", event_ticker, len(market_tickers))

        return expired

    async def full_scan(self):
        logger.info("Starting full event scan...")
        cursor = ""
        pages = 0
        retries = 0
        max_retries = 3
        all_events = []
        while True:
            try:
                events, next_cursor = await self.api.fetch_events_page(cursor)
                all_events.extend(events)
                pages += 1
                retries = 0
                if pages % 10 == 0:
                    logger.info("Scanning page %d... (%d events collected)", pages, len(all_events))
                if not next_cursor:
                    break
                cursor = next_cursor
                await asyncio.sleep(0.5)
            except Exception:
                retries += 1
                if retries >= max_retries:
                    logger.error("Full scan aborted after %d retries at page %d", max_retries, pages)
                    break
                logger.exception("Error during full scan (retry %d/%d)", retries, max_retries)
                await asyncio.sleep(5)

        def _earliest_close(event):
            times = [m.close_time for m in event.markets if m.close_time]
            return min(times) if times else "9999"

        all_events.sort(key=_earliest_close)
        all_new = []
        for event in all_events:
            all_new.extend(self.register_events([event]))
        if all_new:
            await self.scanner.subscribe(all_new)

        logger.info("Full scan complete: %d pages, %d events, %d new markets",
                    pages, len(self.event_tickers), len(all_new))

    async def poll_loop(self, interval_secs: int):
        await self.full_scan()
        while True:
            await asyncio.sleep(interval_secs)
            try:
                events, _ = await self.api.fetch_events_page("")
                new_tickers = self.register_events(events)
                if new_tickers:
                    await self.scanner.subscribe(new_tickers)
                    logger.info("Re-poll: %d new markets found", len(new_tickers))
            except Exception:
                logger.exception("Error during event re-poll")

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(300)
            self.cleanup_expired()
