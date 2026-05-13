import logging
from datetime import datetime, timezone

from src.fees import arb_profit, buy_side_arb_profit, exposure_ratio, maker_arb_profit, maker_exposure_ratio, monotone_pair_profit
from src.models import Orderbook, TradeSignal
from src.risk import RiskProfile

logger = logging.getLogger(__name__)


class ArbEngine:
    def __init__(self, risk_profile: RiskProfile, maker_max_horizon_hours: float = 4.0,
                 max_contracts_per_arb: int = 1):
        self.min_profit_pct = risk_profile.min_profit_pct
        self.max_exposure_ratio = risk_profile.max_exposure_ratio
        self.near_term_hours = risk_profile.near_term_hours
        self.hurdle_rate_annual_pct = risk_profile.hurdle_rate_annual_pct
        self.min_bid_depth = risk_profile.min_bid_depth
        self.min_volume_24h = risk_profile.min_volume_24h
        self.maker_max_exposure_ratio = 50.0
        self.maker_max_horizon_hours = maker_max_horizon_hours
        self.max_contracts_per_arb = max_contracts_per_arb
        self.min_open_interest = risk_profile.min_open_interest
        self.min_liquidity = risk_profile.min_liquidity
        self.near_expiry_min_profit_pct = risk_profile.near_expiry_min_profit_pct
        self.near_expiry_min_bid_depth = risk_profile.near_expiry_min_bid_depth
        self.near_expiry_min_volume_24h = risk_profile.near_expiry_min_volume_24h
        self.two_sided_min_spread_cents = risk_profile.two_sided_min_spread_cents
        self.two_sided_max_inventory = risk_profile.two_sided_max_inventory
        self.two_sided_min_volume_24h = risk_profile.two_sided_min_volume_24h
        self.buy_side_max_horizon_hours = risk_profile.buy_side_max_horizon_hours

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
        legs = self._validate_legs(orderbooks, market_metadata, event_ticker=event_ticker)
        if legs is None:
            return None

        bid_prices = [price for _, price in legs]
        bid_sum = sum(bid_prices)
        profit = arb_profit(bid_prices)
        if profit <= 0:
            if 0.97 <= bid_sum < 1.00:  # below $1.00 means maker can't profit either
                logger.debug("taker near-miss %s: bid_sum=%.4f", event_ticker, bid_sum)
            return None

        profit_pct = (profit / 1.0) * 100

        if profit_pct < self.min_profit_pct:
            return None

        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        depths = [orderbooks[ticker].yes_bid_depth_at(price) for ticker, price in legs]
        quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
            quantity=quantity,
        )

    def _validate_legs(
        self,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
        event_ticker: str | None = None,
    ) -> list[tuple[str, float]] | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            legs.append((ticker, best_bid))

        bid_sum = sum(p for _, p in legs)
        near_miss = bid_sum >= 0.97  # within striking distance of taker threshold

        if self.min_bid_depth > 1:
            for ticker, best_bid in legs:
                if orderbooks[ticker].yes_bid_depth_at(best_bid) < self.min_bid_depth:
                    if near_miss and event_ticker:
                        logger.debug(
                            "near-miss %s: bid_sum=%.4f blocked — %s depth < min %d",
                            event_ticker, bid_sum, ticker, self.min_bid_depth,
                        )
                    return None

        if self.min_volume_24h > 0 and market_metadata:
            for ticker, _ in legs:
                volume = market_metadata.get(ticker, {}).get("volume_24h", 0)
                if volume < self.min_volume_24h:
                    if near_miss and event_ticker:
                        logger.debug(
                            "near-miss %s: bid_sum=%.4f blocked — %s volume %.0f < min %.0f",
                            event_ticker, bid_sum, ticker, volume, self.min_volume_24h,
                        )
                    return None

        if self.min_open_interest > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("open_interest", 0) < self.min_open_interest:
                    return None

        if self.min_liquidity > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("liquidity", 0) < self.min_liquidity:
                    return None

        return legs

    def evaluate_maker(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs = self._validate_legs(orderbooks, market_metadata, event_ticker=event_ticker)
        if legs is None:
            return None

        bid_prices = [price for _, price in legs]
        bid_sum = sum(bid_prices)
        profit = maker_arb_profit(bid_prices)
        if profit <= 0:
            if bid_sum >= 0.95:
                logger.debug("maker near-miss %s: bid_sum=%.4f", event_ticker, bid_sum)
            return None

        profit_pct = (profit / 1.0) * 100

        if profit_pct < self.min_profit_pct:
            return None

        # Maker ties up capital — require known expiry within horizon
        if not market_metadata:
            return None
        days = self._days_to_expiry(market_metadata)
        if days is None:
            return None
        if days > self.maker_max_horizon_hours / 24:
            logger.debug(
                "maker horizon-filtered %s: bid_sum=%.4f profit_pct=%.1f%% closes_in=%.1fh horizon=%.1fh",
                event_ticker, bid_sum, profit_pct, days * 24, self.maker_max_horizon_hours,
            )
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

    def evaluate_monotone_pair(
        self,
        upper_ticker: str,
        upper_book: Orderbook,
        lower_ticker: str,
        lower_book: Orderbook,
    ) -> TradeSignal | None:
        upper_bid = upper_book.best_yes_bid()
        lower_ask = lower_book.best_yes_ask()
        if upper_bid is None or lower_ask is None:
            return None

        profit = monotone_pair_profit(upper_bid, lower_ask)
        if profit <= 0:
            return None

        profit_pct = profit * 100
        if profit_pct < self.min_profit_pct:
            return None

        return TradeSignal(
            event_ticker=f"{upper_ticker}|{lower_ticker}",
            legs=[(upper_ticker, upper_bid), (lower_ticker, lower_ask)],
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=0.0,
            signal_type="monotone",
            quantity=1,
            leg_actions=["sell", "buy"],
        )

    def evaluate_near_expiry(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            legs.append((ticker, best_bid))

        bid_prices = [price for _, price in legs]

        if self.near_expiry_min_bid_depth > 1:
            for ticker, best_bid in legs:
                if orderbooks[ticker].yes_bid_depth_at(best_bid) < self.near_expiry_min_bid_depth:
                    return None

        if self.near_expiry_min_volume_24h > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("volume_24h", 0) < self.near_expiry_min_volume_24h:
                    return None

        if self.min_open_interest > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("open_interest", 0) < self.min_open_interest:
                    return None

        if self.min_liquidity > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("liquidity", 0) < self.min_liquidity:
                    return None

        profit = arb_profit(bid_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100
        if profit_pct < self.near_expiry_min_profit_pct:
            return None

        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        depths = [orderbooks[ticker].yes_bid_depth_at(price) for ticker, price in legs]
        quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
            signal_type="near_expiry_taker",
            quantity=quantity,
        )

    def evaluate_buy_side(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_ask = book.best_yes_ask()
            if best_ask is None or best_ask < 0.01:
                return None
            legs.append((ticker, best_ask))

        ask_prices = [price for _, price in legs]
        ask_sum = sum(ask_prices)
        max_ask = max(ask_prices)
        # Two coverage guards — either alone can be fooled, together they're robust:
        #  1. ask_sum < 0.60: only low-prob outcomes registered (dominant bucket missing)
        #  2. max_ask < 0.20: no single outcome has meaningful probability registered
        if ask_sum < 0.60 or max_ask < 0.20:
            logger.debug(
                "buy-side coverage-filtered %s: ask_sum=%.4f max_ask=%.4f — likely missing high-probability outcome legs",
                event_ticker, ask_sum, max_ask,
            )
            return None

        if self.buy_side_max_horizon_hours > 0 and market_metadata and legs:
            first_ticker = legs[0][0]
            close_str = market_metadata.get(first_ticker, {}).get("close_time", "")
            if close_str:
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    hours_to_close = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_to_close > self.buy_side_max_horizon_hours:
                        logger.debug(
                            "buy-side horizon-filtered %s: closes_in=%.1fh limit=%.1fh",
                            event_ticker, hours_to_close, self.buy_side_max_horizon_hours,
                        )
                        return None
                except (ValueError, TypeError):
                    pass

        if self.min_bid_depth > 1:
            for ticker, ask_price in legs:
                if orderbooks[ticker].yes_ask_depth_at(ask_price) < self.min_bid_depth:
                    return None

        if self.min_volume_24h > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("volume_24h", 0) < self.min_volume_24h:
                    return None

        if self.min_open_interest > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("open_interest", 0) < self.min_open_interest:
                    return None

        if self.min_liquidity > 0 and market_metadata:
            for ticker, _ in legs:
                if market_metadata.get(ticker, {}).get("liquidity", 0) < self.min_liquidity:
                    return None

        profit = buy_side_arb_profit(ask_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100
        if profit_pct < self.min_profit_pct:
            return None

        depths = [orderbooks[ticker].yes_ask_depth_at(price) for ticker, price in legs]
        quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

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

    def evaluate_two_sided(
        self,
        ticker: str,
        book: Orderbook,
        volume_24h: float = 0.0,
    ) -> TradeSignal | None:
        if self.two_sided_max_inventory <= 0:
            return None
        if volume_24h < self.two_sided_min_volume_24h:
            return None

        best_bid = book.best_yes_bid()
        best_ask = book.best_yes_ask()
        if best_bid is None or best_ask is None:
            return None

        spread_cents = round((best_ask - best_bid) * 100)
        if spread_cents < self.two_sided_min_spread_cents + 2:
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
