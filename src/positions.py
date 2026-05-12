import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    ticker: str
    side: str
    quantity: float
    avg_price: float


class PositionTracker:
    def __init__(self):
        self._positions: dict[str, TrackedPosition] = {}

    def record_fill(self, ticker: str, side: str, price: float, quantity: float, action: str):
        if quantity <= 0:
            return
        if ticker in self._positions:
            pos = self._positions[ticker]
            total_cost = pos.avg_price * pos.quantity + price * quantity
            pos.quantity += quantity
            pos.avg_price = total_cost / pos.quantity
        else:
            self._positions[ticker] = TrackedPosition(
                ticker=ticker,
                side=side,
                quantity=quantity,
                avg_price=price,
            )
        logger.info(f"Fill: {action} {quantity}x {ticker} @ {price:.4f}")

    def get_position(self, ticker: str) -> TrackedPosition | None:
        return self._positions.get(ticker)

    def open_positions(self) -> list[TrackedPosition]:
        return [p for p in self._positions.values() if p.quantity > 0]

    def calculate_event_pnl(self, tickers: list[str]) -> dict:
        # Assumes equal fill quantities across all legs; inaccurate for partial fills
        total_premium = 0.0
        max_quantity = 0.0
        for t in tickers:
            pos = self._positions.get(t)
            if pos:
                total_premium += pos.avg_price * pos.quantity
                max_quantity = max(max_quantity, pos.quantity)
        max_payout = 1.0 * max_quantity
        return {
            "total_premium": total_premium,
            "max_payout": max_payout,
            "gross_profit": total_premium - max_payout,
        }
