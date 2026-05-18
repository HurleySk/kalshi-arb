class KalshiOrderBuilder:
    def build_sell_order(self, ticker: str, price: float, quantity: int, *,
                         time_in_force: str | None = None,
                         expiration_ts: int | None = None) -> dict:
        order = {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }
        if time_in_force:
            order["time_in_force"] = time_in_force
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        return order

    def build_buy_order(self, ticker: str, price: float, quantity: int, *,
                        time_in_force: str | None = None,
                        expiration_ts: int | None = None) -> dict:
        order = {
            "ticker": ticker,
            "action": "buy",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }
        if time_in_force:
            order["time_in_force"] = time_in_force
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        return order

    def build_close_order(self, ticker: str, quantity: int, *,
                          expiration_ts: int | None = None) -> dict:
        if quantity < 0:
            order = {
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 99, "count": abs(quantity),
            }
        else:
            order = {
                "ticker": ticker, "action": "sell", "side": "yes",
                "type": "limit", "yes_price": 1, "count": quantity,
            }
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        return order

    @staticmethod
    def unwrap_order(raw: dict) -> dict:
        return raw.get("order", raw)
