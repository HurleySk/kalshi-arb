import math
from src.fees import maker_fee, arb_profit, exposure_ratio


def test_maker_fee_at_50_cents():
    fee = maker_fee(0.50)
    assert math.isclose(fee, 0.004375, abs_tol=1e-6)


def test_maker_fee_at_extremes():
    assert math.isclose(maker_fee(0.01), 0.0175 * 0.01 * 0.99, abs_tol=1e-6)
    assert math.isclose(maker_fee(0.99), 0.0175 * 0.99 * 0.01, abs_tol=1e-6)


def test_maker_fee_at_zero_and_one():
    assert maker_fee(0.0) == 0.0
    assert maker_fee(1.0) == 0.0


def test_arb_profit_basic():
    prices = [0.30, 0.25, 0.25, 0.25]
    profit = arb_profit(prices)
    gross = sum(prices) - 1.0  # 0.05
    fees = sum(maker_fee(p) for p in prices)
    assert math.isclose(profit, gross - fees, abs_tol=1e-6)
    assert profit > 0


def test_arb_profit_no_opportunity():
    prices = [0.25, 0.25, 0.25, 0.25]
    profit = arb_profit(prices)
    assert profit < 0


def test_exposure_ratio_basic():
    prices = [0.30, 0.25, 0.25, 0.25]
    ratio = exposure_ratio(prices)
    assert ratio > 0
    assert not math.isinf(ratio)


def test_exposure_ratio_no_opportunity():
    prices = [0.25, 0.25, 0.25, 0.25]
    ratio = exposure_ratio(prices)
    assert math.isinf(ratio)


def test_exposure_ratio_safe_arb():
    prices = [0.60, 0.55, 0.50]
    ratio = exposure_ratio(prices)
    assert math.isclose(ratio, 0.0, abs_tol=1e-6)
