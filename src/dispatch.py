import json
import logging
import time

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
    ):
        self.engine = engine
        self.executor = executor
        self.maker = maker
        self.orderbook_mgr = orderbook_mgr
        self.market_metadata = market_metadata
        self._signal_cooldown = signal_cooldown
        self._enable_buy_side_arb = enable_buy_side_arb
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
            buy_signal = self.engine.evaluate_buy_side(event_ticker, event_books, market_metadata=meta)
            if buy_signal and not self.executor.is_executing():
                if not self.executor.is_event_blacklisted(event_ticker):
                    key = event_ticker + ":buy"
                    last = self._last_signal_time.get(key, 0)
                    if time.time() - last >= self._signal_cooldown:
                        self._last_signal_time[key] = time.time()
                        self._pending_execution.add(key)
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

        if self.maker and not signal:
            self._maker_dirty_events.add(event_ticker)

        return None

    def mark_execution_complete(self, event_ticker: str):
        self._pending_execution.discard(event_ticker)
        self._pending_execution.discard(event_ticker + ":buy")

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
