import asyncio
import logging
from datetime import datetime, timezone

from src.core.models import Event, Market
from src.core.orderbook_manager import OrderbookManager

logger = logging.getLogger(__name__)


class _NoOpMonotoneRegistry:
    """Stub — PredictIt doesn't have threshold-based markets."""
    def get_families(self) -> dict:
        return {}

    def try_register(self, event_ticker: str, market_ticker: str, title: str) -> str | None:
        return None

    def unregister_event(self, event_ticker: str) -> None:
        pass


class PredictItDiscovery:
    def __init__(self, scraper, orderbook_mgr: OrderbookManager, scanner):
        self.scraper = scraper
        self.orderbook_mgr = orderbook_mgr
        self.scanner = scanner
        self.monotone_registry = _NoOpMonotoneRegistry()
        self.event_tickers: set[str] = set()
        self.market_metadata: dict[str, dict] = {}
        self.event_total_markets: dict[str, int] = {}

    def _convert_to_events(self, parsed_markets: list[dict]) -> list[Event]:
        events = []
        for market in parsed_markets:
            market_id = market["id"]
            event_ticker = f"PI-{market_id}"
            contracts = market.get("contracts", [])
            markets = []
            for contract in contracts:
                if contract.get("status") != "Open":
                    continue
                contract_id = contract["id"]
                ticker = f"PI-{market_id}-{contract_id}"
                markets.append(Market(
                    ticker=ticker,
                    event_ticker=event_ticker,
                    title=contract.get("name", ""),
                    status=contract.get("status", "Open"),
                    close_time=contract.get("dateEnd", ""),
                    exchange="predictit",
                    volume_24h=0.0,
                    open_interest=0.0,
                    liquidity=0.0,
                ))
            if len(markets) < 2:
                continue
            events.append(Event(
                event_ticker=event_ticker,
                title=market.get("name", ""),
                series_ticker=f"PI-SERIES-{market_id}",
                mutually_exclusive=True,
                markets=markets,
                total_market_count=len(markets),
                exchange="predictit",
            ))
        return events

    def register_events(self, events: list[Event]) -> list[str]:
        new_tickers = []
        for event in events:
            if event.event_ticker not in self.event_tickers:
                self.event_tickers.add(event.event_ticker)
                market_tickers = event.market_tickers()
                self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                new_tickers.extend(market_tickers)
            total = event.total_market_count or len(event.markets)
            self.event_total_markets[event.event_ticker] = total
            for m in event.markets:
                self.market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.close_time,
                    "volume_24h": m.volume_24h,
                    "open_interest": m.open_interest,
                    "liquidity": m.liquidity,
                }
        return new_tickers

    def cleanup_expired(self) -> set[str]:
        now = datetime.now(timezone.utc)
        removed = set()
        for event_ticker in list(self.event_tickers):
            market_tickers = self.orderbook_mgr.get_event_markets(event_ticker)
            all_expired = True
            for mt in market_tickers:
                meta = self.market_metadata.get(mt, {})
                close_str = meta.get("close_time", "")
                if not close_str:
                    all_expired = False
                    break
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    if close_dt.tzinfo is None:
                        close_dt = close_dt.replace(tzinfo=timezone.utc)
                    if close_dt > now:
                        all_expired = False
                        break
                except (ValueError, TypeError):
                    all_expired = False
                    break
            if all_expired:
                self.event_tickers.discard(event_ticker)
                self.orderbook_mgr.unregister_event(event_ticker)
                for mt in market_tickers:
                    self.market_metadata.pop(mt, None)
                self.event_total_markets.pop(event_ticker, None)
                removed.add(event_ticker)
                logger.info("Removed expired event: %s", event_ticker)
        return removed

    async def full_scan(self) -> None:
        try:
            data = await self.scraper.fetch()
            parsed = self.scraper.parse_markets(data)
            events = self._convert_to_events(parsed)
            new_tickers = self.register_events(events)
            if new_tickers:
                await self.scanner.subscribe(new_tickers)
            logger.info(
                "PredictIt full scan: %d events, %d new markets",
                len(events), len(new_tickers),
            )
        except Exception:
            logger.exception("PredictIt full scan failed")

    async def poll_loop(self, interval_secs: int) -> None:
        await self.full_scan()
        while True:
            await asyncio.sleep(interval_secs)
            await self.full_scan()

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            removed = self.cleanup_expired()
            if removed:
                logger.info("Cleaned up %d expired PredictIt events", len(removed))
