from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Orderbook:
    yes_bids: dict[int, float] = field(default_factory=dict)
    no_bids: dict[int, float] = field(default_factory=dict)

    def best_yes_bid(self) -> float | None:
        if not self.yes_bids:
            return None
        return max(self.yes_bids) / 100.0

    def best_no_bid(self) -> float | None:
        if not self.no_bids:
            return None
        return max(self.no_bids) / 100.0

    def yes_bid_depth_at(self, price: float) -> float:
        return sum(
            qty for cents, qty in self.yes_bids.items()
            if cents >= round(price * 100)
        )


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str
    close_time: str = ""
    expected_expiration_time: str = ""
    volume_24h: float = 0.0
    open_interest: float = 0.0
    liquidity: float = 0.0


@dataclass
class Event:
    event_ticker: str
    title: str
    series_ticker: str
    mutually_exclusive: bool
    markets: list[Market] = field(default_factory=list)

    def market_tickers(self) -> list[str]:
        return [m.ticker for m in self.markets]


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"


@dataclass
class Order:
    order_id: str
    ticker: str
    action: str
    side: str
    price: float
    quantity: float
    status: OrderStatus
    filled_quantity: float = 0.0


@dataclass
class Position:
    ticker: str
    side: str
    quantity: float
    avg_price: float


@dataclass
class TradeSignal:
    event_ticker: str
    legs: list[tuple[str, float]]
    net_profit: float
    profit_pct: float
    exposure_ratio: float
    signal_type: str = "taker"
