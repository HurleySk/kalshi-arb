import time

from src.core.models import Orderbook


class OrderbookManager:
    def __init__(self):
        self._books: dict[str, Orderbook] = {}
        self._event_markets: dict[str, list[str]] = {}
        self._market_to_event: dict[str, str] = {}
        self._last_update_ts: dict[str, float] = {}

    def register_event(self, event_ticker: str, market_tickers: list[str]):
        self._event_markets[event_ticker] = market_tickers
        for t in market_tickers:
            self._market_to_event[t] = event_ticker

    def unregister_event(self, event_ticker: str):
        tickers = self._event_markets.pop(event_ticker, [])
        for t in tickers:
            self._market_to_event.pop(t, None)
            self._books.pop(t, None)
            self._last_update_ts.pop(t, None)

    def get_event_for_market(self, market_ticker: str) -> str | None:
        return self._market_to_event.get(market_ticker)

    def apply_snapshot(self, ticker: str, snapshot: dict):
        self._books[ticker] = Orderbook(
            bids=dict(snapshot.get("bids", {})),
            asks=dict(snapshot.get("asks", {})),
        )
        self._last_update_ts[ticker] = time.time()

    def apply_delta(self, ticker: str, delta: dict):
        book = self._books.get(ticker)
        if book is None:
            return
        price_cents = delta["price_cents"]
        delta_qty = delta["delta_qty"]
        side = delta["side"]
        levels = book.bids if side == "bid" else book.asks

        new_qty = levels.get(price_cents, 0) + delta_qty
        if new_qty <= 0:
            levels.pop(price_cents, None)
        else:
            levels[price_cents] = new_qty
        self._last_update_ts[ticker] = time.time()

    def market_age(self, ticker: str) -> float:
        ts = self._last_update_ts.get(ticker)
        if ts is None:
            return float("inf")
        return time.time() - ts

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
