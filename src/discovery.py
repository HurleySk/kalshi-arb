import asyncio
import logging
import re
from datetime import datetime, timezone

from src.models import Event
from src.scanner import OrderbookManager

logger = logging.getLogger(__name__)

_THRESHOLD_PATTERN = re.compile(
    r'\b(above|exceed|over|reach|below|under)\s+([\d,]+(?:\.\d+)?)\b',
    re.IGNORECASE,
)


class MonotoneFamilyRegistry:
    def __init__(self):
        # template_key → list of {event_ticker, market_ticker, threshold, direction}
        self._families: dict[str, list[dict]] = {}

    def try_register(self, event_ticker: str, market_ticker: str, title: str) -> str | None:
        m = _THRESHOLD_PATTERN.search(title)
        if not m:
            return None
        direction = m.group(1).lower()
        threshold = float(m.group(2).replace(",", ""))
        template = title[:m.start(2)] + "*" + title[m.end(2):]
        key = template.lower()
        if key not in self._families:
            self._families[key] = []
        self._families[key].append({
            "event_ticker": event_ticker,
            "market_ticker": market_ticker,
            "threshold": threshold,
            "direction": direction,
        })
        self._families[key].sort(key=lambda x: x["threshold"])
        return key

    def get_families(self) -> dict[str, list[dict]]:
        return {k: v for k, v in self._families.items() if len(v) >= 2}

    def unregister_event(self, event_ticker: str):
        for key in list(self._families.keys()):
            self._families[key] = [m for m in self._families[key] if m["event_ticker"] != event_ticker]
            if len(self._families[key]) == 0:
                del self._families[key]


class EventDiscovery:
    def __init__(self, api, orderbook_mgr: OrderbookManager, scanner):
        self.api = api
        self.orderbook_mgr = orderbook_mgr
        self.scanner = scanner
        self.event_tickers: set[str] = set()
        self.market_metadata: dict[str, dict] = {}
        self.monotone_registry = MonotoneFamilyRegistry()

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
                    "open_interest": m.open_interest,
                    "liquidity": m.liquidity,
                }
                self.monotone_registry.try_register(event.event_ticker, m.ticker, event.title)
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
            self.monotone_registry.unregister_event(event_ticker)
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
