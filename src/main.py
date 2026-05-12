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
        self.engine = ArbEngine(
            min_profit_pct=self.cfg.min_profit_pct,
            max_exposure_ratio=self.cfg.max_exposure_ratio,
        )
        self.positions = PositionTracker()
        self.executor = ExecutionManager(
            api=self.api,
            positions=self.positions,
            fill_timeout_secs=self.cfg.fill_timeout_secs,
        )
        self.scanner = MarketScanner(
            ws_url=self.cfg.ws_url,
            auth=self.auth,
            orderbook_mgr=self.orderbook_mgr,
            on_orderbook_update=self._on_orderbook_update,
        )
        self._event_tickers: set[str] = set()
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

        handler = logging.FileHandler(self.cfg.log_file)
        handler.setFormatter(logging.Formatter(
            '{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)s}'
        ))
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        root.setLevel(getattr(logging, self.cfg.log_level))
        root.addHandler(handler)
        root.addHandler(console)

    def _on_orderbook_update(self, market_ticker: str):
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            return

        event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
        signal = self.engine.evaluate(event_ticker, event_books)

        if signal and not self.executor.is_executing():
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

    async def _execute_and_track(self, signal):
        try:
            await self.executor.execute(signal)
            self._stats["arbs_executed"] += 1
        except Exception:
            self._stats["arbs_failed"] += 1

    async def _report_status(self):
        while True:
            await asyncio.sleep(30)
            uptime = time.time() - self._stats["started_at"]
            positions = self.positions.open_positions()
            realized_pnl = sum(
                p.avg_price * p.quantity for p in positions
            )
            logger.info(
                "STATUS | uptime=%.0fs | events=%d | arbs_detected=%d | "
                "arbs_executed=%d | arbs_failed=%d | theoretical_profit=$%.4f | "
                "open_positions=%d | premium_collected=$%.4f",
                uptime,
                len(self._event_tickers),
                self._stats["arbs_detected"],
                self._stats["arbs_executed"],
                self._stats["arbs_failed"],
                self._stats["total_theoretical_profit"],
                len(positions),
                realized_pnl,
            )

    async def _discover_events(self):
        cursor = ""
        while True:
            try:
                events, next_cursor = await self.api.fetch_events_page(cursor)
                new_tickers = []
                for event in events:
                    if event.event_ticker not in self._event_tickers:
                        self._event_tickers.add(event.event_ticker)
                        market_tickers = event.market_tickers()
                        self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                        new_tickers.extend(market_tickers)
                        logger.info(
                            "Discovered event %s (%s) with %d markets",
                            event.event_ticker, event.title, len(market_tickers),
                        )

                if new_tickers:
                    await self.scanner.subscribe(new_tickers)

                logger.info(
                    "Event scan: %d total events tracked, %d new markets this page",
                    len(self._event_tickers), len(new_tickers),
                )

                if next_cursor:
                    cursor = next_cursor
                    await asyncio.sleep(3)
                else:
                    cursor = ""
                    await asyncio.sleep(self.cfg.event_poll_interval_secs)

            except Exception:
                logger.exception("Error discovering events")
                await asyncio.sleep(self.cfg.event_poll_interval_secs)

    async def run(self):
        self._setup_logging()
        self._stats["started_at"] = time.time()
        logger.info("Starting Kalshi Arb Bot in %s mode", self.cfg.mode.upper())

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
