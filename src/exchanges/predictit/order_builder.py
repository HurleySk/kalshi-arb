PREDICTIT_BASE_URL = "https://www.predictit.org"


class PredictItOrderBuilder:
    def _parse_ticker(self, ticker: str) -> tuple[int, int]:
        parts = ticker.split("-")
        market_id = int(parts[1])
        contract_id = int(parts[2])
        return market_id, contract_id

    def build_sell_order(self, ticker: str, price: float, quantity: int) -> dict:
        market_id, contract_id = self._parse_ticker(ticker)
        return {
            "ticker": ticker,
            "market_id": market_id,
            "contract_id": contract_id,
            "market_url": f"{PREDICTIT_BASE_URL}/markets/detail/{market_id}",
            "action": "sell",
            "outcome": "yes",
            "shares": quantity,
            "price": round(price * 100),
        }

    def build_buy_order(self, ticker: str, price: float, quantity: int) -> dict:
        market_id, contract_id = self._parse_ticker(ticker)
        return {
            "ticker": ticker,
            "market_id": market_id,
            "contract_id": contract_id,
            "market_url": f"{PREDICTIT_BASE_URL}/markets/detail/{market_id}",
            "action": "buy",
            "outcome": "yes",
            "shares": quantity,
            "price": round(price * 100),
        }

    def build_close_order(self, ticker: str, quantity: int) -> dict:
        market_id, contract_id = self._parse_ticker(ticker)
        if quantity > 0:
            action = "sell"
            price = 1
        else:
            action = "buy"
            price = 99
        return {
            "ticker": ticker,
            "market_id": market_id,
            "contract_id": contract_id,
            "market_url": f"{PREDICTIT_BASE_URL}/markets/detail/{market_id}",
            "action": action,
            "outcome": "yes",
            "shares": abs(quantity),
            "price": price,
        }

    def unwrap_order(self, raw: dict) -> dict:
        return raw
