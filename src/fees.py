def maker_fee(price: float) -> float:
    """Per-contract maker fee in dollars."""
    return 0.0175 * price * (1.0 - price)


def arb_profit(bid_prices: list[float]) -> float:
    """Per-contract net profit in dollars after fees."""
    gross = sum(bid_prices) - 1.0
    fees = sum(maker_fee(p) for p in bid_prices)
    return gross - fees


def exposure_ratio(bid_prices: list[float]) -> float:
    """Ratio of worst-case loss to net premium. Lower is safer."""
    premiums = sum(bid_prices)
    fees = sum(maker_fee(p) for p in bid_prices)
    net_premium = premiums - 1.0 - fees
    if net_premium <= 0:
        return float("inf")
    filled_fees = fees - maker_fee(max(bid_prices))
    worst_loss = max(0.0, 1.0 - (premiums - max(bid_prices)) + filled_fees)
    return worst_loss / net_premium
