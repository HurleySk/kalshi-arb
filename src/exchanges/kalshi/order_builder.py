class KalshiOrderBuilder:
    def build_sell_order(self, ticker: str, price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }

    def build_buy_order(self, ticker: str, price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "buy",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }

    def build_close_order(self, ticker: str, quantity: int) -> dict:
        if quantity < 0:
            return {
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 99, "count": abs(quantity),
            }
        return {
            "ticker": ticker, "action": "sell", "side": "yes",
            "type": "limit", "yes_price": 1, "count": quantity,
        }

    @staticmethod
    def unwrap_order(raw: dict) -> dict:
        return raw.get("order", raw)
