def create_exchange(name: str, config: dict):
    if name == "kalshi":
        from src.exchanges.kalshi import KalshiExchange
        return KalshiExchange(config)
    elif name == "predictit":
        from src.exchanges.predictit import PredictItExchange
        return PredictItExchange(config)
    raise KeyError(name)
