import logging

from src.core.models import Orderbook, TradeSignal
from src.core.fees import arb_profit, exposure_ratio
from src.core.risk import RiskProfile
from src.ports.fee_model import FeeModel

logger = logging.getLogger(__name__)


def evaluate(
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None,
    fee_model: FeeModel,
    risk_profile: RiskProfile,
    recorder=None,
    max_contracts_per_arb: int = 1,
) -> TradeSignal | None:
    if market_metadata is None:
        market_metadata = {}

    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        bid = book.best_bid()
        if bid is None:
            return None
        legs.append((ticker, bid))

    for ticker, price in legs:
        meta = market_metadata.get(ticker, {})
        depth = orderbooks[ticker].bid_depth_at(price)
        if depth < risk_profile.near_expiry_min_bid_depth:
            return None
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.near_expiry_min_volume_24h:
            return None

    bid_prices = [p for _, p in legs]
    profit = arb_profit(bid_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.near_expiry_min_profit_pct:
        return None

    exp_ratio = exposure_ratio(bid_prices, fee_model)
    if exp_ratio > risk_profile.max_exposure_ratio:
        return None

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=exp_ratio,
        signal_type="near_expiry_taker",
        quantity=max_contracts_per_arb,
    )
