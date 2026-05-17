from src.exchanges.predictit.order_builder import PredictItOrderBuilder


def test_build_sell_order():
    ob = PredictItOrderBuilder()
    order = ob.build_sell_order("PI-7456-28541", 0.55, 10)
    assert order["ticker"] == "PI-7456-28541"
    assert order["action"] == "sell"
    assert order["outcome"] == "yes"
    assert order["price"] == 55
    assert order["shares"] == 10
    assert order["market_id"] == 7456
    assert order["contract_id"] == 28541


def test_build_buy_order():
    ob = PredictItOrderBuilder()
    order = ob.build_buy_order("PI-7456-28541", 0.45, 5)
    assert order["ticker"] == "PI-7456-28541"
    assert order["action"] == "buy"
    assert order["outcome"] == "yes"
    assert order["price"] == 45
    assert order["shares"] == 5


def test_build_close_order_long_position():
    ob = PredictItOrderBuilder()
    order = ob.build_close_order("PI-7456-28541", 10)
    assert order["action"] == "sell"
    assert order["price"] == 1
    assert order["shares"] == 10


def test_build_close_order_short_position():
    ob = PredictItOrderBuilder()
    order = ob.build_close_order("PI-7456-28541", -10)
    assert order["action"] == "buy"
    assert order["price"] == 99
    assert order["shares"] == 10


def test_unwrap_order():
    ob = PredictItOrderBuilder()
    raw = {
        "order_id": "browser-1234",
        "ticker": "PI-7456-28541",
        "status": "filled",
    }
    unwrapped = ob.unwrap_order(raw)
    assert unwrapped == raw


def test_ticker_parsing():
    ob = PredictItOrderBuilder()
    order = ob.build_sell_order("PI-100-200", 0.50, 1)
    assert order["market_id"] == 100
    assert order["contract_id"] == 200
    assert order["market_url"] == "https://www.predictit.org/markets/detail/100"
