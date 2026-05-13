import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.auth import KalshiAuth
from src.api import KalshiAPI
from src.config import load_config
from src.dispatch import Dispatcher
from src.discovery import EventDiscovery
from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.positions import PositionTracker
from src.maker import MakerManager
from src.risk import load_risk_profile
from src.two_sided import TwoSidedManager
from src.scanner import MarketScanner, OrderbookManager

logger = logging.getLogger("kalshi-arb")


class ArbBot:
    def __init__(self, config_path: str):
        self.cfg = load_config(config_path)
        self.auth = KalshiAuth(
            api_key_id=self.cfg.api_key_id,
            private_key_path=self.cfg.private_key_path,
        )
        self.api = KalshiAPI(base_url=self.cfg.rest_base_url, auth=self.auth)
        self.orderbook_mgr = OrderbookManager()

        self.risk_profile = load_risk_profile(self.cfg.risk_mode, self.cfg.strategy_overrides)

        self.engine = ArbEngine(
            risk_profile=self.risk_profile,
            maker_max_horizon_hours=self.cfg.maker_max_horizon_hours,
            max_contracts_per_arb=self.cfg.max_contracts_per_arb,
        )
        self.positions = PositionTracker()
        self.executor = ExecutionManager(
            api=self.api,
            positions=self.positions,
            fill_timeout_secs=self.cfg.fill_timeout_secs,
            risk_profile=self.risk_profile,
            max_session_loss=self.cfg.max_session_loss,
            circuit_breaker_on_any_loss=self.cfg.circuit_breaker_on_any_loss,
        )
        self.maker = MakerManager(
            api=self.api,
            fill_mode=self.cfg.maker_fill_mode,
            max_events=self.cfg.max_maker_events,
            risk_profile=self.risk_profile,
        ) if self.cfg.maker_enabled else None
        self.two_sided = TwoSidedManager(
            api=self.api,
            risk_profile=self.risk_profile,
        ) if self.risk_profile.two_sided_max_inventory > 0 else None
        self.scanner = MarketScanner(
            ws_url=self.cfg.ws_url,
            auth=self.auth,
            orderbook_mgr=self.orderbook_mgr,
            on_orderbook_update=self._on_orderbook_update,
            on_fill=self._on_fill,
        )
        self.discovery = EventDiscovery(
            api=self.api,
            orderbook_mgr=self.orderbook_mgr,
            scanner=self.scanner,
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

        handler = logging.FileHandler(self.cfg.log_file)
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
                resp = await self.api.get_market_trades(ticker)
                if not resp.get("trades"):
                    logger.info("No recent trades for %s, skipping arb", ticker)
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
        try:
            tickers = [t for t, _ in signal.legs]
            if not await self._validate_recent_trades(tickers):
                logger.info("Recent trades check failed for %s, skipping", signal.event_ticker)
                return
            await self.executor.execute(signal, quantity=signal.quantity)
            self._stats["arbs_executed"] += 1
            if self.executor.is_circuit_breaker_tripped():
                await self._emergency_shutdown()
        except Exception:
            logger.exception("Failed to execute arb for %s", signal.event_ticker)
            self._stats["arbs_failed"] += 1
        finally:
            self.dispatcher.mark_execution_complete(signal.event_ticker)

    async def _emergency_shutdown(self):
        logger.critical(
            "CIRCUIT BREAKER TRIPPED — session loss: $%.4f. Cancelling all orders and closing positions.",
            self.executor.session_realized_loss,
        )
        try:
            if self.maker:
                await self.maker.cancel_all()
            orders_resp = await self.api.get_open_orders()
            resting = [o for o in orders_resp.get("orders", [])
                       if o.get("status") in ("resting", "pending", "open")]
            if resting:
                await self.api.batch_cancel_orders([o["order_id"] for o in resting])
                logger.info("Cancelled %d open orders", len(resting))

            positions_resp = await self.api.get_positions()
            close_orders = []
            for mp in positions_resp.get("market_positions", []):
                qty = int(float(mp.get("position_fp", "0")))
                if qty != 0:
                    close_orders.append(self.api.build_close_order(mp["ticker"], qty))
            if close_orders:
                await self.api.batch_create_orders(close_orders)
                logger.info("Sent %d close orders", len(close_orders))
            else:
                logger.info("No positions to close")
        except Exception:
            logger.exception("Error during emergency shutdown")

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
                for mt in self.orderbook_mgr._event_markets.get(event_ticker, []):
                    close_str = self.discovery.market_metadata.get(mt, {}).get("close_time", "")
                    if close_str:
                        try:
                            close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                            if now < close_dt <= horizon_cutoff:
                                maker_horizon_events += 1
                                break
                        except (ValueError, TypeError):
                            pass

            logger.info(
                "STATUS | uptime=%.0fs | events=%d | arbs_detected=%d | "
                "arbs_executed=%d | arbs_failed=%d | theoretical_profit=$%.4f | "
                "open_positions=%d | unrealized_premium=$%.4f | "
                "realized_pnl=$%.4f | session_loss=$%.4f | circuit_breaker=%s | "
                "maker_events=%d | maker_horizon=%d",
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
            )

    async def _boot_reconcile(self):
        """Cancel orphaned resting orders, load longs, close shorts."""
        try:
            orders_resp = await self.api.get_open_orders()
            resting = [
                o for o in orders_resp.get("orders", [])
                if o.get("status") in ("resting", "pending", "open")
            ]
            if len(resting) >= 100:
                # get_open_orders returns at most 100 results; warn if we may have hit the cap
                logger.warning(
                    "Boot: get_open_orders returned %d resting orders (may be truncated — "
                    "increase limit or paginate if this fires regularly)",
                    len(resting),
                )
            if resting:
                logger.warning("Boot: cancelling %d orphaned resting order(s)", len(resting))
                await self.api.batch_cancel_orders([o["order_id"] for o in resting])

            positions_resp = await self.api.get_positions()
            longs, shorts = [], []
            for mp in positions_resp.get("market_positions", []):
                qty = int(float(mp.get("position_fp", "0")))
                if qty > 0:
                    longs.append((mp["ticker"], qty))
                elif qty < 0:
                    shorts.append(mp["ticker"])

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
                    order = self.api.build_close_order(ticker, -1)
                    try:
                        result = await self.api.batch_create_orders([order])
                        inner = self.api.unwrap_order(result.get("orders", [{}])[0])
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

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self._print_summary()
            await self.scanner.close()
            await self.api.close()

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
