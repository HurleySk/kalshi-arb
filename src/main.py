import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from src.auth import KalshiAuth
from src.api import KalshiAPI
from src.config import load_config
from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.positions import PositionTracker
from src.maker import MakerManager
from src.risk import load_risk_profile
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

        self.engine = ArbEngine(risk_profile=self.risk_profile)
        self.positions = PositionTracker()
        self.executor = ExecutionManager(
            api=self.api,
            positions=self.positions,
            fill_timeout_secs=self.cfg.fill_timeout_secs,
            risk_profile=self.risk_profile,
        )
        self.executor._max_session_loss = self.cfg.max_session_loss
        self.executor._circuit_breaker_on_any_loss = self.cfg.circuit_breaker_on_any_loss
        self.maker = MakerManager(
            api=self.api,
            fill_mode=self.cfg.maker_fill_mode,
            max_events=self.cfg.max_maker_events,
        ) if self.cfg.maker_enabled else None
        self.scanner = MarketScanner(
            ws_url=self.cfg.ws_url,
            auth=self.auth,
            orderbook_mgr=self.orderbook_mgr,
            on_orderbook_update=self._on_orderbook_update,
            on_fill=self._on_fill,
        )
        self._event_tickers: set[str] = set()
        self._market_metadata: dict[str, dict] = {}
        self._last_signal_time: dict[str, float] = {}
        self._signal_cooldown = 60.0
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
        if self.maker and self.maker.owns_order(order_id):
            ticker = fill_data.get("market_ticker", "")
            price = float(fill_data.get("yes_price_dollars", 0))
            quantity = int(float(fill_data.get("count_fp", 0)))
            if ticker and quantity > 0:
                asyncio.get_event_loop().create_task(
                    self.maker.handle_fill(order_id, ticker, price, quantity)
                )
        else:
            self.executor.handle_fill(fill_data)

    def _on_orderbook_update(self, market_ticker: str):
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            return

        if self.executor.is_circuit_breaker_tripped():
            return

        event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
        meta = {t: self._market_metadata.get(t, {}) for t in event_books}

        # Reprice active maker events on every orderbook update
        if self.maker and self.maker.is_event_active(event_ticker):
            asyncio.get_event_loop().create_task(
                self.maker.on_orderbook_update(event_ticker, event_books)
            )

        # Taker layer: immediate execution on fat arbs
        signal = self.engine.evaluate(event_ticker, event_books, market_metadata=meta)

        if signal and not self.executor.is_executing():
            if self.executor.is_event_blacklisted(event_ticker):
                return
            # If maker orders are active on this event, cancel them — taker takes over
            if self.maker and self.maker.is_event_active(event_ticker):
                asyncio.get_event_loop().create_task(self.maker.cancel_event(event_ticker))
            last = self._last_signal_time.get(event_ticker, 0)
            if time.time() - last < self._signal_cooldown:
                return
            self._last_signal_time[event_ticker] = time.time()
            self._stats["arbs_detected"] += 1
            self._stats["total_theoretical_profit"] += signal.net_profit
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
            asyncio.get_event_loop().create_task(self._execute_and_track(signal))
            return

        # Maker layer: post on near-arb opportunities
        if self.maker and not signal:
            maker_signal = self.engine.evaluate_maker(event_ticker, event_books, market_metadata=meta)
            if maker_signal and not self.maker.is_event_active(event_ticker):
                asyncio.get_event_loop().create_task(self._post_maker(maker_signal))

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

    async def _post_maker(self, signal):
        try:
            tickers = [t for t, _ in signal.legs]
            if not await self._validate_recent_trades(tickers):
                return
            await self.maker.post(signal)
            logger.info(json.dumps({
                "event": "maker_posted",
                "event_ticker": signal.event_ticker,
                "legs": signal.legs,
                "net_profit": round(signal.net_profit, 6),
                "profit_pct": round(signal.profit_pct, 2),
            }))
        except Exception:
            logger.exception("Failed to post maker orders for %s", signal.event_ticker)

    async def _execute_and_track(self, signal):
        try:
            tickers = [t for t, _ in signal.legs]
            if not await self._validate_recent_trades(tickers):
                logger.info("Recent trades check failed for %s, skipping", signal.event_ticker)
                return
            await self.executor.execute(signal)
            self._stats["arbs_executed"] += 1
            if self.executor.is_circuit_breaker_tripped():
                await self._emergency_shutdown()
        except Exception:
            logger.exception("Failed to execute arb for %s", signal.event_ticker)
            self._stats["arbs_failed"] += 1

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
                if qty < 0:
                    close_orders.append({
                        "ticker": mp["ticker"], "action": "buy", "side": "yes",
                        "type": "limit", "yes_price": 100, "count": abs(qty),
                    })
                elif qty > 0:
                    close_orders.append({
                        "ticker": mp["ticker"], "action": "sell", "side": "yes",
                        "type": "limit", "yes_price": 1, "count": qty,
                    })
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
            realized_pnl = sum(
                p.avg_price * p.quantity for p in positions
            )
            cb_status = "TRIPPED" if self.executor.is_circuit_breaker_tripped() else "ok"
            maker_count = self.maker.active_event_count() if self.maker else 0
            logger.info(
                "STATUS | uptime=%.0fs | events=%d | arbs_detected=%d | "
                "arbs_executed=%d | arbs_failed=%d | theoretical_profit=$%.4f | "
                "open_positions=%d | premium_collected=$%.4f | "
                "session_loss=$%.4f | circuit_breaker=%s | maker_events=%d",
                uptime,
                len(self._event_tickers),
                self._stats["arbs_detected"],
                self._stats["arbs_executed"],
                self._stats["arbs_failed"],
                self._stats["total_theoretical_profit"],
                len(positions),
                realized_pnl,
                self.executor.session_realized_loss,
                cb_status,
                maker_count,
            )

    def _register_events(self, events) -> list[str]:
        new_tickers = []
        for event in events:
            if event.event_ticker not in self._event_tickers:
                self._event_tickers.add(event.event_ticker)
                market_tickers = event.market_tickers()
                self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                new_tickers.extend(market_tickers)
            for m in event.markets:
                self._market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.expected_expiration_time,
                    "volume_24h": m.volume_24h,
                }
        return new_tickers

    async def _full_scan(self):
        logger.info("Starting full event scan...")
        cursor = ""
        pages = 0
        retries = 0
        max_retries = 3
        all_events = []
        while True:
            try:
                events, next_cursor = await self.api.fetch_events_page(cursor)
                all_events.extend(events)
                pages += 1
                retries = 0
                if pages % 10 == 0:
                    logger.info("Scanning page %d... (%d events collected)", pages, len(all_events))
                if not next_cursor:
                    break
                cursor = next_cursor
                await asyncio.sleep(0.5)
            except Exception:
                retries += 1
                if retries >= max_retries:
                    logger.error("Full scan aborted after %d retries at page %d", max_retries, pages)
                    break
                logger.exception("Error during full scan at page %d (retry %d/%d)", pages, retries, max_retries)
                await asyncio.sleep(5)

        def _earliest_close(event):
            times = [m.close_time for m in event.markets if m.close_time]
            return min(times) if times else "9999"

        all_events.sort(key=_earliest_close)
        all_new_tickers = []
        for event in all_events:
            all_new_tickers.extend(self._register_events([event]))
        if all_new_tickers:
            await self.scanner.subscribe(all_new_tickers)
        total_new = len(all_new_tickers)

        logger.info(
            "Full scan complete: %d pages, %d events, %d new markets (sorted by close_time)",
            pages, len(self._event_tickers), total_new,
        )

    async def _discover_events(self):
        await self._full_scan()
        while True:
            await asyncio.sleep(self.cfg.event_poll_interval_secs)
            try:
                events, _ = await self.api.fetch_events_page("")
                new_tickers = self._register_events(events)
                if new_tickers:
                    await self.scanner.subscribe(new_tickers)
                    logger.info("Re-poll: %d new markets found", len(new_tickers))
            except Exception:
                logger.exception("Error during event re-poll")

    async def run(self):
        self._setup_logging()
        self._stats["started_at"] = time.time()
        logger.info("Starting Kalshi Arb Bot in %s mode (risk: %s)",
                     self.cfg.mode.upper(), self.cfg.risk_mode)

        await self.scanner.connect()
        await self.scanner.subscribe_fills()

        discovery_task = asyncio.create_task(self._discover_events())
        listen_task = asyncio.create_task(self.scanner.listen())
        status_task = asyncio.create_task(self._report_status())

        try:
            await asyncio.gather(discovery_task, listen_task, status_task)
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
        logger.info("  Events tracked: %d", len(self._event_tickers))
        logger.info("  Arbs detected: %d", self._stats["arbs_detected"])
        logger.info("  Arbs executed: %d", self._stats["arbs_executed"])
        logger.info("  Arbs failed: %d", self._stats["arbs_failed"])
        logger.info("  Theoretical profit: $%.4f per contract", self._stats["total_theoretical_profit"])
        logger.info("  Open positions: %d", len(positions))
        for p in positions:
            logger.info("    %s: %s %.0f @ $%.4f", p.ticker, p.side, p.quantity, p.avg_price)
        logger.info("=" * 60)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Copy config.example.yaml to config.yaml and fill in your credentials.")
        sys.exit(1)

    bot = ArbBot(config_path)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
