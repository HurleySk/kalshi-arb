import logging
from datetime import datetime, timezone

from src.fees import arb_profit, exposure_ratio, maker_arb_profit, maker_exposure_ratio
from src.models import Orderbook, TradeSignal
from src.risk import RiskProfile

logger = logging.getLogger(__name__)


class ArbEngine:
    def __init__(self, risk_profile: RiskProfile):
        self.min_profit_pct = risk_profile.min_profit_pct
        self.max_exposure_ratio = risk_profile.max_exposure_ratio
        self.near_term_hours = risk_profile.near_term_hours
        self.hurdle_rate_annual_pct = risk_profile.hurdle_rate_annual_pct
        self.min_bid_depth = risk_profile.min_bid_depth
        self.min_volume_24h = risk_profile.min_volume_24h
        self.maker_max_exposure_ratio = 50.0

    def _days_to_expiry(self, market_metadata: dict[str, dict]) -> float | None:
        earliest = None
        for meta in market_metadata.values():
            close_str = meta.get("close_time", "")
            if not close_str:
                continue
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                days = (close_dt - datetime.now(timezone.utc)).total_seconds() / 86400
                if days > 0 and (earliest is None or days < earliest):
                    earliest = days
            except (ValueError, TypeError):
                continue
        return earliest

    def evaluate(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs = self._validate_legs(orderbooks, market_metadata)
        if legs is None:
            return None

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

    def _validate_legs(
        self,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> list[tuple[str, float]] | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            if self.min_bid_depth > 1:
                if book.yes_bid_depth_at(best_bid) < self.min_bid_depth:
                    return None
            legs.append((ticker, best_bid))

        if self.min_volume_24h > 0 and market_metadata:
            for ticker, _ in legs:
                meta = market_metadata.get(ticker, {})
                volume = meta.get("volume_24h", 0)
                if volume < self.min_volume_24h:
                    return None

        return legs

    def evaluate_maker(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs = self._validate_legs(orderbooks, market_metadata)
        if legs is None:
            return None

        bid_prices = [price for _, price in legs]
        profit = maker_arb_profit(bid_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100

        if profit_pct < self.min_profit_pct:
            return None

        # Maker ties up capital — require known expiry and apply hurdle rate
        if not market_metadata:
            return None
        days = self._days_to_expiry(market_metadata)
        if days is None:
            return None
        if days > self.near_term_hours / 24:
            annualized = profit_pct * (365 / days)
            if annualized < self.hurdle_rate_annual_pct:
                return None

        exp_ratio = maker_exposure_ratio(bid_prices)
        if exp_ratio > self.maker_max_exposure_ratio:
            return None

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
            signal_type="maker",
        )
