import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.api import KalshiAPI
from src.models import TradeSignal
from src.positions import PositionTracker
from src.risk import RiskProfile

logger = logging.getLogger(__name__)


@dataclass
class ArbExecution:
    signal: TradeSignal
    order_ids: list[str] = field(default_factory=list)
    filled: dict[str, float] = field(default_factory=dict)
    started_at: float = 0.0
    batch_response: list[dict] = field(default_factory=list)


class ExecutionManager:
    def __init__(self, api: KalshiAPI, positions: PositionTracker,
                 fill_timeout_secs: int, risk_profile: RiskProfile | None = None):
        self.api = api
        self.positions = positions
        self.fill_timeout_secs = fill_timeout_secs
        self._executing = False
        self._active: ArbExecution | None = None
        self._failed_events: set[str] = set()
        self._unwind_phase1_secs = risk_profile.unwind_phase1_secs if risk_profile else 15
        self._unwind_phase2_secs = risk_profile.unwind_phase2_secs if risk_profile else 30
        self._unwind_price_step_cents = risk_profile.unwind_price_step_cents if risk_profile else 3
        self.session_realized_loss = 0.0
        self._circuit_breaker_tripped = False
        self._max_session_loss = 1.0
        self._circuit_breaker_on_any_loss = True

    def is_event_blacklisted(self, event_ticker: str) -> bool:
        return event_ticker in self._failed_events

    def is_circuit_breaker_tripped(self) -> bool:
        return self._circuit_breaker_tripped

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
                batch_response=order_list,
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
                await self._unwind_partial_fill(execution)

    def _record_unwind_loss(self, ticker: str, sell_price: float, buy_price: float, qty: int):
        loss = (buy_price - sell_price) * qty
        self.session_realized_loss += loss
        logger.error(
            "UNWIND LOSS: %s sold@%.2f bought@%.2f qty=%d loss=$%.4f (session total: $%.4f)",
            ticker, sell_price, buy_price, qty, loss, self.session_realized_loss,
        )
        if self._circuit_breaker_on_any_loss and loss > 0:
            logger.critical("CIRCUIT BREAKER: any-loss mode triggered by $%.4f loss on %s", loss, ticker)
            self._circuit_breaker_tripped = True
        elif self.session_realized_loss >= self._max_session_loss:
            logger.critical("CIRCUIT BREAKER: session loss $%.4f >= max $%.4f", self.session_realized_loss, self._max_session_loss)
            self._circuit_breaker_tripped = True

    def _parse_unwind_fill(self, resp: dict) -> tuple[str, float]:
        order_inner = resp.get("orders", [{}])[0].get("order", resp.get("orders", [{}])[0])
        status = order_inner.get("status", "")
        buy_price = float(order_inner.get("yes_price_dollars", 0))
        return status, buy_price

    async def _unwind_partial_fill(self, execution: ArbExecution):
        filled_tickers = []
        for o in execution.batch_response:
            inner = o.get("order", o)
            if inner.get("order_id") in execution.filled:
                filled_tickers.append((
                    inner.get("ticker", ""),
                    float(inner.get("yes_price_dollars", 0)),
                    int(float(inner.get("fill_count_fp", 0))),
                ))

        for ticker, fill_price, qty in filled_tickers:
            if qty <= 0:
                continue
            logger.warning("Unwinding %d contracts of %s (filled @ %.2f)", qty, ticker, fill_price)

            step = self._unwind_price_step_cents / 100.0

            # Phase 1: tight limit
            phase1_price = min(fill_price + step, 0.99)
            phase1_order = [{
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": round(phase1_price * 100), "count": qty,
            }]
            resp = await self.api.batch_create_orders(phase1_order)
            status, buy_price = self._parse_unwind_fill(resp)
            if status == "executed":
                self._record_unwind_loss(ticker, fill_price, buy_price, qty)
                logger.info("Unwind phase 1 filled for %s @ %.2f", ticker, phase1_price)
                continue
            phase1_oid = resp.get("orders", [{}])[0].get("order", {}).get("order_id", "")

            if self._unwind_phase1_secs > 0:
                await asyncio.sleep(self._unwind_phase1_secs)

            # Phase 2: wider limit
            if phase1_oid:
                await self.api.cancel_order(phase1_oid)
            phase2_price = min(fill_price + 2 * step, 0.99)
            phase2_order = [{
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": round(phase2_price * 100), "count": qty,
            }]
            resp = await self.api.batch_create_orders(phase2_order)
            status, buy_price = self._parse_unwind_fill(resp)
            if status == "executed":
                self._record_unwind_loss(ticker, fill_price, buy_price, qty)
                logger.info("Unwind phase 2 filled for %s @ %.2f", ticker, phase2_price)
                continue
            phase2_oid = resp.get("orders", [{}])[0].get("order", {}).get("order_id", "")

            wait = self._unwind_phase2_secs - self._unwind_phase1_secs
            if wait > 0:
                await asyncio.sleep(wait)

            # Phase 3: market order at max price
            if phase2_oid:
                await self.api.cancel_order(phase2_oid)
            phase3_order = [{
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 100, "count": qty,
            }]
            resp = await self.api.batch_create_orders(phase3_order)
            status, buy_price = self._parse_unwind_fill(resp)
            if status == "executed":
                self._record_unwind_loss(ticker, fill_price, buy_price, qty)
            elif status == "resting":
                self._record_unwind_loss(ticker, fill_price, 1.0, qty)
                logger.error("Unwind phase 3 STILL RESTING for %s — loss estimated", ticker)
            logger.warning("Unwind phase 3 for %s: %s @ $1.00", ticker, status)

    def handle_fill(self, fill_data: dict):
        logger.info("WS fill event: %s", fill_data)
        order_id = fill_data.get("order_id", "")
        ticker = fill_data.get("market_ticker", "")
        price = float(fill_data.get("yes_price_dollars", 0))
        quantity = int(float(fill_data.get("count_fp", 0)))
        action = fill_data.get("action", "sell")
        side = fill_data.get("outcome_side", fill_data.get("side", "yes"))

        if not ticker or quantity <= 0:
            logger.warning("Ignoring invalid fill: ticker=%r qty=%d data=%s", ticker, quantity, fill_data)
            return

        if self._active and order_id in self._active.filled:
            logger.debug("Skipping duplicate fill for %s (already tracked from batch response)", order_id)
        else:
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
