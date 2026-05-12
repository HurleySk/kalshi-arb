from dataclasses import dataclass, field
from enum import Enum


@dataclass
class OrderbookLevel:
    price: float
    quantity: float


@dataclass
class Orderbook:
    yes_bids: list[OrderbookLevel] = field(default_factory=list)
    no_bids: list[OrderbookLevel] = field(default_factory=list)

    def best_yes_bid(self) -> float | None:
        if not self.yes_bids:
            return None
        return max(level.price for level in self.yes_bids)

    def best_no_bid(self) -> float | None:
        if not self.no_bids:
            return None
        return max(level.price for level in self.no_bids)


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str


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
