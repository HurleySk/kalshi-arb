"""
ReplayEngine — Load recorded orderbook snapshots from SQLite and re-evaluate
them with different ArbEngine parameters to answer "what would have happened
with different thresholds?"

Usage (module mode):
    python3 -m src.replay --db data/arb_history.db --sweep min_profit_pct=0.5:3.0:0.25
    python3 -m src.replay --sweep min_profit_pct=0.5:3.0:0.25 \\
        --train-end 2026-05-08 --test-start 2026-05-08
"""

import itertools
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.core.engine import ArbEngine
from src.core.models import Orderbook
from src.core.risk import load_risk_profile
from src.ports.fee_model import FeeModel


class ReplayEngine:
    """Replay recorded orderbook snapshots against ArbEngine with varying parameters."""

    def __init__(self, db_path: str, risk_mode: str = "conservative",
                 fee_model: FeeModel | None = None) -> None:
        self._db_path = db_path
        self._risk_mode = risk_mode
        self._fee_model = fee_model
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_snapshots(
        self,
        start: float | None = None,
        end: float | None = None,
    ) -> list[tuple[float, dict[str, dict[str, Orderbook]]]]:
        """Load orderbook snapshots from SQLite.

        Returns list of (timestamp, {event_ticker: {market_ticker: Orderbook}}),
        sorted by timestamp ascending. Multiple rows at the same timestamp are
        grouped into a single snapshot entry.
        """
        query = "SELECT ts, event_ticker, market_ticker, yes_bids_json, no_bids_json FROM orderbook_snapshots"
        conditions: list[str] = []
        params: list[Any] = []
        if start is not None:
            conditions.append("ts >= ?")
            params.append(start)
        if end is not None:
            conditions.append("ts <= ?")
            params.append(end)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY ts ASC"

        rows = self._conn.execute(query, params).fetchall()

        # Group by timestamp then event_ticker
        grouped: dict[float, dict[str, dict[str, Orderbook]]] = {}
        for row in rows:
            ts = row["ts"]
            event_ticker = row["event_ticker"]
            market_ticker = row["market_ticker"]
            yes_bids_json = row["yes_bids_json"] or "{}"
            no_bids_json = row["no_bids_json"] or "{}"

            bids = {int(k): v for k, v in json.loads(yes_bids_json).items()}
            asks = {int(k): v for k, v in json.loads(no_bids_json).items()}
            book = Orderbook(bids=bids, asks=asks)

            if ts not in grouped:
                grouped[ts] = {}
            if event_ticker not in grouped[ts]:
                grouped[ts][event_ticker] = {}
            grouped[ts][event_ticker][market_ticker] = book

        return [(ts, events) for ts, events in sorted(grouped.items())]

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    def sweep(
        self,
        param_ranges: dict[str, list],
        start: float | None = None,
        end: float | None = None,
        train_end: float | None = None,
        test_start: float | None = None,
    ) -> list[dict]:
        """Parameter sweep over recorded snapshots.

        Args:
            param_ranges: {param_name: [value1, value2, ...]} — one list per
                parameter. The sweep covers the cartesian product.
            start: optional lower bound timestamp for snapshot selection.
            end: optional upper bound timestamp for snapshot selection.
            train_end: if set together with test_start, splits results into
                train_* and test_* metrics.
            test_start: see train_end.

        Returns:
            list of dicts, one per parameter combination:
            {
                "params": {...},
                "signal_count": int,
                "theoretical_profit": float,
                # when train/test split:
                "train_signal_count": int,
                "train_theoretical_profit": float,
                "test_signal_count": int,
                "test_theoretical_profit": float,
            }
        """
        use_split = train_end is not None and test_start is not None

        if use_split:
            train_snapshots = self.load_snapshots(start=start, end=train_end)
            test_snapshots = self.load_snapshots(start=test_start, end=end)
            all_snapshots = self.load_snapshots(start=start, end=end)
        else:
            all_snapshots = self.load_snapshots(start=start, end=end)

        param_names = list(param_ranges.keys())
        param_values = list(param_ranges.values())

        results = []
        for combo in itertools.product(*param_values):
            overrides = dict(zip(param_names, combo))
            profile = load_risk_profile(self._risk_mode, overrides)
            engine = ArbEngine(fee_model=self._fee_model, risk_profile=profile)

            if use_split:
                train_count, train_profit = self._evaluate_snapshots(engine, train_snapshots)
                test_count, test_profit = self._evaluate_snapshots(engine, test_snapshots)
                all_count, all_profit = self._evaluate_snapshots(engine, all_snapshots)
                results.append({
                    "params": overrides,
                    "signal_count": all_count,
                    "theoretical_profit": all_profit,
                    "train_signal_count": train_count,
                    "train_theoretical_profit": train_profit,
                    "test_signal_count": test_count,
                    "test_theoretical_profit": test_profit,
                })
            else:
                count, profit = self._evaluate_snapshots(engine, all_snapshots)
                results.append({
                    "params": overrides,
                    "signal_count": count,
                    "theoretical_profit": profit,
                })

        return results

    # ------------------------------------------------------------------
    # Evaluation helper
    # ------------------------------------------------------------------

    def _evaluate_snapshots(
        self,
        engine: ArbEngine,
        snapshots: list[tuple[float, dict[str, dict[str, Orderbook]]]],
    ) -> tuple[int, float]:
        """Evaluate all snapshots through engine.evaluate() and evaluate_buy_side().

        Returns (signal_count, total_theoretical_profit).
        """
        signal_count = 0
        total_profit = 0.0
        seen: set[str] = set()

        for _ts, events in snapshots:
            for event_ticker, orderbooks in events.items():
                if event_ticker in seen:
                    continue

                signal = engine.evaluate(event_ticker, orderbooks)
                if signal is not None:
                    seen.add(event_ticker)
                    signal_count += 1
                    total_profit += signal.net_profit
                    continue

                buy_signal = engine.evaluate_buy_side(event_ticker, orderbooks)
                if buy_signal is not None:
                    seen.add(event_ticker)
                    signal_count += 1
                    total_profit += buy_signal.net_profit

        return signal_count, total_profit

    # ------------------------------------------------------------------
    # Plateau detection
    # ------------------------------------------------------------------

    def find_plateaus(
        self,
        results: list[dict],
        param_name: str,
        threshold: float = 0.10,
    ) -> list[tuple]:
        """Find ranges of param_name where profit is within threshold of maximum.

        Args:
            results: list of sweep result dicts (must contain "params" and
                "theoretical_profit").
            param_name: the parameter to scan for plateaus.
            threshold: fractional tolerance from max profit (default 0.10 = 10%).

        Returns:
            list of (lo, hi) tuples representing contiguous plateau ranges.
        """
        # Extract and sort by param value
        pairs = []
        for r in results:
            if param_name not in r["params"]:
                continue
            pairs.append((r["params"][param_name], r["theoretical_profit"]))
        if not pairs:
            return []

        pairs.sort(key=lambda x: x[0])
        max_profit = max(p for _, p in pairs)
        if max_profit <= 0:
            return []

        cutoff = max_profit * (1.0 - threshold)
        in_plateau: list[tuple] = []
        plateau_start = None

        for i, (val, profit) in enumerate(pairs):
            is_good = profit >= cutoff
            if is_good and plateau_start is None:
                plateau_start = val
            elif not is_good and plateau_start is not None:
                # plateau ended at previous value
                prev_val = pairs[i - 1][0]
                in_plateau.append((plateau_start, prev_val))
                plateau_start = None

        # Close any open plateau at the end
        if plateau_start is not None:
            in_plateau.append((plateau_start, pairs[-1][0]))

        return in_plateau

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_date_to_ts(date_str: str) -> float:
    """Parse YYYY-MM-DD as UTC midnight timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _parse_sweep_arg(arg: str) -> tuple[str, list[float]]:
    """Parse 'name=start:end:step' into (name, [values])."""
    name, spec = arg.split("=", 1)
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValueError(f"Expected name=start:end:step, got: {arg!r}")
    start_val, end_val, step = float(parts[0]), float(parts[1]), float(parts[2])
    values = []
    current = start_val
    while current <= end_val + 1e-9:
        values.append(round(current, 10))
        current += step
    return name, values


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Replay orderbook snapshots with parameter sweep",
    )
    parser.add_argument(
        "--db",
        default="data/arb_history.db",
        help="Path to SQLite database (default: data/arb_history.db)",
    )
    parser.add_argument(
        "--risk-mode",
        default="conservative",
        choices=["conservative", "moderate", "aggressive"],
        help="Base risk mode (default: conservative)",
    )
    parser.add_argument(
        "--sweep",
        nargs="+",
        metavar="PARAM=START:END:STEP",
        default=[],
        help="Parameter sweep specs, e.g. min_profit_pct=0.5:3.0:0.25",
    )
    parser.add_argument("--start", help="Start date YYYY-MM-DD (UTC)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (UTC)")
    parser.add_argument("--train-end", dest="train_end", help="Train split end date YYYY-MM-DD")
    parser.add_argument("--test-start", dest="test_start", help="Test split start date YYYY-MM-DD")
    args = parser.parse_args()

    param_ranges: dict[str, list] = {}
    for sweep_spec in args.sweep:
        name, values = _parse_sweep_arg(sweep_spec)
        param_ranges[name] = values

    start_ts = _parse_date_to_ts(args.start) if args.start else None
    end_ts = _parse_date_to_ts(args.end) if args.end else None
    train_end_ts = _parse_date_to_ts(args.train_end) if args.train_end else None
    test_start_ts = _parse_date_to_ts(args.test_start) if args.test_start else None

    replay = ReplayEngine(args.db, risk_mode=args.risk_mode)

    if param_ranges:
        results = replay.sweep(
            param_ranges,
            start=start_ts,
            end=end_ts,
            train_end=train_end_ts,
            test_start=test_start_ts,
        )
        # Print results as TSV
        if results:
            headers = list(results[0]["params"].keys()) + [
                k for k in results[0] if k != "params"
            ]
            print("\t".join(headers))
            for r in results:
                row = [str(r["params"].get(h, "")) for h in results[0]["params"]]
                row += [str(r.get(h, "")) for h in headers if h not in results[0]["params"]]
                print("\t".join(row))
        # Find plateaus for each swept param
        for pname in param_ranges:
            plateaus = replay.find_plateaus(results, pname)
            if plateaus:
                print(f"\nPlateaus for {pname} (within 10% of max profit):")
                for lo, hi in plateaus:
                    print(f"  [{lo}, {hi}]")
    else:
        # No sweep — just summarize snapshot count
        snapshots = replay.load_snapshots(start=start_ts, end=end_ts)
        print(f"Loaded {len(snapshots)} snapshot timestamps from {args.db}")

    replay.close()


if __name__ == "__main__":
    main()
