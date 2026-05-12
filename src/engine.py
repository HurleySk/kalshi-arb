import logging

from src.fees import arb_profit, exposure_ratio
from src.models import Orderbook, TradeSignal

logger = logging.getLogger(__name__)


class ArbEngine:
    def __init__(self, min_profit_pct: float, max_exposure_ratio: float):
        self.min_profit_pct = min_profit_pct
        self.max_exposure_ratio = max_exposure_ratio

    def evaluate(self, event_ticker: str, orderbooks: dict[str, Orderbook]) -> TradeSignal | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            legs.append((ticker, best_bid))

        bid_prices = [price for _, price in legs]
        profit = arb_profit(bid_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100
        if profit_pct < self.min_profit_pct:
            return None

        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
        )
