from src.exchanges.kalshi.order_builder import KalshiOrderBuilder


def test_build_sell_order_default_no_tif():
    ob = KalshiOrderBuilder()
    order = ob.build_sell_order("TICKER", 0.55, 1)
    assert order["ticker"] == "TICKER"
    assert order["yes_price"] == 55
    assert order["count"] == 1
    assert order["action"] == "sell"
    assert "time_in_force" not in order
    assert "expiration_ts" not in order


def test_build_sell_order_with_ioc():
    ob = KalshiOrderBuilder()
    order = ob.build_sell_order("TICKER", 0.55, 1, time_in_force="immediate_or_cancel")
    assert order["time_in_force"] == "immediate_or_cancel"
    assert "expiration_ts" not in order


def test_build_sell_order_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_sell_order("TICKER", 0.55, 1, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000
    assert "time_in_force" not in order


def test_build_buy_order_default_no_tif():
    ob = KalshiOrderBuilder()
    order = ob.build_buy_order("TICKER", 0.40, 2)
    assert order["ticker"] == "TICKER"
    assert order["yes_price"] == 40
    assert order["count"] == 2
    assert order["action"] == "buy"
    assert "time_in_force" not in order
    assert "expiration_ts" not in order


def test_build_buy_order_with_ioc():
    ob = KalshiOrderBuilder()
    order = ob.build_buy_order("TICKER", 0.40, 2, time_in_force="immediate_or_cancel")
    assert order["time_in_force"] == "immediate_or_cancel"


def test_build_buy_order_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_buy_order("TICKER", 0.40, 2, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000


def test_build_close_order_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_close_order("TICKER", 1, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000
    assert order["action"] == "sell"
    assert order["yes_price"] == 1


def test_build_close_order_negative_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_close_order("TICKER", -1, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000
    assert order["action"] == "buy"
    assert order["yes_price"] == 99


def test_build_close_order_default_no_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_close_order("TICKER", 1)
    assert "expiration_ts" not in order
