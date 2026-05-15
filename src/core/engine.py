import logging

from src.core.models import Orderbook, TradeSignal
from src.core.risk import RiskProfile
from src.core.fees import maker_arb_profit, maker_exposure_ratio
from src.ports.fee_model import FeeModel
from src.ports.constraints import PositionConstraints
from src.strategies import taker, near_expiry, monotone

logger = logging.getLogger(__name__)


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
            if depth < self.risk_profile.min_bid_depth:
                return None
            vol = meta.get("volume_24h", 0)
            if vol < self.risk_profile.maker_min_volume_24h:
                return None

        bid_prices = [p for _, p in legs]
        profit = maker_arb_profit(bid_prices, self.fee_model)
        if profit <= 0:
            return None

        profit_pct = profit * 100.0
        exp_ratio = maker_exposure_ratio(bid_prices, self.fee_model)

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
        min_spread = self.risk_profile.two_sided_min_spread_cents + 2
        if spread_cents < min_spread:
            return None

        our_bid = best_bid + 0.01
        our_ask = best_ask - 0.01

        return TradeSignal(
            event_ticker=ticker,
            legs=[(ticker, our_bid), (ticker, our_ask)],
            net_profit=our_ask - our_bid,
            profit_pct=(our_ask - our_bid) * 100.0,
            exposure_ratio=0.0,
            signal_type="two_sided",
            leg_actions=["buy", "sell"],
        )
