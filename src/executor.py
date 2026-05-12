import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.api import KalshiAPI
from src.models import TradeSignal
from src.positions import PositionTracker

logger = logging.getLogger(__name__)


@dataclass
class ArbExecution:
    signal: TradeSignal
    order_ids: list[str] = field(default_factory=list)
    filled: dict[str, float] = field(default_factory=dict)
    started_at: float = 0.0


class ExecutionManager:
    def __init__(self, api: KalshiAPI, positions: PositionTracker, fill_timeout_secs: int):
        self.api = api
        self.positions = positions
        self.fill_timeout_secs = fill_timeout_secs
        self._executing = False
        self._active: ArbExecution | None = None
        self._failed_events: set[str] = set()

    def is_event_blacklisted(self, event_ticker: str) -> bool:
        return event_ticker in self._failed_events

    def is_executing(self) -> bool:
        return self._executing

    def build_orders(self, signal: TradeSignal, quantity: int) -> list[dict]:
        return [
            self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=quantity)
            for ticker, price in signal.legs
        ]

    async def execute(self, signal: TradeSignal, quantity: int = 1):
        if self._executing:
            logger.warning("Already executing, skipping signal for %s", signal.event_ticker)
            return

        self._executing = True
        try:
            orders = self.build_orders(signal, quantity)
            logger.info(
                "Executing arb on %s: %d legs, profit=%.4f (%.2f%%)",
                signal.event_ticker, len(signal.legs), signal.net_profit, signal.profit_pct,
            )

            response = await self.api.batch_create_orders(orders)
            logger.info("Batch order response: %s", response)
            order_list = response.get("orders", [])
            execution = ArbExecution(
                signal=signal,
                order_ids=[o.get("order", o).get("order_id", "") for o in order_list],
                started_at=time.time(),
            )
            self._active = execution

            for o in order_list:
                inner = o.get("order", o)
                if inner.get("status") == "executed":
                    oid = inner.get("order_id", "")
                    price = float(inner.get("yes_price_dollars", 0))
                    qty = int(float(inner.get("fill_count_fp", 0)))
                    execution.filled[oid] = price
                    self.positions.record_fill(
                        ticker=inner.get("ticker", ""),
                        side=inner.get("side", "yes"),
                        price=price,
                        quantity=qty,
                        action=inner.get("action", "sell"),
                    )

            await self._monitor_fills(execution)
        except Exception:
            logger.exception("Failed to execute arb on %s", signal.event_ticker)
        finally:
            self._executing = False
            self._active = None

    async def _monitor_fills(self, execution: ArbExecution):
        deadline = execution.started_at + self.fill_timeout_secs
        while time.time() < deadline:
            if len(execution.filled) == len(execution.order_ids):
                logger.info("All legs filled for %s", execution.signal.event_ticker)
                return
            await asyncio.sleep(0.5)

        unfilled = [
            oid for oid in execution.order_ids if oid not in execution.filled
        ]
        if unfilled:
            filled_count = len(execution.filled)
            total_count = len(execution.order_ids)
            logger.warning(
                "Timeout: %d/%d legs filled for %s, cancelling %d unfilled",
                filled_count, total_count, execution.signal.event_ticker, len(unfilled),
            )
            await self.api.batch_cancel_orders(unfilled)
            if filled_count > 0:
                logger.error(
                    "PARTIAL FILL on %s: %d legs filled, %d cancelled — UNHEDGED EXPOSURE",
                    execution.signal.event_ticker, filled_count, len(unfilled),
                )
                self._failed_events.add(execution.signal.event_ticker)

    def handle_fill(self, fill_data: dict):
        order_id = fill_data.get("order_id", "")
        ticker = fill_data.get("ticker", "")
        price = float(fill_data.get("yes_price_cents", 0)) / 100.0
        quantity = int(fill_data.get("count", 0))
        action = fill_data.get("action", "sell")
        side = fill_data.get("side", "yes")

        self.positions.record_fill(
            ticker=ticker,
            side=side,
            price=price,
            quantity=quantity,
            action=action,
        )

        if self._active and order_id in self._active.order_ids:
            self._active.filled[order_id] = price
            logger.info("Leg filled: %s @ %.2f (%d/%d)",
                        ticker, price, len(self._active.filled), len(self._active.order_ids))
