import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.api import KalshiAPI
from src.models import TradeSignal, OrderStatus
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
            order_list = response.get("orders", [])
            execution = ArbExecution(
                signal=signal,
                order_ids=[o["order_id"] for o in order_list],
                started_at=time.time(),
            )
            self._active = execution

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
            logger.warning(
                "Timeout: %d unfilled legs for %s, cancelling",
                len(unfilled), execution.signal.event_ticker,
            )
            await self.api.batch_cancel_orders(unfilled)

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
