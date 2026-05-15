# src/strategies/taker.py
import logging
from datetime import datetime, timezone

from src.core.models import Orderbook, TradeSignal
from src.core.fees import arb_profit, buy_side_arb_profit, exposure_ratio
from src.core.risk import RiskProfile
from src.ports.fee_model import FeeModel

logger = logging.getLogger(__name__)


def evaluate_sell_side(
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
        if depth < risk_profile.min_bid_depth:
            return None
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.min_volume_24h:
            return None

    bid_prices = [p for _, p in legs]
    profit = arb_profit(bid_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.min_profit_pct:
        return None

    exp_ratio = exposure_ratio(bid_prices, fee_model)
    if exp_ratio > risk_profile.max_exposure_ratio:
        return None

    days = _days_to_expiry(market_metadata)
    if days is not None and days > risk_profile.near_term_hours / 24.0:
        annualized = (profit_pct / days) * 365
        if annualized < risk_profile.hurdle_rate_annual_pct:
            return None

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=exp_ratio,
        signal_type="taker",
        quantity=max_contracts_per_arb,
    )


def evaluate_buy_side(
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None,
    fee_model: FeeModel,
    risk_profile: RiskProfile,
    expected_market_count: int | None = None,
    recorder=None,
    max_contracts_per_arb: int = 1,
) -> TradeSignal | None:
    if market_metadata is None:
        market_metadata = {}

    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        ask = book.best_ask()
        if ask is None:
            return None
        meta = market_metadata.get(ticker, {})
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.min_volume_24h:
            return None
        legs.append((ticker, ask))

    if expected_market_count is not None and len(legs) < expected_market_count:
        return None

    ask_prices = [p for _, p in legs]
    ask_sum = sum(ask_prices)
    max_ask = max(ask_prices)

    if ask_sum < 0.60:
        return None
    if max_ask < 0.20:
        return None
    if risk_profile.min_buy_side_coverage > 0 and ask_sum < risk_profile.min_buy_side_coverage:
        return None

    profit = buy_side_arb_profit(ask_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.min_profit_pct:
        return None

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="buy_side_taker",
        quantity=max_contracts_per_arb,
        leg_actions=["buy"] * len(legs),
    )


def _days_to_expiry(market_metadata: dict[str, dict]) -> float | None:
    now = datetime.now(timezone.utc)
    earliest = None
    for meta in market_metadata.values():
        close_str = meta.get("close_time", "")
        if not close_str:
            continue
        try:
            close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            days = (close - now).total_seconds() / 86400
            if earliest is None or days < earliest:
                earliest = days
        except (ValueError, TypeError):
            continue
    return earliest
