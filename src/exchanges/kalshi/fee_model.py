class KalshiFeeModel:
    TAKER_FEE_RATE = 0.07

    def taker_fee(self, price: float) -> float:
        return self.TAKER_FEE_RATE * price * (1.0 - price)

    def maker_fee(self, price: float) -> float:
        return 0.0

    def profit_fee(self, gross_profit: float) -> float:
        return 0.0
