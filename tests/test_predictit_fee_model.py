import math

from src.exchanges.predictit.fee_model import PredictItFeeModel


def test_taker_fee_is_zero():
    fm = PredictItFeeModel()
    assert fm.taker_fee(0.50) == 0.0
    assert fm.taker_fee(0.01) == 0.0
    assert fm.taker_fee(0.99) == 0.0


def test_maker_fee_is_zero():
    fm = PredictItFeeModel()
    assert fm.maker_fee(0.50) == 0.0


def test_profit_fee_ten_percent_without_withdrawal():
    fm = PredictItFeeModel(include_withdrawal_fee=False)
    assert math.isclose(fm.profit_fee(1.0), 0.10, abs_tol=1e-9)
    assert math.isclose(fm.profit_fee(0.50), 0.05, abs_tol=1e-9)


def test_profit_fee_with_withdrawal():
    fm = PredictItFeeModel(include_withdrawal_fee=True)
    assert math.isclose(fm.profit_fee(1.0), 0.145, abs_tol=1e-9)
    assert math.isclose(fm.profit_fee(0.50), 0.0725, abs_tol=1e-9)


def test_profit_fee_default_includes_withdrawal():
    fm = PredictItFeeModel()
    assert math.isclose(fm.profit_fee(1.0), 0.145, abs_tol=1e-9)


def test_profit_fee_zero_on_zero_profit():
    fm = PredictItFeeModel()
    assert fm.profit_fee(0.0) == 0.0


def test_profit_fee_zero_on_negative_profit():
    fm = PredictItFeeModel()
    assert fm.profit_fee(-0.50) == 0.0


def test_arb_profit_with_predictit_fees():
    from src.core.fees import arb_profit
    fm = PredictItFeeModel(include_withdrawal_fee=False)
    prices = [0.40, 0.40, 0.40]
    gross = sum(prices) - 1.0
    expected = gross - 0.10 * gross
    assert math.isclose(arb_profit(prices, fm), expected, abs_tol=1e-9)


def test_arb_profit_with_withdrawal_fee():
    from src.core.fees import arb_profit
    fm = PredictItFeeModel(include_withdrawal_fee=True)
    prices = [0.40, 0.40, 0.40]
    gross = sum(prices) - 1.0
    expected = gross - 0.145 * gross
    assert math.isclose(arb_profit(prices, fm), expected, abs_tol=1e-9)
