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

    legs: list[tuple[str, float, float]] = []
    for ticker, book in orderbooks.items():
        bid = book.best_bid()
        if bid is None:
            return None
        depth = book.bid_depth_at(bid)
        legs.append((ticker, bid, depth))

    for ticker, price, depth in legs:
        if depth < risk_profile.min_bid_depth:
            _log_near_miss(event_ticker, "taker", ticker, "depth", depth, risk_profile.min_bid_depth)
            return None
        meta = market_metadata.get(ticker, {})
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.min_volume_24h:
            _log_near_miss(event_ticker, "taker", ticker, "volume", vol, risk_profile.min_volume_24h)
            return None

    bid_prices = [p for _, p, _ in legs]
    bid_sum = sum(bid_prices)
    profit = arb_profit(bid_prices, fee_model)
    if profit <= 0:
        if 0.97 <= bid_sum < 1.00:
            logger.debug("taker near-miss %s: bid_sum=%.4f", event_ticker, bid_sum)
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.min_profit_pct:
        return None

    exp_ratio = exposure_ratio(bid_prices, fee_model)
    if exp_ratio > risk_profile.max_exposure_ratio:
        return None

    depths = [d for _, _, d in legs]
    quantity = max(1, min(int(min(depths)), max_contracts_per_arb))

    return TradeSignal(
        event_ticker=event_ticker,
        legs=[(t, p) for t, p, _ in legs],
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=exp_ratio,
        signal_type="taker",
        quantity=quantity,
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

    if expected_market_count is not None and len(orderbooks) < expected_market_count:
        return None

    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        ask = book.best_ask()
        if ask is None or ask < 0.01:
            return None
        legs.append((ticker, ask))

    ask_prices = [p for _, p in legs]
    ask_sum = sum(ask_prices)
    max_ask = max(ask_prices)

    if ask_sum < 0.60 or max_ask < 0.20:
        logger.debug(
            "buy-side coverage-filtered %s: ask_sum=%.4f max_ask=%.4f",
            event_ticker, ask_sum, max_ask,
        )
        return None

    floor = risk_profile.min_buy_side_coverage
    if floor > 0 and ask_sum < floor:
        logger.debug(
            "buy-side coverage-floor-filtered %s: ask_sum=%.4f < min_buy_side_coverage=%.2f",
            event_ticker, ask_sum, floor,
        )
        return None

    if risk_profile.buy_side_max_horizon_hours > 0 and market_metadata and legs:
        first_ticker = legs[0][0]
        close_str = market_metadata.get(first_ticker, {}).get("close_time", "")
        if close_str:
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                hours = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours > risk_profile.buy_side_max_horizon_hours:
                    logger.debug(
                        "buy-side horizon-filtered %s: closes_in=%.1fh limit=%.1fh",
                        event_ticker, hours, risk_profile.buy_side_max_horizon_hours,
                    )
                    return None
            except (ValueError, TypeError):
                pass

    if risk_profile.min_bid_depth > 1:
        for ticker, ask_price in legs:
            if orderbooks[ticker].ask_depth_at(ask_price) < risk_profile.min_bid_depth:
                return None

    if risk_profile.min_volume_24h > 0 and market_metadata:
        for ticker, _ in legs:
            if market_metadata.get(ticker, {}).get("volume_24h", 0) < risk_profile.min_volume_24h:
                return None

    if risk_profile.min_open_interest > 0 and market_metadata:
        for ticker, _ in legs:
            if market_metadata.get(ticker, {}).get("open_interest", 0) < risk_profile.min_open_interest:
                return None

    if risk_profile.min_liquidity > 0 and market_metadata:
        for ticker, _ in legs:
            if market_metadata.get(ticker, {}).get("liquidity", 0) < risk_profile.min_liquidity:
                return None

    profit = buy_side_arb_profit(ask_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.min_profit_pct:
        return None

    depths = [orderbooks[ticker].ask_depth_at(price) for ticker, price in legs]
    quantity = max(1, min(int(min(depths)), max_contracts_per_arb))

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="buy_side_taker",
        quantity=quantity,
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
            if days > 0 and (earliest is None or days < earliest):
                earliest = days
        except (ValueError, TypeError):
            continue
    return earliest


def _log_near_miss(event_ticker, strategy, ticker, filter_name, actual, threshold):
    logger.debug(
        "near-miss %s: bid_sum blocked — %s %s/%s < min %s",
        event_ticker, ticker, filter_name, actual, threshold,
    )
