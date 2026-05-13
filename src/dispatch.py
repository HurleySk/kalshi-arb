import json
import logging
import time
from datetime import datetime, timezone, timedelta

from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.models import TradeSignal
from src.scanner import OrderbookManager

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        engine: ArbEngine,
        executor: ExecutionManager,
        maker,
        orderbook_mgr: OrderbookManager,
        market_metadata: dict[str, dict],
        signal_cooldown: float = 60.0,
        enable_buy_side_arb: bool = True,
        near_expiry_window_minutes: int = 0,
        monotone_registry=None,
        event_total_markets: dict[str, int] | None = None,
    ):
        self.engine = engine
        self.executor = executor
        self.maker = maker
        self.orderbook_mgr = orderbook_mgr
        self.market_metadata = market_metadata
        self._signal_cooldown = signal_cooldown
        self._enable_buy_side_arb = enable_buy_side_arb
        self._near_expiry_window_minutes = near_expiry_window_minutes
        self._monotone_registry = monotone_registry
        self._event_total_markets: dict[str, int] = event_total_markets or {}
        self._last_signal_time: dict[str, float] = {}
        self._pending_execution: set[str] = set()
        self._maker_dirty_events: set[str] = set()

    def process_orderbook_update(self, market_ticker: str) -> TradeSignal | None:
        """Evaluate one orderbook update. Returns a TradeSignal to execute, or None."""
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            return None

        if self.executor.is_circuit_breaker_tripped():
            return None

        if event_ticker in self._pending_execution:
            return None

        event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
        meta = {t: self.market_metadata.get(t, {}) for t in event_books}

        signal = self.engine.evaluate(event_ticker, event_books, market_metadata=meta)

        if signal and not self.executor.is_executing():
            if self.executor.is_event_blacklisted(event_ticker):
                return None
            last = self._last_signal_time.get(event_ticker, 0)
            if time.time() - last < self._signal_cooldown:
                return None
            self._last_signal_time[event_ticker] = time.time()
            self._pending_execution.add(event_ticker)
            logger.info(
                json.dumps({
                    "event": "arb_detected",
                    "event_ticker": event_ticker,
                    "legs": signal.legs,
                    "net_profit": round(signal.net_profit, 6),
                    "profit_pct": round(signal.profit_pct, 2),
                    "exposure_ratio": round(signal.exposure_ratio, 2),
                })
            )
            return signal

        if not signal and self._enable_buy_side_arb:
            api_total = self._event_total_markets.get(event_ticker)
            registered = self.orderbook_mgr.get_registered_market_count(event_ticker)
            buy_signal = self.engine.evaluate_buy_side(
                event_ticker, event_books, market_metadata=meta,
                expected_market_count=api_total if api_total else registered,
            )
            if buy_signal and not self.executor.is_executing():
                if not self.executor.is_event_blacklisted(event_ticker):
                    key = event_ticker + ":buy"
                    last = self._last_signal_time.get(key, 0)
                    if time.time() - last >= self._signal_cooldown:
                        self._last_signal_time[key] = time.time()
                        self._pending_execution.add(key)
                        self._pending_execution.add(event_ticker)  # block all variants
                        logger.info(
                            json.dumps({
                                "event": "buy_side_arb_detected",
                                "event_ticker": event_ticker,
                                "legs": buy_signal.legs,
                                "net_profit": round(buy_signal.net_profit, 6),
                                "profit_pct": round(buy_signal.profit_pct, 2),
                            })
                        )
                        return buy_signal

        if not signal and self._is_near_expiry(event_ticker):
            ne_signal = self.engine.evaluate_near_expiry(event_ticker, event_books, market_metadata=meta)
            if ne_signal and not self.executor.is_executing():
                if not self.executor.is_event_blacklisted(event_ticker):
                    key = event_ticker + ":ne"
                    last = self._last_signal_time.get(key, 0)
                    if time.time() - last >= self._signal_cooldown:
                        self._last_signal_time[key] = time.time()
                        self._pending_execution.add(key)
                        self._pending_execution.add(event_ticker)  # block all variants
                        logger.info(json.dumps({
                            "event": "near_expiry_arb_detected",
                            "event_ticker": event_ticker,
                            "legs": ne_signal.legs,
                            "net_profit": round(ne_signal.net_profit, 6),
                            "profit_pct": round(ne_signal.profit_pct, 2),
                        }))
                        return ne_signal

        if not signal and self._monotone_registry and not self.executor.is_executing():
            for family in self._monotone_registry.get_families().values():
                for i in range(len(family) - 1):
                    lower = family[i]
                    upper = family[i + 1]
                    # Only evaluate "above/exceed/over/reach" families — "below/under" semantics
                    # invert the P(above threshold) monotone constraint and require separate handling.
                    if lower.get("direction") not in ("above", "exceed", "over", "reach"):
                        continue
                    lower_book = self.orderbook_mgr.get_orderbook(lower["market_ticker"])
                    upper_book = self.orderbook_mgr.get_orderbook(upper["market_ticker"])
                    if lower_book is None or upper_book is None:
                        continue
                    mono_signal = self.engine.evaluate_monotone_pair(
                        upper["market_ticker"], upper_book,
                        lower["market_ticker"], lower_book,
                    )
                    if mono_signal:
                        if self.executor.is_event_blacklisted(mono_signal.event_ticker):
                            continue
                        key = mono_signal.event_ticker + ":mono"
                        last = self._last_signal_time.get(key, 0)
                        if time.time() - last >= self._signal_cooldown:
                            self._last_signal_time[key] = time.time()
                            self._pending_execution.add(key)
                            logger.info(json.dumps({
                                "event": "monotone_arb_detected",
                                "pair": mono_signal.event_ticker,
                                "net_profit": round(mono_signal.net_profit, 6),
                            }))
                            return mono_signal

        if self.maker and not signal:
            self._maker_dirty_events.add(event_ticker)

        return None

    def _is_near_expiry(self, event_ticker: str) -> bool:
        if self._near_expiry_window_minutes <= 0:
            return False
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=self._near_expiry_window_minutes)
        for mt in self.orderbook_mgr._event_markets.get(event_ticker, []):
            close_str = self.market_metadata.get(mt, {}).get("close_time", "")
            if close_str:
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    if now < close_dt <= cutoff:
                        return True
                except (ValueError, TypeError):
                    pass
        return False

    def mark_execution_complete(self, event_ticker: str):
        self._pending_execution.discard(event_ticker)
        self._pending_execution.discard(event_ticker + ":buy")
        self._pending_execution.discard(event_ticker + ":ne")
        self._pending_execution.discard(event_ticker + ":mono")

    def consume_dirty_events(self) -> list[str]:
        dirty = list(self._maker_dirty_events)
        self._maker_dirty_events.clear()
        return dirty

    def route_fill(self, fill_data: dict) -> str:
        order_id = fill_data.get("order_id", "")
        if self.maker and self.maker.owns_order(order_id):
            return "maker"
        self.executor.handle_fill(fill_data)
        return "executor"
