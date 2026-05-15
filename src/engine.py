import logging
from datetime import datetime, timezone

from src.fees import arb_profit, buy_side_arb_profit, exposure_ratio, maker_arb_profit, maker_exposure_ratio, monotone_pair_profit
from src.models import Orderbook, TradeSignal
from src.risk import RiskProfile

logger = logging.getLogger(__name__)


class ArbEngine:
    def __init__(self, risk_profile: RiskProfile, maker_max_horizon_hours: float = 4.0,
                 max_contracts_per_arb: int = 1, recorder=None):
        self.recorder = recorder
        self.min_profit_pct = risk_profile.min_profit_pct
        self.max_exposure_ratio = risk_profile.max_exposure_ratio
        self.near_term_hours = risk_profile.near_term_hours
        self.hurdle_rate_annual_pct = risk_profile.hurdle_rate_annual_pct
        self.min_bid_depth = risk_profile.min_bid_depth
        self.min_ask_depth = risk_profile.min_ask_depth
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
        self.min_buy_side_coverage = risk_profile.min_buy_side_coverage
        self.maker_min_volume_24h = risk_profile.maker_min_volume_24h

    def _record_near_miss(self, event_ticker: str, strategy: str, bid_sum: float,
                          legs: list[tuple[str, float, float]], reject_reason: str | None = None):
        if not self.recorder:
            return
        self.recorder.record_signal(
            event_ticker=event_ticker, strategy=strategy, outcome="near_miss",
            reject_reason=reject_reason, bid_sum=bid_sum, ask_sum=None,
            profit_pct=None, exposure_ratio=None,
            legs=[{"ticker": t, "price": p, "depth": d} for t, p, d in legs],
            metadata=None,
        )

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

        bid_prices = [price for _, price, _ in legs]
        bid_sum = sum(bid_prices)
        profit = arb_profit(bid_prices)
        if profit <= 0:
            if 0.97 <= bid_sum < 1.00:  # below $1.00 means maker can't profit either
                logger.debug("taker near-miss %s: bid_sum=%.4f", event_ticker, bid_sum)
                self._record_near_miss(event_ticker, "taker", bid_sum, legs)
            return None

        profit_pct = (profit / 1.0) * 100

        if profit_pct < self.min_profit_pct:
            return None

        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        depths = [d for _, _, d in legs]
        quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

        return TradeSignal(
            event_ticker=event_ticker,
            legs=[(t, p) for t, p, _ in legs],
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
        min_bid_depth: int | None = None,
        min_volume_24h: float | None = None,
        min_ask_depth: int | None = None,
        strategy: str = "taker",
    ) -> list[tuple[str, float, float]] | None:
        legs: list[tuple[str, float, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            depth = book.yes_bid_depth_at(best_bid)
            legs.append((ticker, best_bid, depth))

        bid_sum = sum(p for _, p, _ in legs)
        near_miss = bid_sum >= 0.97  # within striking distance of taker threshold

        effective_min_depth = min_bid_depth if min_bid_depth is not None else self.min_bid_depth
        if effective_min_depth > 1:
            for ticker, best_bid, depth in legs:
                if depth < effective_min_depth:
                    if near_miss and event_ticker:
                        logger.debug(
                            "near-miss %s: bid_sum=%.4f blocked — %s depth < min %d",
                            event_ticker, bid_sum, ticker, effective_min_depth,
                        )
                        self._record_near_miss(event_ticker, strategy, bid_sum, legs, "depth_filter")
                    return None

        effective_min_ask_depth = min_ask_depth if min_ask_depth is not None else self.min_ask_depth
        if effective_min_ask_depth >= 1:
            for ticker, best_bid, depth in legs:
                book = orderbooks[ticker]
                best_ask = book.best_yes_ask()
                if best_ask is None:
                    if near_miss and event_ticker:
                        logger.debug(
                            "near-miss %s: bid_sum=%.4f blocked — %s no ask (one-sided market)",
                            event_ticker, bid_sum, ticker,
                        )
                        self._record_near_miss(event_ticker, strategy, bid_sum, legs, "no_ask")
                    return None
                ask_depth = book.yes_ask_depth_at(best_ask)
                if ask_depth < effective_min_ask_depth:
                    if near_miss and event_ticker:
                        logger.debug(
                            "near-miss %s: bid_sum=%.4f blocked — %s ask_depth %.0f < min %d",
                            event_ticker, bid_sum, ticker, ask_depth, effective_min_ask_depth,
                        )
                        self._record_near_miss(event_ticker, strategy, bid_sum, legs, "ask_depth_filter")
                    return None

        effective_min_volume = min_volume_24h if min_volume_24h is not None else self.min_volume_24h
        if effective_min_volume > 0 and market_metadata:
            for ticker, _, _ in legs:
                volume = market_metadata.get(ticker, {}).get("volume_24h", 0)
                if volume < effective_min_volume:
                    if near_miss and event_ticker:
                        logger.debug(
                            "near-miss %s: bid_sum=%.4f blocked — %s volume %.0f < min %.0f",
                            event_ticker, bid_sum, ticker, volume, effective_min_volume,
                        )
                        self._record_near_miss(event_ticker, strategy, bid_sum, legs, "volume_filter")
                    return None

        if self.min_open_interest > 0 and market_metadata:
            for ticker, _, _ in legs:
                if market_metadata.get(ticker, {}).get("open_interest", 0) < self.min_open_interest:
                    return None

        if self.min_liquidity > 0 and market_metadata:
            for ticker, _, _ in legs:
                if market_metadata.get(ticker, {}).get("liquidity", 0) < self.min_liquidity:
                    return None

        return legs

    def evaluate_maker(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs = self._validate_legs(
            orderbooks, market_metadata, event_ticker=event_ticker,
            min_volume_24h=self.maker_min_volume_24h, strategy="maker",
        )
        if legs is None:
            return None

        bid_prices = [price for _, price, _ in legs]
        bid_sum = sum(bid_prices)
        profit = maker_arb_profit(bid_prices)
        if profit <= 0:
            if bid_sum >= 0.95:
                logger.debug("maker near-miss %s: bid_sum=%.4f", event_ticker, bid_sum)
                self._record_near_miss(event_ticker, "maker", bid_sum, legs)
            return None

        profit_pct = (profit / 1.0) * 100

        if profit_pct < self.min_profit_pct:
            return None

        # Maker ties up capital — require known expiry within horizon
        if market_metadata is None:
            return None
        days = self._days_to_expiry({t: market_metadata.get(t, {}) for t, _, _ in legs})
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
            legs=[(t, p) for t, p, _ in legs],
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
        legs = self._validate_legs(
            orderbooks, market_metadata, event_ticker=event_ticker,
            min_bid_depth=self.near_expiry_min_bid_depth,
            min_volume_24h=self.near_expiry_min_volume_24h,
            strategy="near_expiry",
        )
        if legs is None:
            return None

        bid_prices = [price for _, price, _ in legs]
        profit = arb_profit(bid_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100
        if profit_pct < self.near_expiry_min_profit_pct:
            return None

        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        depths = [d for _, _, d in legs]
        quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

        return TradeSignal(
            event_ticker=event_ticker,
            legs=[(t, p) for t, p, _ in legs],
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
        expected_market_count: int | None = None,
    ) -> TradeSignal | None:
        if expected_market_count is not None and len(orderbooks) < expected_market_count:
            logger.debug(
                "buy-side incomplete %s: have %d orderbooks, expected %d registered",
                event_ticker, len(orderbooks), expected_market_count,
            )
            return None

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

        floor = self.min_buy_side_coverage
        if floor > 0 and ask_sum < floor:
            logger.debug(
                "buy-side coverage-floor-filtered %s: ask_sum=%.4f < min_buy_side_coverage=%.2f",
                event_ticker, ask_sum, floor,
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
