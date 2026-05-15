import math
from src.fees import taker_fee, arb_profit, exposure_ratio, maker_arb_profit, buy_side_arb_profit, monotone_pair_profit, TAKER_FEE_RATE


def test_taker_fee_at_50_cents():
    fee = taker_fee(0.50)
    assert math.isclose(fee, 0.07 * 0.50 * 0.50, abs_tol=1e-6)
    assert math.isclose(fee, 0.0175, abs_tol=1e-6)


def test_taker_fee_at_extremes():
    assert math.isclose(taker_fee(0.01), 0.07 * 0.01 * 0.99, abs_tol=1e-6)
    assert math.isclose(taker_fee(0.99), 0.07 * 0.99 * 0.01, abs_tol=1e-6)


def test_taker_fee_at_zero_and_one():
    assert taker_fee(0.0) == 0.0
    assert taker_fee(1.0) == 0.0


def test_taker_fee_rate_is_seven_percent():
    assert TAKER_FEE_RATE == 0.07


def test_arb_profit_basic():
    prices = [0.30, 0.25, 0.25, 0.25]
    profit = arb_profit(prices)
    gross = sum(prices) - 1.0  # 0.05
    fees = sum(taker_fee(p) for p in prices)
    assert math.isclose(profit, gross - fees, abs_tol=1e-6)
    # With taker fees (4x higher), this should still be marginally positive
    assert profit < 0  # 0.05 gross - 0.054 fees = negative with taker rate


def test_arb_profit_wide_spread():
    prices = [0.40, 0.35, 0.35]
    profit = arb_profit(prices)
    gross = sum(prices) - 1.0  # 0.10
    fees = sum(taker_fee(p) for p in prices)
    # fees = 0.07*(0.4*0.6 + 0.35*0.65 + 0.35*0.65) = 0.07*(0.24+0.2275+0.2275) = 0.04865
    assert math.isclose(profit, gross - fees, abs_tol=1e-6)
    assert profit > 0


def test_arb_profit_no_opportunity():
    prices = [0.25, 0.25, 0.25, 0.25]
    profit = arb_profit(prices)
    assert profit < 0


def test_exposure_ratio_no_opportunity():
    prices = [0.25, 0.25, 0.25, 0.25]
    ratio = exposure_ratio(prices)
    assert math.isinf(ratio)


def test_exposure_ratio_wide_spread():
    prices = [0.60, 0.55, 0.50]
    ratio = exposure_ratio(prices)
    assert ratio >= 0
    assert not math.isinf(ratio)


def test_maker_arb_profit_no_fees():
    profit = maker_arb_profit([0.55, 0.50])
    assert abs(profit - 0.05) < 1e-9


def test_maker_arb_profit_below_dollar():
    profit = maker_arb_profit([0.40, 0.50])
    assert profit < 0


def test_maker_arb_profit_three_legs():
    profit = maker_arb_profit([0.40, 0.35, 0.35])
    assert abs(profit - 0.10) < 1e-9


def test_buy_side_profit_positive_when_sum_below_one():
    # 3 legs at 30¢ each = 90¢ total, fees = 3 * 0.07 * 0.30 * 0.70 = 0.0441
    # profit = 1.0 - 0.90 - 0.0441 = 0.0559
    profit = buy_side_arb_profit([0.30, 0.30, 0.30])
    assert profit > 0


def test_buy_side_profit_negative_when_sum_above_one():
    profit = buy_side_arb_profit([0.40, 0.40, 0.40])
    assert profit < 0


def test_buy_side_profit_exact():
    asks = [0.30, 0.30, 0.30]
    expected = 1.0 - sum(asks) - sum(taker_fee(p) for p in asks)
    assert abs(buy_side_arb_profit(asks) - expected) < 1e-9


def test_monotone_pair_profit_positive_when_upper_bid_exceeds_lower_ask():
    # Sell upper at 0.65, buy lower at 0.55
    profit = monotone_pair_profit(upper_bid=0.65, lower_ask=0.55)
    assert profit > 0


def test_monotone_pair_profit_negative_when_spread_too_small():
    profit = monotone_pair_profit(upper_bid=0.55, lower_ask=0.54)
    assert profit < 0


def test_monotone_pair_profit_exact():
    ub, la = 0.65, 0.50
    expected = (ub - la) - taker_fee(ub) - taker_fee(la)
    assert abs(monotone_pair_profit(ub, la) - expected) < 1e-9


# --- Core fees (src/core/fees.py) tests ---


class MockFeeModel:
    """Kalshi-equivalent fee model for testing."""
    def taker_fee(self, price: float) -> float:
        return 0.07 * price * (1.0 - price)
    def maker_fee(self, price: float) -> float:
        return 0.0
    def profit_fee(self, gross_profit: float) -> float:
        return 0.0


class ProfitTaxFeeModel:
    """PredictIt-style: 10% profit fee, no per-trade fees."""
    def taker_fee(self, price: float) -> float:
        return 0.0
    def maker_fee(self, price: float) -> float:
        return 0.0
    def profit_fee(self, gross_profit: float) -> float:
        return 0.10 * gross_profit


def test_core_arb_profit_kalshi():
    from src.core.fees import arb_profit
    fm = MockFeeModel()
    profit = arb_profit([0.40, 0.40, 0.40], fm)
    expected_gross = 1.20 - 1.0
    expected_fees = 3 * 0.07 * 0.40 * 0.60
    assert abs(profit - (expected_gross - expected_fees)) < 1e-9


def test_core_arb_profit_predictit():
    from src.core.fees import arb_profit
    fm = ProfitTaxFeeModel()
    profit = arb_profit([0.40, 0.40, 0.40], fm)
    gross = 0.20
    expected = gross - 0.10 * gross
    assert abs(profit - expected) < 1e-9


def test_core_buy_side_arb_profit():
    from src.core.fees import buy_side_arb_profit
    fm = MockFeeModel()
    profit = buy_side_arb_profit([0.20, 0.30, 0.40], fm)
    cost = 0.90
    fees = sum(0.07 * p * (1 - p) for p in [0.20, 0.30, 0.40])
    expected = 1.0 - cost - fees
    assert abs(profit - expected) < 1e-9


def test_core_maker_arb_profit():
    from src.core.fees import maker_arb_profit
    fm = MockFeeModel()
    profit = maker_arb_profit([0.40, 0.40, 0.40], fm)
    assert abs(profit - 0.20) < 1e-9


def test_core_exposure_ratio():
    from src.core.fees import exposure_ratio
    fm = MockFeeModel()
    ratio = exposure_ratio([0.40, 0.40, 0.40], fm)
    assert ratio > 0
    assert ratio < float("inf")


def test_core_monotone_pair_profit():
    from src.core.fees import monotone_pair_profit
    fm = MockFeeModel()
    profit = monotone_pair_profit(0.60, 0.40, fm)
    gross = 0.60 - 0.40
    fees = fm.taker_fee(0.60) + fm.taker_fee(0.40)
    assert abs(profit - (gross - fees)) < 1e-9
