import logging
from datetime import datetime, timezone

from src.core.models import Orderbook, TradeSignal
from src.core.risk import RiskProfile
from src.core.fees import maker_arb_profit, maker_exposure_ratio
from src.ports.fee_model import FeeModel
from src.ports.constraints import PositionConstraints
from src.strategies import taker, near_expiry, monotone

logger = logging.getLogger(__name__)

MAKER_MAX_EXPOSURE_RATIO = 50.0


class ArbEngine:
    def __init__(
        self,
        fee_model: FeeModel,
        risk_profile: RiskProfile,
        constraints: PositionConstraints | None = None,
        maker_max_horizon_hours: float = 4.0,
        max_contracts_per_arb: int = 1,
        recorder=None,
    ):
        self.fee_model = fee_model
        self.risk_profile = risk_profile
        self.constraints = constraints
        self.maker_max_horizon_hours = maker_max_horizon_hours
        self.max_contracts_per_arb = max_contracts_per_arb
        self.recorder = recorder

    def evaluate(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        return taker.evaluate_sell_side(
            event_ticker, orderbooks, market_metadata,
            self.fee_model, self.risk_profile,
            recorder=self.recorder,
            max_contracts_per_arb=self.max_contracts_per_arb,
        )

    def evaluate_buy_side(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
        expected_market_count: int | None = None,
    ) -> TradeSignal | None:
        return taker.evaluate_buy_side(
            event_ticker, orderbooks, market_metadata,
            self.fee_model, self.risk_profile,
            expected_market_count=expected_market_count,
            recorder=self.recorder,
            max_contracts_per_arb=self.max_contracts_per_arb,
        )

    def evaluate_near_expiry(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        return near_expiry.evaluate(
            event_ticker, orderbooks, market_metadata,
            self.fee_model, self.risk_profile,
            recorder=self.recorder,
            max_contracts_per_arb=self.max_contracts_per_arb,
        )

    def evaluate_monotone_pair(
        self,
        upper_ticker: str,
        upper_book: Orderbook,
        lower_ticker: str,
        lower_book: Orderbook,
    ) -> TradeSignal | None:
        return monotone.evaluate(
            upper_ticker, upper_book,
            lower_ticker, lower_book,
            self.fee_model,
            min_profit_pct=self.risk_profile.min_profit_pct,
        )

    def evaluate_maker(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        if market_metadata is None:
            return None

        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            bid = book.best_bid()
            if bid is None:
                return None
            legs.append((ticker, bid))

        for ticker, price in legs:
            meta = market_metadata.get(ticker, {})
            depth = orderbooks[ticker].bid_depth_at(price)
            if depth < self.risk_profile.min_bid_depth:
                return None
            vol = meta.get("volume_24h", 0)
            if vol < self.risk_profile.maker_min_volume_24h:
                return None
            if self.risk_profile.min_open_interest > 0:
                if meta.get("open_interest", 0) < self.risk_profile.min_open_interest:
                    return None
            if self.risk_profile.min_liquidity > 0:
                if meta.get("liquidity", 0) < self.risk_profile.min_liquidity:
                    return None

        if self.risk_profile.min_ask_depth >= 1:
            for ticker, _ in legs:
                book = orderbooks[ticker]
                best_ask = book.best_ask()
                if best_ask is None:
                    return None
                if book.ask_depth_at(best_ask) < self.risk_profile.min_ask_depth:
                    return None

        bid_prices = [p for _, p in legs]
        bid_sum = sum(bid_prices)
        profit = maker_arb_profit(bid_prices, self.fee_model)
        if profit <= 0:
            if bid_sum >= 0.95:
                logger.debug("maker near-miss %s: bid_sum=%.4f", event_ticker, bid_sum)
            return None

        profit_pct = profit * 100.0
        if profit_pct < self.risk_profile.min_profit_pct:
            return None

        event_meta = {t: market_metadata.get(t, {}) for t, _ in legs}
        days = _days_to_expiry(event_meta)
        if days is None:
            return None
        if days > self.maker_max_horizon_hours / 24:
            logger.debug(
                "maker horizon-filtered %s: bid_sum=%.4f profit_pct=%.1f%% closes_in=%.1fh horizon=%.1fh",
                event_ticker, bid_sum, profit_pct, days * 24, self.maker_max_horizon_hours,
            )
            return None
        if days > self.risk_profile.near_term_hours / 24:
            annualized = profit_pct * (365 / days)
            if annualized < self.risk_profile.hurdle_rate_annual_pct:
                return None

        exp_ratio = maker_exposure_ratio(bid_prices, self.fee_model)
        if exp_ratio > MAKER_MAX_EXPOSURE_RATIO:
            return None

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
            signal_type="maker",
        )

    def evaluate_two_sided(
        self,
        ticker: str,
        book: Orderbook,
        volume_24h: float = 0.0,
    ) -> TradeSignal | None:
        if self.risk_profile.two_sided_max_inventory <= 0:
            return None
        if volume_24h < self.risk_profile.two_sided_min_volume_24h:
            return None

        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return None

        spread_cents = round((best_ask - best_bid) * 100)
        if spread_cents < self.risk_profile.two_sided_min_spread_cents + 2:
            return None

        post_bid = round(best_bid + 0.01, 2)
        post_ask = round(best_ask - 0.01, 2)

        if post_bid >= post_ask:
            return None

        return TradeSignal(
            event_ticker=ticker,
            legs=[(ticker, post_bid), (ticker, post_ask)],
            net_profit=round(post_ask - post_bid, 4),
            profit_pct=round((post_ask - post_bid) * 100, 2),
            exposure_ratio=0.0,
            signal_type="two_sided",
            quantity=1,
            leg_actions=["buy", "sell"],
        )


def _days_to_expiry(market_metadata: dict[str, dict]) -> float | None:
    now = datetime.now(timezone.utc)
    earliest = None
    for meta in market_metadata.values():
        close_str = meta.get("close_time", "")
        if not close_str:
            continue
        try:
            close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            days = (close_dt - now).total_seconds() / 86400
            if days > 0 and (earliest is None or days < earliest):
                earliest = days
        except (ValueError, TypeError):
            continue
    return earliest
