from src.exchanges.kalshi.auth import KalshiAuth
from src.exchanges.kalshi.api import KalshiAPI
from src.exchanges.kalshi.fee_model import KalshiFeeModel
from src.exchanges.kalshi.order_builder import KalshiOrderBuilder
from src.exchanges.kalshi.constraints import KalshiConstraints


class KalshiExchange:
    name = "kalshi"

    def __init__(self, config: dict):
        self.auth = KalshiAuth(config["api_key_id"], config["private_key_path"])
        self.api = KalshiAPI(config["base_url"], self.auth)
        self.ws_url = config["ws_url"]
        self.fee_model = KalshiFeeModel()
        self.order_builder = KalshiOrderBuilder()
        self.constraints = KalshiConstraints()

    def create_feed(self, orderbook_mgr, on_update=None, on_fill=None):
        from src.exchanges.kalshi.scanner import MarketScanner
        return MarketScanner(self.ws_url, self.auth, orderbook_mgr, on_update, on_fill)

    def create_discovery(self, orderbook_mgr, scanner):
        from src.exchanges.kalshi.discovery import EventDiscovery
        return EventDiscovery(self.api, orderbook_mgr, scanner)
