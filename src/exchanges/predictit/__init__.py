from src.exchanges.predictit.fee_model import PredictItFeeModel
from src.exchanges.predictit.order_builder import PredictItOrderBuilder
from src.exchanges.predictit.constraints import PredictItConstraints
from src.exchanges.predictit.scraper import PredictItScraper
from src.exchanges.predictit.browser import PredictItBrowser
from src.exchanges.predictit.api import PredictItAPI


class PredictItExchange:
    name = "predictit"

    def __init__(self, config: dict):
        self.fee_model = PredictItFeeModel(
            include_withdrawal_fee=config.get("include_withdrawal_fee", True),
        )
        self.order_builder = PredictItOrderBuilder()
        self.constraints = PredictItConstraints()
        self._scraper = PredictItScraper(proxy_url=config.get("proxy_url"))
        self._browser = PredictItBrowser(
            session_dir=config.get("session_dir", "~/.kalshi/predictit_session"),
            proxy_url=config.get("proxy_url"),
            headless=config.get("headless", True),
        )
        self.api = PredictItAPI(browser=self._browser)
        self._poll_interval_secs = config.get("poll_interval_secs", 60)

    def create_feed(self, orderbook_mgr, on_update=None, on_fill=None):
        from src.exchanges.predictit.scanner import PredictItScanner
        return PredictItScanner(
            scraper=self._scraper,
            orderbook_mgr=orderbook_mgr,
            on_orderbook_update=on_update,
            on_fill=on_fill,
            poll_interval_secs=self._poll_interval_secs,
        )

    def create_discovery(self, orderbook_mgr, scanner):
        from src.exchanges.predictit.discovery import PredictItDiscovery
        return PredictItDiscovery(
            scraper=self._scraper,
            orderbook_mgr=orderbook_mgr,
            scanner=scanner,
        )
