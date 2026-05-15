from src.exchanges.kalshi import KalshiExchange

EXCHANGES = {
    "kalshi": KalshiExchange,
}


def create_exchange(name: str, config: dict):
    return EXCHANGES[name](config)
