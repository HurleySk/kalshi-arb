class PredictItConstraints:
    DEFAULT_MAX_CONTRACTS = 3500

    def __init__(self, max_contracts: int = DEFAULT_MAX_CONTRACTS):
        self._max_contracts = max_contracts

    def max_position_size(self, ticker: str) -> int | None:
        return self._max_contracts

    def max_total_exposure(self) -> float | None:
        return None
