from src.exchanges.kalshi import KalshiExchange
from src.exchanges.predictit import PredictItExchange

EXCHANGES = {
    "kalshi": KalshiExchange,
    "predictit": PredictItExchange,
}


def create_exchange(name: str, config: dict):
    return EXCHANGES[name](config)
