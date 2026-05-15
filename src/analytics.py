"""
Analytics — query the SQLite bot history database and produce performance reports.

Usage:
    python3 -m src.analytics --db data/arb_history.db
    python3 -m src.analytics --db data/arb_history.db --start 2026-05-01 --end 2026-05-15
    python3 -m src.analytics --db data/arb_history.db --format json
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class Analytics:
    """Read-only analytics over the DataRecorder SQLite database."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def strategy_breakdown(
        self, start: float | None = None, end: float | None = None
    ) -> dict[str, dict[str, int]]:
        """
        Return signal counts grouped by strategy and outcome.

        Example:
            {"taker": {"fire_count": 1, "reject_count": 2,
                       "near_miss_count": 1, "total": 4}}
        """
        where, params = self._time_filter(start, end, ts_col="ts")
        sql = f"""
            SELECT
                strategy,
                outcome,
                COUNT(*) AS cnt
            FROM signal_evaluations
            {where}
            GROUP BY strategy, outcome
        """
        rows = self._conn.execute(sql, params).fetchall()

        result: dict[str, dict[str, int]] = {}
        for row in rows:
            strategy = row["strategy"]
            outcome = row["outcome"]
            cnt = row["cnt"]
            if strategy not in result:
                result[strategy] = {
                    "fire_count": 0,
                    "reject_count": 0,
                    "near_miss_count": 0,
                    "total": 0,
                }
            entry = result[strategy]
            entry["total"] += cnt
            if outcome == "fire":
                entry["fire_count"] += cnt
            elif outcome == "reject":
                entry["reject_count"] += cnt
            elif outcome == "near_miss":
                entry["near_miss_count"] += cnt
        return result

    def rejection_funnel(
        self, start: float | None = None, end: float | None = None
    ) -> dict[str, int]:
        """
        Return reject counts grouped by reject_reason.

        Example:
            {"depth_filter": 3, "volume_filter": 2}
        """
        where, params = self._time_filter(start, end, ts_col="ts")
        sql = f"""
            SELECT reject_reason, COUNT(*) AS cnt
            FROM signal_evaluations
            {where}
            AND outcome = 'reject'
            AND reject_reason IS NOT NULL
            GROUP BY reject_reason
        """
        rows = self._conn.execute(sql, params).fetchall()
        return {row["reject_reason"]: row["cnt"] for row in rows}

    def partial_fill_analysis(
        self, start: float | None = None, end: float | None = None
    ) -> dict[str, Any]:
        """
        Return execution stats with partial-fill breakdown.

        Example:
            {"total_executions": 10, "partial_count": 2,
             "partial_rate": 0.2, "total_unwind_cost": 0.30, "avg_unwind_cost": 0.15}
        """
        where, params = self._time_filter(start, end, ts_col="ts")
        sql = f"""
            SELECT
                COUNT(*)                               AS total_executions,
                SUM(CASE WHEN result = 'partial_fill' THEN 1 ELSE 0 END) AS partial_count,
                SUM(COALESCE(unwind_cost, 0.0))        AS total_unwind_cost
            FROM executions
            {where}
        """
        row = self._conn.execute(sql, params).fetchone()
        total = row["total_executions"] or 0
        partial = row["partial_count"] or 0
        total_unwind = row["total_unwind_cost"] or 0.0

        # avg_unwind_cost is averaged over partial fills only
        avg_unwind: float = 0.0
        if partial > 0:
            avg_sql = f"""
                SELECT AVG(COALESCE(unwind_cost, 0.0)) AS avg_cost
                FROM executions
                {where}
                AND result = 'partial_fill'
            """
            avg_row = self._conn.execute(avg_sql, params).fetchone()
            avg_unwind = avg_row["avg_cost"] or 0.0

        return {
            "total_executions": total,
            "partial_count": partial,
            "partial_rate": partial / total if total > 0 else 0.0,
            "total_unwind_cost": round(total_unwind, 10),
            "avg_unwind_cost": round(avg_unwind, 10),
        }

    def balance_curve(
        self, start: float | None = None, end: float | None = None
    ) -> dict[str, Any]:
        """
        Return balance-curve summary from the balances table.

        Example:
            {"start_cash_cents": 10000, "end_cash_cents": 10200,
             "change_cents": 200, "max_drawdown_cents": 50, "snapshots": 10}
        """
        where, params = self._time_filter(start, end, ts_col="ts")
        sql = f"""
            SELECT ts, cash_cents
            FROM balances
            {where}
            ORDER BY ts ASC
        """
        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return {
                "start_cash_cents": None,
                "end_cash_cents": None,
                "change_cents": None,
                "max_drawdown_cents": None,
                "snapshots": 0,
            }

        cash_series = [r["cash_cents"] for r in rows]
        start_cash = cash_series[0]
        end_cash = cash_series[-1]
        change = end_cash - start_cash

        # Max drawdown: largest peak-to-trough decline
        max_drawdown = 0
        peak = cash_series[0]
        for val in cash_series[1:]:
            if val > peak:
                peak = val
            drawdown = peak - val
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return {
            "start_cash_cents": start_cash,
            "end_cash_cents": end_cash,
            "change_cents": change,
            "max_drawdown_cents": max_drawdown,
            "snapshots": len(rows),
        }

    def near_miss_analysis(
        self, start: float | None = None, end: float | None = None
    ) -> dict[str, Any]:
        """
        Return near-miss signal summary.

        Example:
            {"total_near_misses": 5,
             "by_strategy": {"taker": 3, "maker": 2},
             "best_miss": {"event_ticker": "EVT-A", "bid_sum": 0.98, "strategy": "taker"}}
        """
        where, params = self._time_filter(start, end, ts_col="ts")
        sql = f"""
            SELECT strategy, COUNT(*) AS cnt
            FROM signal_evaluations
            {where}
            AND outcome = 'near_miss'
            GROUP BY strategy
        """
        rows = self._conn.execute(sql, params).fetchall()
        total = sum(r["cnt"] for r in rows)
        by_strategy = {r["strategy"]: r["cnt"] for r in rows}

        best_miss = None
        if total > 0:
            # "best" = highest bid_sum among near-misses (closest to firing)
            best_sql = f"""
                SELECT event_ticker, bid_sum, strategy
                FROM signal_evaluations
                {where}
                AND outcome = 'near_miss'
                ORDER BY bid_sum DESC
                LIMIT 1
            """
            best_row = self._conn.execute(best_sql, params).fetchone()
            if best_row:
                best_miss = {
                    "event_ticker": best_row["event_ticker"],
                    "bid_sum": best_row["bid_sum"],
                    "strategy": best_row["strategy"],
                }

        return {
            "total_near_misses": total,
            "by_strategy": by_strategy,
            "best_miss": best_miss,
        }

    def full_report(
        self, start: float | None = None, end: float | None = None
    ) -> str:
        """
        Produce a human-readable multi-section performance report string.
        """
        breakdown = self.strategy_breakdown(start, end)
        funnel = self.rejection_funnel(start, end)
        pf = self.partial_fill_analysis(start, end)
        curve = self.balance_curve(start, end)
        nm = self.near_miss_analysis(start, end)

        lines: list[str] = []

        # ------------------------------------------------------------------
        # Header
        # ------------------------------------------------------------------
        lines.append("=" * 60)
        lines.append("  Kalshi Arb Bot — Performance Report")
        lines.append("=" * 60)

        # ------------------------------------------------------------------
        # Strategy Breakdown
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("Strategy Breakdown")
        lines.append("-" * 40)
        if breakdown:
            for strat, counts in sorted(breakdown.items()):
                lines.append(
                    f"  {strat:<20}  fire={counts['fire_count']}  "
                    f"reject={counts['reject_count']}  "
                    f"near_miss={counts['near_miss_count']}  "
                    f"total={counts['total']}"
                )
        else:
            lines.append("  (no data)")

        # ------------------------------------------------------------------
        # Rejection Funnel
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("Rejection Funnel")
        lines.append("-" * 40)
        if funnel:
            for reason, cnt in sorted(funnel.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {reason:<30}  {cnt}")
        else:
            lines.append("  (no rejections)")

        # ------------------------------------------------------------------
        # Partial Fill Analysis
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("Partial Fill Analysis")
        lines.append("-" * 40)
        lines.append(f"  Total executions : {pf['total_executions']}")
        lines.append(f"  Partial fills    : {pf['partial_count']}")
        rate_pct = pf["partial_rate"] * 100
        lines.append(f"  Partial rate     : {rate_pct:.1f}%")
        lines.append(f"  Total unwind cost: ${pf['total_unwind_cost']:.4f}")
        lines.append(f"  Avg unwind cost  : ${pf['avg_unwind_cost']:.4f}")

        # ------------------------------------------------------------------
        # Balance Curve
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("Balance Curve")
        lines.append("-" * 40)
        if curve["snapshots"] == 0:
            lines.append("  (no balance snapshots recorded)")
        else:
            lines.append(f"  Start cash : {curve['start_cash_cents']}¢")
            lines.append(f"  End cash   : {curve['end_cash_cents']}¢")
            lines.append(f"  Change     : {curve['change_cents']:+}¢")
            lines.append(f"  Max drawdown: {curve['max_drawdown_cents']}¢")
            lines.append(f"  Snapshots  : {curve['snapshots']}")

        # ------------------------------------------------------------------
        # Near Miss Analysis
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("Near Miss Analysis")
        lines.append("-" * 40)
        lines.append(f"  Total near misses: {nm['total_near_misses']}")
        if nm["by_strategy"]:
            for strat, cnt in sorted(nm["by_strategy"].items()):
                lines.append(f"    {strat}: {cnt}")
        if nm["best_miss"]:
            bm = nm["best_miss"]
            lines.append(
                f"  Best miss: {bm['event_ticker']}  "
                f"strategy={bm['strategy']}  "
                f"bid_sum={bm['bid_sum']}"
            )

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _time_filter(
        self,
        start: float | None,
        end: float | None,
        ts_col: str = "ts",
    ) -> tuple[str, list]:
        """
        Return a (WHERE clause, params list) pair.

        The WHERE clause always starts with "WHERE 1=1" so callers can safely
        append additional "AND ..." conditions.
        """
        where = "WHERE 1=1"
        params: list = []
        if start is not None:
            where += f" AND {ts_col} >= ?"
            params.append(start)
        if end is not None:
            where += f" AND {ts_col} <= ?"
            params.append(end)
        return where, params

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> float:
    """Parse YYYY-MM-DD string to a UTC Unix timestamp (start of day)."""
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kalshi Arb Bot — Performance Analytics",
    )
    parser.add_argument(
        "--db",
        default="data/arb_history.db",
        help="Path to the SQLite history database (default: data/arb_history.db)",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Start date filter, YYYY-MM-DD (inclusive, UTC)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date filter, YYYY-MM-DD (inclusive, UTC)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: text (default) or json",
    )
    args = parser.parse_args()

    start_ts = _parse_date(args.start) if args.start else None
    end_ts = _parse_date(args.end) if args.end else None

    analytics = Analytics(args.db)
    try:
        if args.format == "json":
            data = {
                "strategy_breakdown": analytics.strategy_breakdown(start_ts, end_ts),
                "rejection_funnel": analytics.rejection_funnel(start_ts, end_ts),
                "partial_fill_analysis": analytics.partial_fill_analysis(start_ts, end_ts),
                "balance_curve": analytics.balance_curve(start_ts, end_ts),
                "near_miss_analysis": analytics.near_miss_analysis(start_ts, end_ts),
            }
            print(json.dumps(data, indent=2))
        else:
            print(analytics.full_report(start_ts, end_ts))
    finally:
        analytics.close()


if __name__ == "__main__":
    main()
