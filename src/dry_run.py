"""
DryRunEngine — Replay recorded orderbook snapshots through the real
ExecutionManager with a SimulatedAPI to catch race conditions, position
tracker bugs, and fill dedup failures without risking real money.

Usage:
    python3 -m src.dry_run --db data/arb_history.db
    python3 -m src.dry_run --db data/arb_history.db --partial-fill-rate 0.2 --ws-race-rate 0.5
"""

import argparse
import asyncio
import logging
import sys

from src.core.engine import ArbEngine
from src.core.positions import PositionTracker
from src.core.replay import ReplayEngine
from src.core.risk import load_risk_profile
from src.exchanges.kalshi.fee_model import KalshiFeeModel
from src.executor import ExecutionManager, TimeoutConfig
from src.simulator import FaultConfig, SimulatedAPI

logger = logging.getLogger(__name__)

_FAST_TIMEOUTS = TimeoutConfig(batch_create=0.01, batch_cancel=0.01, balance=0.01, monitor_poll=0.001)


class DryRunEngine:
    def __init__(self, db_path: str, risk_mode: str = "conservative",
                 fault_config: FaultConfig | None = None):
        self._db_path = db_path
        self._risk_mode = risk_mode
        self._faults = fault_config or FaultConfig()
        self.sim_api = SimulatedAPI(fault_config=self._faults)
        self.risk_profile = load_risk_profile(risk_mode, {})
        self.engine = ArbEngine(fee_model=KalshiFeeModel(), risk_profile=self.risk_profile)
        self.positions = PositionTracker()
        self.executor = ExecutionManager(
            api=self.sim_api,
            positions=self.positions,
            fill_timeout_secs=0,
            risk_profile=self.risk_profile,
            circuit_breaker_on_any_loss=False,
            timeouts=_FAST_TIMEOUTS,
        )

        self.signals_fired = 0
        self.executions = 0
        self.partial_fills = 0
        self.ws_fills_injected = 0
        self.ws_fills_deduped = 0

    async def run(self, start: float | None = None, end: float | None = None) -> dict:
        replay = ReplayEngine(self._db_path, self._risk_mode)
        snapshots = replay.load_snapshots(start=start, end=end)
        if not snapshots:
            logger.warning("No snapshots found in %s", self._db_path)
            return self._build_report([])

        seen_events: set[str] = set()

        for _ts, events in snapshots:
            for event_ticker, orderbooks in events.items():
                if event_ticker in seen_events:
                    continue
                if self.executor.is_event_blacklisted(event_ticker):
                    continue

                signal = self.engine.evaluate(event_ticker, orderbooks)
                if signal is None:
                    signal = self.engine.evaluate_buy_side(event_ticker, orderbooks)
                if signal is None:
                    continue

                seen_events.add(event_ticker)
                self.signals_fired += 1

                await self.executor.execute(signal, quantity=signal.quantity)
                self.executions += 1
                if self.executor.is_event_blacklisted(event_ticker):
                    self.partial_fills += 1

                if self.executor._active is None:
                    await asyncio.sleep(0.01)

                ws_fills = list(self.sim_api.pending_ws_fills)
                self.sim_api.pending_ws_fills.clear()
                for fill in ws_fills:
                    self.ws_fills_injected += 1
                    oid = fill.get("order_id", "")
                    was_known = oid in self.executor._processed_fill_ids
                    self.executor.handle_fill(fill)
                    if was_known:
                        self.ws_fills_deduped += 1

        violations = self.verify_invariants()
        return self._build_report(violations)

    def verify_invariants(self) -> list[str]:
        violations = []

        for pos in self.positions.open_positions():
            if pos.quantity < 0:
                violations.append(
                    f"PHANTOM_SHORT: {pos.ticker} has quantity={pos.quantity}")

        for task in self.executor._unwind_tasks:
            if not task.done():
                violations.append(
                    f"UNWIND_HANGING: unwind task still running")

        return violations

    def _build_report(self, violations: list[str]) -> dict:
        open_pos = self.positions.open_positions()
        return {
            "signals_fired": self.signals_fired,
            "executions": self.executions,
            "partial_fills": self.partial_fills,
            "ws_fills_injected": self.ws_fills_injected,
            "ws_fills_deduped": self.ws_fills_deduped,
            "open_positions": len(open_pos),
            "realized_pnl": round(self.positions.realized_pnl, 4),
            "session_loss": round(self.executor.session_realized_loss, 4),
            "invariant_violations": violations,
            "passed": len(violations) == 0,
        }


def _print_report(report: dict):
    print("\n=== DRY RUN REPORT ===")
    print(f"Signals fired:      {report['signals_fired']}")
    print(f"Executions:         {report['executions']}")
    print(f"Partial fills:      {report['partial_fills']}")
    print(f"WS fills injected:  {report['ws_fills_injected']}")
    print(f"WS fills deduped:   {report['ws_fills_deduped']}")
    print(f"Open positions:     {report['open_positions']}")
    print(f"Realized P&L:       ${report['realized_pnl']}")
    print(f"Session loss:       ${report['session_loss']}")
    print()
    if report["passed"]:
        print("INVARIANTS: ALL PASSED")
    else:
        print(f"INVARIANTS: {len(report['invariant_violations'])} VIOLATIONS")
        for v in report["invariant_violations"]:
            print(f"  - {v}")
    print()


async def _main():
    parser = argparse.ArgumentParser(description="Dry-run replay with fault injection")
    parser.add_argument("--db", default="data/arb_history.db")
    parser.add_argument("--risk-mode", default="conservative")
    parser.add_argument("--partial-fill-rate", type=float, default=0.0)
    parser.add_argument("--ws-race-rate", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    faults = FaultConfig(
        partial_fill_rate=args.partial_fill_rate,
        ws_race_rate=args.ws_race_rate,
        seed=args.seed,
    )
    engine = DryRunEngine(args.db, risk_mode=args.risk_mode, fault_config=faults)
    report = await engine.run()
    _print_report(report)
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    asyncio.run(_main())
