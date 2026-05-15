from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Orderbook:
    bids: dict[int, float] = field(default_factory=dict)
    asks: dict[int, float] = field(default_factory=dict)

    def best_bid(self) -> float | None:
        if not self.bids:
            return None
        return max(self.bids) / 100.0

    def best_ask(self) -> float | None:
        if not self.asks:
            return None
        return min(self.asks) / 100.0

    def bid_depth_at(self, price: float) -> float:
        return sum(
            qty for cents, qty in self.bids.items()
            if cents >= round(price * 100)
        )

    def ask_depth_at(self, price: float) -> float:
        return sum(
            qty for cents, qty in self.asks.items()
            if cents <= round(price * 100)
        )


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str
    close_time: str = ""
    expected_expiration_time: str = ""
    exchange: str = "kalshi"
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
    total_market_count: int = 0
    exchange: str = "kalshi"

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
class Fill:
    order_id: str
    ticker: str
    price: float
    quantity: int
    side: str
    exchange: str
    timestamp: float


@dataclass
class TradeSignal:
    event_ticker: str
    legs: list[tuple[str, float]]
    net_profit: float
    profit_pct: float
    exposure_ratio: float
    signal_type: str = "taker"
    quantity: int = 1
    leg_actions: list[str] | None = None

    def __post_init__(self):
        if self.leg_actions is not None and len(self.leg_actions) != len(self.legs):
            raise ValueError(
                f"leg_actions length {len(self.leg_actions)} must match legs length {len(self.legs)}"
            )
