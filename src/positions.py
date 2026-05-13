import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    ticker: str
    side: str
    quantity: float
    avg_price: float
    opened_by: str = "sell"  # "sell" = short (sell-side arb), "buy" = long (buy-side arb)


class PositionTracker:
    def __init__(self):
        self._positions: dict[str, TrackedPosition] = {}
        self.realized_pnl: float = 0.0

    def record_fill(self, ticker: str, side: str, price: float, quantity: float, action: str):
        if quantity <= 0:
            return

        pos = self._positions.get(ticker)

        if pos is None:
            # Opening a new position in whichever direction
            self._positions[ticker] = TrackedPosition(
                ticker=ticker, side=side, quantity=quantity, avg_price=price, opened_by=action
            )
            logger.info("Fill: %s %dx %s @ %.4f (open)", action, quantity, ticker, price)
            return

        if pos.opened_by == action:
            # Same direction as original fill — average into the position
            total_cost = pos.avg_price * pos.quantity + price * quantity
            pos.quantity += quantity
            pos.avg_price = total_cost / pos.quantity
            logger.info("Fill: %s %dx %s @ %.4f (add)", action, quantity, ticker, price)
        else:
            # Opposite direction — close/reduce the position
            closed_qty = min(quantity, pos.quantity)
            if pos.opened_by == "sell":
                pnl = (pos.avg_price - price) * closed_qty   # sold high, buying low
            else:
                pnl = (price - pos.avg_price) * closed_qty   # bought low, selling high
            self.realized_pnl += pnl
            pos.quantity -= closed_qty
            if pos.quantity <= 0:
                del self._positions[ticker]
            logger.info("Close: %s %dx %s @ %.4f (realized: $%.4f)", action, quantity, ticker, price, pnl)

    def load_position(self, ticker: str, side: str, quantity: float) -> None:
        """Load a long position fetched from the exchange on startup.

        Only call with quantity > 0 (long positions). avg_price is set to 0.0 because
        the exchange does not return cost basis; realized P&L for this position will be
        overstated when the unwind sell arrives.
        """
        assert quantity > 0, f"load_position only accepts long positions (qty > 0), got {quantity}"
        self._positions[ticker] = TrackedPosition(
            ticker=ticker, side=side, quantity=quantity, avg_price=0.0, opened_by="buy"
        )
        logger.info("Boot: loaded position %s qty=%.0f (cost basis unknown — P&L will be overstated on close)", ticker, quantity)

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
