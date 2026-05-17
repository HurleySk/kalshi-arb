import logging

from src.core.models import Orderbook, TradeSignal
from src.core.fees import monotone_pair_profit
from src.ports.fee_model import FeeModel

logger = logging.getLogger(__name__)


def evaluate(
    upper_ticker: str,
    upper_book: Orderbook,
    lower_ticker: str,
    lower_book: Orderbook,
    fee_model: FeeModel,
    min_profit_pct: float = 0.0,
) -> TradeSignal | None:
    upper_bid = upper_book.best_bid()
    lower_ask = lower_book.best_ask()
    if upper_bid is None or lower_ask is None:
        return None

    profit = monotone_pair_profit(upper_bid, lower_ask, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < min_profit_pct:
        return None

    return TradeSignal(
        event_ticker=f"{upper_ticker}|{lower_ticker}",
        legs=[(upper_ticker, upper_bid), (lower_ticker, lower_ask)],
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="monotone",
        leg_actions=["sell", "buy"],
    )
