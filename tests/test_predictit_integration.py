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


from unittest.mock import MagicMock, patch

from src.core.orderbook_manager import OrderbookManager
from src.core.fees import arb_profit


SAMPLE_API_RESPONSE = {
    "markets": [
        {
            "id": 7456,
            "name": "Who will win the 2026 election?",
            "shortName": "2026 Election",
            "status": "Open",
            "contracts": [
                {
                    "id": 28541,
                    "name": "Democratic",
                    "shortName": "Dem",
                    "status": "Open",
                    "dateEnd": "2026-11-03T23:59:00",
                    "bestBuyYesCost": 0.54,
                    "bestBuyNoCost": 0.48,
                    "bestSellYesCost": 0.52,
                    "bestSellNoCost": 0.46,
                    "lastTradePrice": 0.53,
                    "lastClosePrice": 0.53,
                },
                {
                    "id": 28542,
                    "name": "Republican",
                    "shortName": "Rep",
                    "status": "Open",
                    "dateEnd": "2026-11-03T23:59:00",
                    "bestBuyYesCost": 0.49,
                    "bestBuyNoCost": 0.53,
                    "bestSellYesCost": 0.47,
                    "bestSellNoCost": 0.51,
                    "lastTradePrice": 0.47,
                    "lastClosePrice": 0.47,
                },
            ],
        }
    ]
}


def test_full_data_pipeline():
    """Test: scraper → discovery → scanner → orderbook → fee calc."""
    from src.exchanges.predictit import PredictItExchange
    from src.exchanges.predictit.scraper import PredictItScraper

    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": False,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    mgr = OrderbookManager()

    # Step 1: Scraper parses JSON
    parsed = exchange._scraper.parse_markets(SAMPLE_API_RESPONSE)
    assert len(parsed) == 1

    # Step 2: Discovery converts to events and registers
    scanner = exchange.create_feed(mgr)
    discovery = exchange.create_discovery(mgr, scanner)
    events = discovery._convert_to_events(parsed)
    new_tickers = discovery.register_events(events)
    assert len(new_tickers) == 2
    assert "PI-7456-28541" in new_tickers
    assert "PI-7456-28542" in new_tickers

    # Step 3: Scanner builds orderbooks from contract data
    contract_dem = SAMPLE_API_RESPONSE["markets"][0]["contracts"][0]
    contract_rep = SAMPLE_API_RESPONSE["markets"][0]["contracts"][1]
    book_dem = scanner._build_orderbook(contract_dem)
    book_rep = scanner._build_orderbook(contract_rep)
    assert book_dem.best_bid() == 0.52  # bestSellYesCost
    assert book_rep.best_bid() == 0.47  # bestSellYesCost

    # Step 4: Fee calculation works with PredictIt fee model
    bid_prices = [0.52, 0.47]
    profit = arb_profit(bid_prices, exchange.fee_model)
    # gross = 0.99 - 1.0 = -0.01 (not profitable)
    assert profit < 0

    # With wider spread (hypothetical profitable case)
    bid_prices_wide = [0.60, 0.55]
    profit_wide = arb_profit(bid_prices_wide, exchange.fee_model)
    # gross = 1.15 - 1.0 = 0.15, fee = 10% of 0.15 = 0.015, net = 0.135
    assert profit_wide > 0
