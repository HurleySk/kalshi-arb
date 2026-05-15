from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ports.fee_model import FeeModel


def taker_fee(price: float, fee_model: FeeModel) -> float:
    return fee_model.taker_fee(price)


def arb_profit(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.taker_fee(p) for p in bid_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def maker_arb_profit(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.maker_fee(p) for p in bid_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def maker_exposure_ratio(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.maker_fee(p) for p in bid_prices)
    net_premium = gross - fees
    if net_premium <= 0:
        return float("inf")
    worst_loss = max(0.0, 1.0 - (sum(bid_prices) - max(bid_prices)))
    return worst_loss / net_premium


def monotone_pair_profit(upper_bid: float, lower_ask: float, fee_model: FeeModel) -> float:
    gross = upper_bid - lower_ask
    fees = fee_model.taker_fee(upper_bid) + fee_model.taker_fee(lower_ask)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def buy_side_arb_profit(ask_prices: list[float], fee_model: FeeModel) -> float:
    gross = 1.0 - sum(ask_prices)
    fees = sum(fee_model.taker_fee(p) for p in ask_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def exposure_ratio(bid_prices: list[float], fee_model: FeeModel) -> float:
    premiums = sum(bid_prices)
    fees = sum(fee_model.taker_fee(p) for p in bid_prices)
    net_premium = premiums - 1.0 - fees
    if net_premium <= 0:
        return float("inf")
    filled_fees = fees - fee_model.taker_fee(max(bid_prices))
    worst_loss = max(0.0, 1.0 - (premiums - max(bid_prices)) + filled_fees)
    return worst_loss / net_premium
