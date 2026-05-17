import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.config import load_config
from src.exchanges import create_exchange
from src.core.dispatch import Dispatcher
from src.core.engine import ArbEngine
from src.core.orderbook_manager import OrderbookManager
from src.core.positions import PositionTracker
from src.core.recorder import DataRecorder
from src.core.capital_guard import CapitalGuard
from src.core.reservation_store import ReservationStore
from src.core.risk import load_risk_profile
from src.executor import ExecutionManager
from src.strategies.maker import MakerManager
from src.strategies.two_sided import TwoSidedManager

logger = logging.getLogger("kalshi-arb")


class ArbBot:
    def __init__(self, config_path: str):
        self.cfg = load_config(config_path)
        exchange_config = {
            "api_key_id": self.cfg.api_key_id,
            "private_key_path": str(self.cfg.private_key_path),
            "base_url": self.cfg.rest_base_url,
            "ws_url": self.cfg.ws_url,
        }
        self.exchange = create_exchange(self.cfg.exchange, exchange_config)
        self.api = self.exchange.api
        self.order_builder = self.exchange.order_builder
        self.orderbook_mgr = OrderbookManager()

        self.risk_profile = load_risk_profile(self.cfg.risk_mode, self.cfg.strategy_overrides)

        self.reservations = ReservationStore(path="data/reservations.json")
        self.capital_guard = CapitalGuard(budgets=self.cfg.capital_budgets)

        if self.cfg.recording_enabled:
            self.recorder = DataRecorder(
                session_dir=self.cfg.recording_session_dir,
                max_db_size_mb=self.cfg.retention_max_db_size_mb,
            )
        else:
            self.recorder = DataRecorder()

        self.engine = ArbEngine(
            fee_model=self.exchange.fee_model,
            risk_profile=self.risk_profile,
            constraints=self.exchange.constraints,
            maker_max_horizon_hours=self.cfg.maker_max_horizon_hours,
            max_contracts_per_arb=self.cfg.max_contracts_per_arb,
            recorder=self.recorder,
        )
        self.positions = PositionTracker(recorder=self.recorder)
        self.executor = ExecutionManager(
            api=self.api,
            order_builder=self.exchange.order_builder,
            positions=self.positions,
            fill_timeout_secs=self.cfg.fill_timeout_secs,
            risk_profile=self.risk_profile,
            max_session_loss=self.cfg.max_session_loss,
            circuit_breaker_on_any_loss=self.cfg.circuit_breaker_on_any_loss,
            recorder=self.recorder,
        )
        self.executor._on_circuit_breaker_cb = lambda: asyncio.create_task(
            self._emergency_shutdown()
        )
        self.maker = MakerManager(
            api=self.api,
            order_builder=self.exchange.order_builder,
            fill_mode=self.cfg.maker_fill_mode,
            max_events=self.cfg.max_maker_events,
            risk_profile=self.risk_profile,
            track_fill_id=self.executor._track_fill_id,
        ) if self.cfg.maker_enabled else None
        self.two_sided = TwoSidedManager(
            api=self.api,
            order_builder=self.exchange.order_builder,
            risk_profile=self.risk_profile,
        ) if self.risk_profile.two_sided_max_inventory > 0 else None
        self.scanner = self.exchange.create_feed(
            self.orderbook_mgr,
            on_update=self._on_orderbook_update,
            on_fill=self._on_fill,
        )
        self.discovery = self.exchange.create_discovery(
            self.orderbook_mgr,
            self.scanner,
        )
        self.dispatcher = Dispatcher(
            engine=self.engine,
            executor=self.executor,
            maker=self.maker,
            orderbook_mgr=self.orderbook_mgr,
            market_metadata=self.discovery.market_metadata,
            enable_buy_side_arb=self.risk_profile.enable_buy_side_arb,
            near_expiry_window_minutes=self.risk_profile.near_expiry_window_minutes,
            monotone_registry=self.discovery.monotone_registry,
            event_total_markets=self.discovery.event_total_markets,
            recorder=self.recorder,
        )
        self._ob_update_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10000)
        self._maker_queue: asyncio.Queue | None = None
        self._maker_dirty_events: set[str] = set()
        self._stats = {
            "started_at": 0.0,
            "arbs_detected": 0,
            "arbs_executed": 0,
            "arbs_failed": 0,
            "total_theoretical_profit": 0.0,
        }
        self._shutdown_timeout: float = 60
        self._shutdown_api_timeout: float = 15
        self._shutdown_retry_delay: float = 2
        self._shutdown_retry_backoff: float = 1.5
        self._recent_trades_timeout: float = 10
        self._recent_trades_retry_timeout: float = 5

    def _setup_logging(self):
        log_dir = Path(self.cfg.log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                d = {
                    "timestamp": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                if record.exc_info:
                    d["exception"] = self.formatException(record.exc_info)
                return json.dumps(d)

        handler = logging.handlers.RotatingFileHandler(
            self.cfg.log_file,
            maxBytes=self.cfg.log_max_file_size_mb * 1024 * 1024,
            backupCount=self.cfg.log_max_backup_count,
        )
        handler.setFormatter(JsonFormatter())
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        root.setLevel(getattr(logging, self.cfg.log_level))
        root.addHandler(handler)
        root.addHandler(console)

    def _on_fill(self, fill_data: dict):
        order_id = fill_data.get("order_id", "")
        if self.two_sided and self.two_sided.owns_order(order_id):
            price = float(fill_data.get("yes_price_dollars", 0))
            quantity = int(float(fill_data.get("count_fp", 0)))
            asyncio.create_task(self.two_sided.handle_fill(order_id, price, quantity))
            return
        if self.dispatcher.route_fill(fill_data) == "maker":
            ticker = fill_data.get("market_ticker", "")
            price = float(fill_data.get("yes_price_dollars", 0))
            quantity = int(float(fill_data.get("count_fp", 0)))
            if ticker and quantity > 0:
                asyncio.create_task(
                    self.maker.handle_fill(order_id, ticker, price, quantity)
                )

    def _on_orderbook_update(self, market_ticker: str):
        try:
            self._ob_update_queue.put_nowait(market_ticker)
        except asyncio.QueueFull:
            pass

    async def _process_orderbook_updates(self):
        while True:
            market_ticker = await self._ob_update_queue.get()
            signal = self.dispatcher.process_orderbook_update(market_ticker)

            if signal:
                if self.maker and self.maker.is_event_active(signal.event_ticker):
                    asyncio.create_task(self.maker.cancel_event(signal.event_ticker))
                self._stats["arbs_detected"] += 1
                self._stats["total_theoretical_profit"] += signal.net_profit
                asyncio.create_task(self._execute_and_track(signal))
                continue

            dirty = self.dispatcher.consume_dirty_events()
            if dirty and self._maker_queue:
                self._maker_dirty_events.update(dirty)
                try:
                    self._maker_queue.put_nowait(True)
                except asyncio.QueueFull:
                    pass

    async def _validate_recent_trades(self, tickers: list[str]) -> bool:
        if not self.risk_profile.require_recent_trades:
            return True
        for ticker in tickers:
            try:
                resp = await asyncio.wait_for(
                    self.api.get_market_trades(ticker), timeout=self._recent_trades_timeout)
                if not resp.get("trades"):
                    logger.info("No recent trades for %s, skipping arb", ticker)
                    return False
            except asyncio.TimeoutError:
                logger.warning("Recent trades check timed out for %s — retrying once", ticker)
                try:
                    resp = await asyncio.wait_for(
                        self.api.get_market_trades(ticker), timeout=self._recent_trades_retry_timeout)
                    if not resp.get("trades"):
                        logger.info("No recent trades for %s on retry, skipping arb", ticker)
                        return False
                except asyncio.TimeoutError:
                    logger.warning("Recent trades check timed out twice for %s — treating as no trades", ticker)
                    return False
                except Exception:
                    logger.exception("Failed to check recent trades for %s on retry", ticker)
                    return False
            except Exception:
                logger.exception("Failed to check recent trades for %s", ticker)
                return False
        return True

    async def _maker_worker(self):
        self._maker_queue = asyncio.Queue(maxsize=1)
        failed_until: dict[str, float] = {}
        while True:
            await self._maker_queue.get()
            dirty = list(self._maker_dirty_events)
            self._maker_dirty_events.clear()

            now = time.time()
            for event_ticker in dirty:
                try:
                    if self.executor.is_circuit_breaker_tripped():
                        break
                    if failed_until.get(event_ticker, 0) > now:
                        continue

                    event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
                    if not event_books:
                        continue
                    meta = {t: self.discovery.market_metadata.get(t, {}) for t in event_books}

                    if self.maker.is_event_active(event_ticker):
                        await self.maker.on_orderbook_update(event_ticker, event_books)
                        continue

                    maker_signal = self.engine.evaluate_maker(
                        event_ticker, event_books, market_metadata=meta)
                    if not maker_signal:
                        continue

                    tickers = [t for t, _ in maker_signal.legs]
                    if not await self._validate_recent_trades(tickers):
                        continue

                    posted = await self.maker.post(maker_signal)
                    if posted:
                        logger.info(json.dumps({
                            "event": "maker_posted",
                            "event_ticker": maker_signal.event_ticker,
                            "legs": maker_signal.legs,
                            "net_profit": round(maker_signal.net_profit, 6),
                            "profit_pct": round(maker_signal.profit_pct, 2),
                        }))

                    if self.two_sided:
                        for mt, book in event_books.items():
                            vol = self.discovery.market_metadata.get(mt, {}).get("volume_24h", 0.0)
                            ts_signal = self.engine.evaluate_two_sided(mt, book, volume_24h=vol)
                            if ts_signal:
                                await self.two_sided.post(ts_signal)
                except Exception:
                    logger.exception("Maker worker error for %s", event_ticker)
                    failed_until[event_ticker] = time.time() + 60

    async def _execute_and_track(self, signal):
        cost = sum(price * signal.quantity for _, price in signal.legs)
        if not self.capital_guard.can_execute(self.cfg.exchange, cost):
            logger.info(
                "capital_limit: skipping %s (need $%.4f, headroom $%.4f)",
                signal.event_ticker, cost, self.capital_guard.headroom(self.cfg.exchange),
            )
            self.dispatcher.mark_execution_complete(signal.event_ticker)
            return
        try:
            tickers = [t for t, _ in signal.legs]
            if not await self._validate_recent_trades(tickers):
                logger.info("Recent trades check failed for %s, skipping", signal.event_ticker)
                return
            await self.executor.execute(signal, quantity=signal.quantity)
            self.capital_guard.commit(
                self.cfg.exchange,
                f"taker_{signal.event_ticker}",
                cost,
            )
            self._stats["arbs_executed"] += 1
            if self.executor.is_circuit_breaker_tripped():
                await self._emergency_shutdown()
        except Exception:
            logger.exception("Failed to execute arb for %s", signal.event_ticker)
            self._stats["arbs_failed"] += 1
        finally:
            self.dispatcher.mark_execution_complete(signal.event_ticker)

    async def _emergency_shutdown(self):
        if getattr(self, '_shutting_down', False):
            return
        self._shutting_down = True
        logger.critical(
            "CIRCUIT BREAKER TRIPPED — session loss: $%.4f. Cancelling all orders and closing positions.",
            self.executor.session_realized_loss,
        )
        try:
            await asyncio.wait_for(self._emergency_shutdown_inner(), timeout=self._shutdown_timeout)
        except asyncio.TimeoutError:
            logger.critical("Emergency shutdown timed out after %.0fs — manual intervention required",
                            self._shutdown_timeout)

    async def _emergency_shutdown_inner(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.maker:
                    await asyncio.wait_for(self.maker.cancel_all(), timeout=self._shutdown_api_timeout)
            except (asyncio.TimeoutError, Exception):
                logger.warning("Failed to cancel maker orders (attempt %d)", attempt + 1)

            try:
                orders_resp = await asyncio.wait_for(self.api.get_open_orders(), timeout=self._shutdown_api_timeout)
                resting = [o for o in orders_resp.get("orders", [])
                           if o.get("status") in ("resting", "pending", "open")]
                if resting:
                    await asyncio.wait_for(
                        self.api.batch_cancel_orders([o["order_id"] for o in resting]),
                        timeout=self._shutdown_api_timeout)
                    logger.info("Cancelled %d open orders", len(resting))
            except (asyncio.TimeoutError, Exception):
                logger.warning("Failed to cancel open orders (attempt %d)", attempt + 1)

            try:
                positions_resp = await asyncio.wait_for(self.api.get_positions(), timeout=self._shutdown_api_timeout)
                close_orders = []
                for mp in positions_resp.get("market_positions", []):
                    qty = int(float(mp.get("position_fp", "0")))
                    if qty != 0:
                        if self.reservations.is_reserved(mp["ticker"]):
                            logger.info("Emergency shutdown: skipping reserved %s", mp["ticker"])
                            continue
                        close_orders.append(self.order_builder.build_close_order(mp["ticker"], qty))
                if close_orders:
                    await asyncio.wait_for(self.api.batch_create_orders(close_orders),
                                           timeout=self._shutdown_api_timeout)
                    logger.info("Sent %d close orders", len(close_orders))
                else:
                    logger.info("No positions to close")
                return
            except (asyncio.TimeoutError, Exception):
                if attempt == max_retries - 1:
                    logger.critical(
                        "Emergency shutdown failed after %d attempts — manual intervention required",
                        max_retries,
                    )
                else:
                    wait = self._shutdown_retry_delay * (self._shutdown_retry_backoff ** attempt)
                    logger.warning(
                        "Emergency shutdown attempt %d failed, retrying in %ds",
                        attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)

    async def _report_status(self):
        while True:
            await asyncio.sleep(30)
            uptime = time.time() - self._stats["started_at"]
            positions = self.positions.open_positions()
            unrealized_premium = sum(p.avg_price * p.quantity for p in positions)
            realized_pnl = self.positions.realized_pnl
            cb_status = "TRIPPED" if self.executor.is_circuit_breaker_tripped() else "ok"
            maker_count = self.maker.active_event_count() if self.maker else 0

            # Count events closing within maker horizon (maker strategy eligibility)
            now = datetime.now(timezone.utc)
            horizon_cutoff = now + timedelta(hours=self.engine.maker_max_horizon_hours)
            maker_horizon_events = 0
            for event_ticker in self.discovery.event_tickers:
                for mt in self.orderbook_mgr.get_event_markets(event_ticker):
                    close_str = self.discovery.market_metadata.get(mt, {}).get("close_time", "")
                    if close_str:
                        try:
                            close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                            if now < close_dt <= horizon_cutoff:
                                maker_horizon_events += 1
                                break
                        except (ValueError, TypeError):
                            pass

            capital_info = ""
            if self.cfg.capital_budgets.get(self.cfg.exchange):
                capital_info = (
                    f" | capital_deployed=${self.capital_guard.deployed(self.cfg.exchange):.2f}"
                    f" | capital_headroom=${self.capital_guard.headroom(self.cfg.exchange):.2f}"
                )

            logger.info(
                "STATUS | uptime=%.0fs | events=%d | arbs_detected=%d | "
                "arbs_executed=%d | arbs_failed=%d | theoretical_profit=$%.4f | "
                "open_positions=%d | unrealized_premium=$%.4f | "
                "realized_pnl=$%.4f | session_loss=$%.4f | circuit_breaker=%s | "
                "maker_events=%d | maker_horizon=%d%s",
                uptime,
                len(self.discovery.event_tickers),
                self._stats["arbs_detected"],
                self._stats["arbs_executed"],
                self._stats["arbs_failed"],
                self._stats["total_theoretical_profit"],
                len(positions),
                unrealized_premium,
                realized_pnl,
                self.executor.session_realized_loss,
                cb_status,
                maker_count,
                maker_horizon_events,
                capital_info,
            )

    async def _boot_reconcile(self):
        """Cancel orphaned resting orders, load longs, close shorts."""
        try:
            await asyncio.wait_for(self._boot_reconcile_inner(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("Boot reconciliation timed out after 60s — proceeding without full reconcile")

    async def _boot_reconcile_inner(self):
        try:
            orders_resp = await asyncio.wait_for(
                self.api.get_open_orders(), timeout=15)
            resting = [
                o for o in orders_resp.get("orders", [])
                if o.get("status") in ("resting", "pending", "open")
            ]
            if len(resting) >= 100:
                logger.warning(
                    "Boot: get_open_orders returned %d resting orders (may be truncated — "
                    "increase limit or paginate if this fires regularly)",
                    len(resting),
                )
            if resting:
                logger.warning("Boot: cancelling %d orphaned resting order(s)", len(resting))
                await asyncio.wait_for(
                    self.api.batch_cancel_orders([o["order_id"] for o in resting]),
                    timeout=15)

            positions_resp = await asyncio.wait_for(
                self.api.get_positions(), timeout=15)
            longs, shorts = [], []
            for mp in positions_resp.get("market_positions", []):
                qty = int(float(mp.get("position_fp", "0")))
                ticker = mp["ticker"]
                avg_price = float(mp.get("average_price_fp", "0") or "0")

                if qty > 0:
                    reserved_qty = self.reservations.get_reserved_quantity(ticker, "yes")
                    bot_qty = max(0, qty - reserved_qty)
                    if reserved_qty > 0:
                        logger.info(
                            "Boot: %s has %d total, %d reserved, %d bot-owned",
                            ticker, qty, reserved_qty, bot_qty,
                        )
                    if bot_qty > 0:
                        longs.append((ticker, bot_qty))
                        self.capital_guard.commit(
                            self.cfg.exchange,
                            f"boot_{ticker}",
                            avg_price * bot_qty,
                        )
                elif qty < 0:
                    if self.reservations.is_reserved(ticker):
                        logger.info("Boot: skipping short close for reserved %s", ticker)
                        continue
                    shorts.append(ticker)

            if longs:
                logger.warning(
                    "Boot: found %d inherited long position(s) — loading into tracker", len(longs)
                )
                for ticker, qty in longs:
                    self.positions.load_position(ticker, "yes", qty)

            if shorts:
                logger.warning(
                    "Boot: found %d inherited short position(s) — closing before trading", len(shorts)
                )
                for ticker in shorts:
                    order = self.order_builder.build_close_order(ticker, -1)
                    try:
                        result = await asyncio.wait_for(
                            self.api.batch_create_orders([order]), timeout=15)
                        inner = self.order_builder.unwrap_order(result.get("orders", [{}])[0])
                        logger.info("Boot: closed short %s status=%s", ticker, inner.get("status", "?"))
                    except Exception:
                        logger.exception("Boot: failed to close short: %s", ticker)
                    await asyncio.sleep(0.15)

            if not resting and not longs and not shorts:
                logger.info("Boot: clean slate — no inherited orders or positions")

        except Exception:
            logger.exception("Boot reconciliation failed")

    async def run(self):
        self._setup_logging()
        self._stats["started_at"] = time.time()
        self.recorder.start_session({
            "mode": self.cfg.mode,
            "risk_mode": self.cfg.risk_mode,
            "max_contracts_per_arb": self.cfg.max_contracts_per_arb,
            "maker_enabled": self.cfg.maker_enabled,
            "circuit_breaker_on_any_loss": self.cfg.circuit_breaker_on_any_loss,
            "max_session_loss": self.cfg.max_session_loss,
            "strategy_overrides": self.cfg.strategy_overrides,
        })
        logger.info("Starting Kalshi Arb Bot in %s mode (risk: %s)",
                     self.cfg.mode.upper(), self.cfg.risk_mode)

        await self._boot_reconcile()
        await self.scanner.connect()
        await self.scanner.subscribe_fills()

        discovery_task = asyncio.create_task(self.discovery.poll_loop(self.cfg.event_poll_interval_secs))
        listen_task = asyncio.create_task(self.scanner.listen())
        status_task = asyncio.create_task(self._report_status())
        cleanup_task = asyncio.create_task(self.discovery.cleanup_loop())
        ob_task = asyncio.create_task(self._process_orderbook_updates())
        maker_task = asyncio.create_task(self._maker_worker()) if self.maker else None

        tasks = [discovery_task, listen_task, status_task, cleanup_task, ob_task]
        if maker_task:
            tasks.append(maker_task)
        if self.two_sided:
            tasks.append(asyncio.create_task(self.two_sided.timeout_loop()))
        if self.cfg.recording_enabled:
            tasks.append(asyncio.create_task(self._snapshot_loop()))
            tasks.append(asyncio.create_task(self._balance_loop()))
            tasks.append(asyncio.create_task(
                self.recorder.cleanup_loop(self.cfg.cleanup_interval_secs)
            ))

        shutdown_event = asyncio.Event()

        def _signal_handler():
            logger.info("Received shutdown signal")
            shutdown_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        try:
            gather_task = asyncio.gather(*tasks)
            shutdown_waiter = asyncio.create_task(shutdown_event.wait())
            done, pending = await asyncio.wait(
                [gather_task, shutdown_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_waiter in done:
                logger.info("Shutting down gracefully...")
                gather_task.cancel()
                try:
                    await gather_task
                except asyncio.CancelledError:
                    pass
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.executor.cancel_unwinds()
            self.scanner.stop()
            self.recorder.end_session()
            self.recorder.close()
            self._print_summary()
            await self.scanner.close()
            await self.api.close()

    async def _snapshot_loop(self):
        interval = self.cfg.recording_snapshot_interval_secs
        while True:
            await asyncio.sleep(interval)
            for event_ticker in list(self.discovery.event_tickers):
                for mt in self.orderbook_mgr.get_event_markets(event_ticker):
                    book = self.orderbook_mgr.get_orderbook(mt)
                    if book:
                        # Recorder schema uses yes_bids/no_bids column names;
                        # core Orderbook.bids = yes-side, .asks = no-side (inverted from no_bids)
                        self.recorder.record_orderbook_snapshot(
                            event_ticker=event_ticker,
                            market_ticker=mt,
                            yes_bids=book.bids,
                            no_bids=book.asks,
                        )

    async def _balance_loop(self):
        interval = self.cfg.recording_balance_poll_interval_secs
        while True:
            await asyncio.sleep(interval)
            try:
                bal = await asyncio.wait_for(self.api.get_balance(), timeout=10)
                self.recorder.record_balance(
                    cash_cents=bal.get("balance", 0),
                    portfolio_cents=bal.get("portfolio_value", 0),
                )
            except Exception:
                logger.exception("Failed to record balance")

    def _print_summary(self):
        uptime = time.time() - self._stats["started_at"]
        positions = self.positions.open_positions()
        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info("  Uptime: %.0f seconds", uptime)
        logger.info("  Events tracked: %d", len(self.discovery.event_tickers))
        logger.info("  Arbs detected: %d", self._stats["arbs_detected"])
        logger.info("  Arbs executed: %d", self._stats["arbs_executed"])
        logger.info("  Arbs failed: %d", self._stats["arbs_failed"])
        logger.info("  Theoretical profit: $%.4f per contract", self._stats["total_theoretical_profit"])
        logger.info("  Open positions: %d", len(positions))
        for p in positions:
            logger.info("    %s: %s %.0f @ $%.4f", p.ticker, p.side, p.quantity, p.avg_price)
        logger.info("=" * 60)


_PIDFILE = Path("/tmp/kalshi-arb.pid")


def _acquire_pidfile() -> None:
    if _PIDFILE.exists():
        try:
            pid = int(_PIDFILE.read_text().strip())
            os.kill(pid, 0)  # signal 0 = existence check only
            print(f"Another instance is already running (PID {pid}). Exiting.")
            sys.exit(1)
        except (ProcessLookupError, PermissionError):
            pass  # stale pidfile — previous run crashed without cleanup
    _PIDFILE.write_text(str(os.getpid()))


def _release_pidfile() -> None:
    _PIDFILE.unlink(missing_ok=True)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Copy config.example.yaml to config.yaml and fill in your credentials.")
        sys.exit(1)

    _acquire_pidfile()
    try:
        bot = ArbBot(config_path)
        asyncio.run(bot.run())
    finally:
        _release_pidfile()


if __name__ == "__main__":
    main()
