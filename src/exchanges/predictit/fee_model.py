class PredictItFeeModel:
    PROFIT_FEE_RATE = 0.10
    WITHDRAWAL_FEE_RATE = 0.05

    def __init__(self, include_withdrawal_fee: bool = True):
        self._include_withdrawal = include_withdrawal_fee
        if include_withdrawal_fee:
            self._effective_rate = 1.0 - (1.0 - self.PROFIT_FEE_RATE) * (1.0 - self.WITHDRAWAL_FEE_RATE)
        else:
            self._effective_rate = self.PROFIT_FEE_RATE

    def taker_fee(self, price: float) -> float:
        return 0.0

    def maker_fee(self, price: float) -> float:
        return 0.0

    def profit_fee(self, gross_profit: float) -> float:
        if gross_profit <= 0:
            return 0.0
        return self._effective_rate * gross_profit
