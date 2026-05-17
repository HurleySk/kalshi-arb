from src.exchanges.predictit import PredictItExchange


def test_exchange_name():
    assert PredictItExchange.name == "predictit"


def test_exchange_init():
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    assert exchange.fee_model is not None
    assert exchange.order_builder is not None
    assert exchange.constraints is not None
    assert exchange.api is not None


def test_exchange_fee_model_type():
    from src.exchanges.predictit.fee_model import PredictItFeeModel
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    assert isinstance(exchange.fee_model, PredictItFeeModel)


def test_exchange_constraints_type():
    from src.exchanges.predictit.constraints import PredictItConstraints
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    assert isinstance(exchange.constraints, PredictItConstraints)


def test_create_feed():
    from src.core.orderbook_manager import OrderbookManager
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    mgr = OrderbookManager()
    feed = exchange.create_feed(mgr)
    assert feed is not None


def test_create_discovery():
    from src.core.orderbook_manager import OrderbookManager
    from unittest.mock import MagicMock
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    mgr = OrderbookManager()
    scanner = MagicMock()
    discovery = exchange.create_discovery(mgr, scanner)
    assert discovery is not None
    assert hasattr(discovery, "market_metadata")
    assert hasattr(discovery, "event_total_markets")
