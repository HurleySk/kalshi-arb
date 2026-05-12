TAKER_FEE_RATE = 0.07


def taker_fee(price: float) -> float:
    """Per-contract taker fee in dollars."""
    return TAKER_FEE_RATE * price * (1.0 - price)


def arb_profit(bid_prices: list[float]) -> float:
    """Per-contract net profit in dollars after taker fees."""
    gross = sum(bid_prices) - 1.0
    fees = sum(taker_fee(p) for p in bid_prices)
    return gross - fees


def maker_arb_profit(bid_prices: list[float]) -> float:
    """Per-contract net profit as maker (0% fees)."""
    return sum(bid_prices) - 1.0


def maker_exposure_ratio(bid_prices: list[float]) -> float:
    """Exposure ratio for maker orders (0% fees)."""
    net_premium = sum(bid_prices) - 1.0
    if net_premium <= 0:
        return float("inf")
    worst_loss = max(0.0, 1.0 - (sum(bid_prices) - max(bid_prices)))
    return worst_loss / net_premium


def exposure_ratio(bid_prices: list[float]) -> float:
    """Ratio of worst-case loss to net premium. Lower is safer."""
    premiums = sum(bid_prices)
    fees = sum(taker_fee(p) for p in bid_prices)
    net_premium = premiums - 1.0 - fees
    if net_premium <= 0:
        return float("inf")
    filled_fees = fees - taker_fee(max(bid_prices))
    worst_loss = max(0.0, 1.0 - (premiums - max(bid_prices)) + filled_fees)
    return worst_loss / net_premium
