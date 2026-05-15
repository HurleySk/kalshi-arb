import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from src.core.models import TradeSignal
from src.core.positions import PositionTracker
from src.core.risk import RiskProfile

logger = logging.getLogger(__name__)


@dataclass
class FilledLeg:
    ticker: str
    fill_price: float
    quantity: int
    unwind_action: str


@dataclass
class ArbExecution:
    signal: TradeSignal
    order_ids: list[str] = field(default_factory=list)
    filled: dict[str, float] = field(default_factory=dict)
    filled_legs: list[FilledLeg] = field(default_factory=list)
    started_at: float = 0.0
    _needs_unwind: bool = False


@dataclass
class TimeoutConfig:
    batch_create: float = 15
    batch_cancel: float = 10
    balance: float = 10
    monitor_poll: float = 0.5


class ExecutionManager:
    def __init__(self, api, order_builder=None, positions: PositionTracker | None = None,
                 fill_timeout_secs: int = 0, risk_profile: RiskProfile | None = None,
                 max_session_loss: float = 1.0, circuit_breaker_on_any_loss: bool = True,
                 recorder=None, timeouts: TimeoutConfig | None = None):
        self.api = api
        self.order_builder = order_builder if order_builder is not None else api
        self.positions = positions
        self.fill_timeout_secs = fill_timeout_secs
        self.recorder = recorder
        self._timeouts = timeouts or TimeoutConfig()
        self._executing = False
        self._active: ArbExecution | None = None
        self._failed_events: set[str] = set()
        self._unwind_phase1_secs = risk_profile.unwind_phase1_secs if risk_profile else 15
        self._unwind_phase2_secs = risk_profile.unwind_phase2_secs if risk_profile else 30
        self._unwind_price_step_cents = risk_profile.unwind_price_step_cents if risk_profile else 3
        self._sequential_execution = risk_profile.sequential_execution if risk_profile else False
        self.session_realized_loss = 0.0
        self._circuit_breaker_tripped = False
        self._processed_fill_ids: OrderedDict[str, None] = OrderedDict()
        self._max_session_loss = max_session_loss
        self._circuit_breaker_on_any_loss = circuit_breaker_on_any_loss
        self._unwind_tasks: list[asyncio.Task] = []
        self._on_circuit_breaker_cb: Callable[[], None] | None = None

    def is_event_blacklisted(self, event_ticker: str) -> bool:
        return event_ticker in self._failed_events

    def is_circuit_breaker_tripped(self) -> bool:
        return self._circuit_breaker_tripped

    def is_executing(self) -> bool:
        return self._executing

    async def cancel_unwinds(self):
        """Cancel active unwind tasks during shutdown."""
        for task in self._unwind_tasks:
            if not task.done():
                task.cancel()
        if self._unwind_tasks:
            await asyncio.gather(*self._unwind_tasks, return_exceptions=True)
            self._unwind_tasks.clear()

    def _track_fill_id(self, order_id: str):
        self._processed_fill_ids[order_id] = None
        if len(self._processed_fill_ids) > 10_000:
            self._processed_fill_ids.popitem(last=False)

    def build_orders(self, signal: TradeSignal, quantity: int) -> list[dict]:
        orders = []
        for i, (ticker, price) in enumerate(signal.legs):
            action = signal.leg_actions[i] if signal.leg_actions else "sell"
            if action == "buy":
                orders.append(self.order_builder.build_buy_order(ticker=ticker, yes_price=price, quantity=quantity))
            else:
                orders.append(self.order_builder.build_sell_order(ticker=ticker, yes_price=price, quantity=quantity))
        return orders

    async def execute(self, signal: TradeSignal, quantity: int = 1):
        if self._executing:
            logger.warning("Already executing, skipping signal for %s", signal.event_ticker)
            return

        # Pre-flight: for buy-side arbs, verify we have enough cash before hitting the exchange
        if signal.leg_actions and all(a == "buy" for a in signal.leg_actions):
            required = sum(price for _, price in signal.legs) * quantity
            try:
                bal = await asyncio.wait_for(self.api.get_balance(), timeout=self._timeouts.balance)
                available = bal.get("balance", 0) / 100.0
                if available < required:
                    logger.warning(
                        "Skipping %s: insufficient balance (need $%.2f, have $%.2f)",
                        signal.event_ticker, required, available,
                    )
                    return
            except Exception:
                logger.exception("Balance pre-check failed for %s — proceeding anyway", signal.event_ticker)

        self._executing = True
        try:
            orders = self.build_orders(signal, quantity)
            logger.info(
                "Executing arb on %s: %d legs, profit=%.4f (%.2f%%)",
                signal.event_ticker, len(signal.legs), signal.net_profit, signal.profit_pct,
            )

            if self._sequential_execution and len(orders) > 1:
                await self._execute_sequential(signal, orders, quantity)
                return

            response = await asyncio.wait_for(self.api.batch_create_orders(orders), timeout=self._timeouts.batch_create)
            logger.info("Batch order response: %s", response)
            order_list = response.get("orders", [])
            execution = ArbExecution(
                signal=signal,
                order_ids=[self.order_builder.unwrap_order(o).get("order_id", "") for o in order_list],
                started_at=time.time(),
            )
            self._active = execution

            for i, o in enumerate(order_list):
                inner = self.order_builder.unwrap_order(o)
                if inner.get("status") == "executed":
                    oid = inner.get("order_id", "")
                    price = float(inner.get("yes_price_dollars", 0))
                    qty = int(float(inner.get("fill_count_fp", 0)))
                    execution.filled[oid] = price
                    if oid:
                        self._track_fill_id(oid)
                    original_action = (
                        signal.leg_actions[i] if signal.leg_actions and i < len(signal.leg_actions) else "sell"
                    )
                    execution.filled_legs.append(FilledLeg(
                        ticker=inner.get("ticker", ""),
                        fill_price=price,
                        quantity=qty,
                        unwind_action="sell" if original_action == "buy" else "buy",
                    ))
                    self.positions.record_fill(
                        ticker=inner.get("ticker", ""),
                        side=inner.get("side", "yes"),
                        price=price,
                        quantity=qty,
                        action=inner.get("action", "sell"),
                    )

            is_buy_side = signal.leg_actions and all(a == "buy" for a in signal.leg_actions)
            if is_buy_side and len(execution.filled) < len(execution.order_ids):
                resting_ids = [oid for oid in execution.order_ids if oid not in execution.filled]
                if resting_ids:
                    logger.warning(
                        "Buy-side resting legs detected for %s: %d/%d filled, cancelling %d immediately",
                        signal.event_ticker, len(execution.filled), len(execution.order_ids), len(resting_ids),
                    )
                    await asyncio.wait_for(self.api.batch_cancel_orders(resting_ids), timeout=self._timeouts.batch_cancel)
                    if execution.filled:
                        logger.error(
                            "PARTIAL FILL on %s: %d legs filled, %d cancelled — UNHEDGED EXPOSURE",
                            signal.event_ticker, len(execution.filled), len(resting_ids),
                        )
                        self._failed_events.add(signal.event_ticker)
                        self._executing = False
                        self._active = None
                        self._launch_unwind(execution)
                    return

            await self._monitor_fills(execution)

            if execution._needs_unwind:
                if self.recorder:
                    self.recorder.record_execution(
                        event_ticker=signal.event_ticker,
                        strategy=signal.signal_type,
                        legs=[{
                            "ticker": t,
                            "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                            "price": p,
                            "quantity": quantity,
                        } for i, (t, p) in enumerate(signal.legs)],
                        result="partial_fill",
                        fill_details={oid: price for oid, price in execution.filled.items()},
                        unwind_cost=0.0,
                    )
                self._executing = False
                self._active = None
                self._launch_unwind(execution)
                return

            if self.recorder:
                filled_count = len(execution.filled)
                total_count = len(execution.order_ids)
                if filled_count == total_count:
                    result = "full_fill"
                elif filled_count > 0:
                    result = "partial_fill"
                else:
                    result = "failed"
                self.recorder.record_execution(
                    event_ticker=signal.event_ticker,
                    strategy=signal.signal_type,
                    legs=[{
                        "ticker": t,
                        "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                        "price": p,
                        "quantity": quantity,
                    } for i, (t, p) in enumerate(signal.legs)],
                    result=result,
                    fill_details={oid: price for oid, price in execution.filled.items()},
                    unwind_cost=0.0,
                )
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
            await asyncio.sleep(self._timeouts.monitor_poll)

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
            await asyncio.wait_for(self.api.batch_cancel_orders(unfilled), timeout=self._timeouts.batch_cancel)
            if filled_count > 0:
                logger.error(
                    "PARTIAL FILL on %s: %d legs filled, %d cancelled — UNHEDGED EXPOSURE",
                    execution.signal.event_ticker, filled_count, len(unfilled),
                )
                self._failed_events.add(execution.signal.event_ticker)
                execution._needs_unwind = True

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

    async def _execute_unwind_phase(self, ticker: str, price_cents: int, qty: int,
                                    prev_oid: str | None, action: str = "buy") -> tuple[bool, float, str]:
        try:
            if prev_oid:
                await asyncio.wait_for(self.api.cancel_order(prev_oid), timeout=self._timeouts.batch_cancel)
        except (asyncio.TimeoutError, Exception):
            logger.warning("Failed to cancel previous unwind order %s — proceeding", prev_oid)
        build = self.order_builder.build_buy_order if action == "buy" else self.order_builder.build_sell_order
        order = [build(ticker=ticker, yes_price=price_cents / 100, quantity=qty)]
        resp = await asyncio.wait_for(self.api.batch_create_orders(order), timeout=self._timeouts.batch_create)
        inner = self.order_builder.unwrap_order(resp.get("orders", [{}])[0])
        status = inner.get("status", "")
        unwind_price = float(inner.get("yes_price_dollars", 0))
        oid = inner.get("order_id", "")
        if oid:
            self._track_fill_id(oid)
        return status == "executed", unwind_price, oid

    async def _execute_sequential(self, signal: TradeSignal, orders: list[dict], quantity: int):
        """Execute legs one at a time, highest price first. Abort on first resting order."""
        leg_indices = list(range(len(orders)))
        leg_indices.sort(key=lambda i: orders[i].get("yes_price", 0), reverse=True)

        filled_legs: list[FilledLeg] = []
        filled_oids: list[str] = []

        for idx in leg_indices:
            order = orders[idx]
            try:
                resp = await asyncio.wait_for(
                    self.api.batch_create_orders([order]), timeout=self._timeouts.batch_create)
            except (asyncio.TimeoutError, Exception):
                logger.exception("Sequential leg %d timed out for %s — aborting", idx, signal.event_ticker)
                break

            inner = self.api.unwrap_order(resp.get("orders", [{}])[0])
            oid = inner.get("order_id", "")
            status = inner.get("status", "")

            if status == "executed":
                price = float(inner.get("yes_price_dollars", 0))
                qty = int(float(inner.get("fill_count_fp", 0)))
                if oid:
                    self._track_fill_id(oid)
                filled_oids.append(oid)
                original_action = (
                    signal.leg_actions[idx] if signal.leg_actions and idx < len(signal.leg_actions) else "sell"
                )
                filled_legs.append(FilledLeg(
                    ticker=inner.get("ticker", ""),
                    fill_price=price,
                    quantity=qty,
                    unwind_action="sell" if original_action == "buy" else "buy",
                ))
                self.positions.record_fill(
                    ticker=inner.get("ticker", ""),
                    side=inner.get("side", "yes"),
                    price=price,
                    quantity=qty,
                    action=inner.get("action", "sell"),
                )
                logger.info("Sequential leg %d filled: %s @ %.2f (%d/%d)",
                            idx, inner.get("ticker", ""), price,
                            len(filled_legs), len(orders))
            else:
                if oid:
                    try:
                        await asyncio.wait_for(
                            self.api.batch_cancel_orders([oid]), timeout=self._timeouts.batch_cancel)
                    except (asyncio.TimeoutError, Exception):
                        logger.warning("Failed to cancel resting order %s", oid)
                logger.warning(
                    "Sequential leg %d resting for %s (%s @ %s) — aborting after %d/%d legs filled",
                    idx, signal.event_ticker, inner.get("ticker", ""),
                    inner.get("yes_price_dollars", "?"),
                    len(filled_legs), len(orders),
                )
                break

        if len(filled_legs) == len(orders):
            logger.info("Sequential execution complete for %s: all %d legs filled", signal.event_ticker, len(orders))
            if self.recorder:
                self.recorder.record_execution(
                    event_ticker=signal.event_ticker,
                    strategy=signal.signal_type,
                    legs=[{"ticker": t, "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                           "price": p, "quantity": quantity} for i, (t, p) in enumerate(signal.legs)],
                    result="full_fill",
                    fill_details={oid: fl.fill_price for oid, fl in zip(filled_oids, filled_legs)},
                    unwind_cost=0.0,
                )
            return

        if filled_legs:
            logger.error(
                "PARTIAL FILL (sequential) on %s: %d/%d legs filled — unwinding",
                signal.event_ticker, len(filled_legs), len(orders),
            )
            self._failed_events.add(signal.event_ticker)
            execution = ArbExecution(signal=signal, order_ids=filled_oids, filled_legs=filled_legs)
            if self.recorder:
                self.recorder.record_execution(
                    event_ticker=signal.event_ticker,
                    strategy=signal.signal_type,
                    legs=[{"ticker": t, "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                           "price": p, "quantity": quantity} for i, (t, p) in enumerate(signal.legs)],
                    result="partial_fill",
                    fill_details={oid: fl.fill_price for oid, fl in zip(filled_oids, filled_legs)},
                    unwind_cost=0.0,
                )
            self._launch_unwind(execution)

    def _launch_unwind(self, execution: ArbExecution):
        """Launch unwind as a detached task so the executor is free for new signals."""
        max_unwind_secs = (self._unwind_phase1_secs + self._unwind_phase2_secs) * len(execution.filled_legs) + 60
        task = asyncio.create_task(self._unwind_with_timeout(execution, max_unwind_secs))
        self._unwind_tasks.append(task)
        task.add_done_callback(self._unwind_done_callback)

    def _unwind_done_callback(self, task: asyncio.Task):
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("Unwind task was cancelled")
        except Exception:
            logger.exception("Unwind task crashed")
        self._unwind_tasks = [t for t in self._unwind_tasks if not t.done()]
        if self._circuit_breaker_tripped and self._on_circuit_breaker_cb:
            self._on_circuit_breaker_cb()

    async def _unwind_with_timeout(self, execution: ArbExecution, timeout_secs: float):
        completed_tickers: set[str] = set()
        try:
            await asyncio.wait_for(
                self._unwind_partial_fill(execution, completed_tickers), timeout=timeout_secs)
        except asyncio.TimeoutError:
            remaining = [l for l in execution.filled_legs if l.ticker not in completed_tickers]
            logger.critical(
                "UNWIND TIMEOUT after %.0fs for %s — %d/%d legs incomplete, manual intervention required",
                timeout_secs, execution.signal.event_ticker,
                len(remaining), len(execution.filled_legs),
            )
            for leg in remaining:
                if leg.unwind_action == "buy":
                    self._record_unwind_loss(leg.ticker, leg.fill_price, 0.99, leg.quantity)
                else:
                    self._record_unwind_loss(leg.ticker, 0.01, leg.fill_price, leg.quantity)

    async def _unwind_partial_fill(self, execution: ArbExecution,
                                    completed_tickers: set[str] | None = None):
        step = self._unwind_price_step_cents / 100.0

        for leg in execution.filled_legs:
            if leg.quantity <= 0:
                continue
            logger.warning("Unwinding %d contracts of %s (filled @ %.2f)", leg.quantity, leg.ticker, leg.fill_price)
            phase2_wait = self._unwind_phase2_secs - self._unwind_phase1_secs
            if leg.unwind_action == "buy":  # closing a short (original leg was a sell)
                phases = [
                    (lambda fp, s=step: min(fp + s, 0.99), 0),
                    (lambda fp, s=step: min(fp + 2 * s, 0.99), self._unwind_phase1_secs),
                    (lambda fp, s=step: min(fp + 4 * s, 0.99), phase2_wait),
                    (lambda fp, s=step: max(min(fp + (1.0 - fp) * 0.5, 0.99), fp + 4 * s), self._unwind_phase2_secs),
                    (lambda fp: 0.99, self._unwind_phase2_secs),
                ]
                fallback = 0.99
            else:  # closing a long (original leg was a buy)
                phases = [
                    (lambda fp, s=step: max(fp - s, 0.01), 0),
                    (lambda fp, s=step: max(fp - 2 * s, 0.01), self._unwind_phase1_secs),
                    (lambda fp, s=step: max(fp - 4 * s, 0.01), phase2_wait),
                    (lambda fp, s=step: min(max(fp * 0.5, 0.01), max(fp - 4 * s, 0.01)), self._unwind_phase2_secs),
                    (lambda fp: 0.01, self._unwind_phase2_secs),
                ]
                fallback = 0.01

            prev_oid = None
            filled = False
            unwind_price = fallback
            for phase_i, (price_fn, wait_secs) in enumerate(phases, 1):
                if wait_secs > 0:
                    await asyncio.sleep(wait_secs)
                try:
                    price = price_fn(leg.fill_price)
                    filled, unwind_price, prev_oid = await self._execute_unwind_phase(
                        leg.ticker, round(price * 100), leg.quantity, prev_oid, leg.unwind_action)
                except asyncio.TimeoutError:
                    logger.warning("Unwind phase %d timed out for %s — advancing to next phase", phase_i, leg.ticker)
                    prev_oid = None
                    continue
                except Exception:
                    logger.exception("Unwind phase %d failed for %s — advancing to next phase", phase_i, leg.ticker)
                    prev_oid = None
                    continue
                if filled:
                    logger.info("Unwind phase %d filled for %s @ %.2f", phase_i, leg.ticker, price)
                    self.positions.record_fill(
                        ticker=leg.ticker, side="yes",
                        price=unwind_price, quantity=leg.quantity,
                        action=leg.unwind_action,
                    )
                    break

            if leg.unwind_action == "buy":
                self._record_unwind_loss(leg.ticker, leg.fill_price, unwind_price, leg.quantity)
            else:
                self._record_unwind_loss(leg.ticker, unwind_price, leg.fill_price, leg.quantity)
            if completed_tickers is not None:
                completed_tickers.add(leg.ticker)
            if not filled:
                logger.error("All %d unwind phases exhausted for %s — last order still resting, loss estimated", len(phases), leg.ticker)
            logger.warning("Unwind complete for %s: final @ $%.2f", leg.ticker, unwind_price)

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

        if order_id and order_id in self._processed_fill_ids:
            logger.debug("Skipping duplicate WS fill for %s (already processed from batch response)", order_id)
        else:
            if order_id:
                self._track_fill_id(order_id)
            self.positions.record_fill(
                ticker=ticker,
                side=side,
                price=price,
                quantity=quantity,
                action=action,
            )

        if self._active and order_id in self._active.order_ids:
            self._active.filled[order_id] = price
            if not any(fl.ticker == ticker for fl in self._active.filled_legs):
                idx = self._active.order_ids.index(order_id)
                sig = self._active.signal
                original_action = (
                    sig.leg_actions[idx] if sig.leg_actions and idx < len(sig.leg_actions) else "sell"
                )
                self._active.filled_legs.append(FilledLeg(
                    ticker=ticker,
                    fill_price=price,
                    quantity=quantity,
                    unwind_action="sell" if original_action == "buy" else "buy",
                ))
            logger.info("Leg filled: %s @ %.2f (%d/%d)",
                        ticker, price, len(self._active.filled), len(self._active.order_ids))
