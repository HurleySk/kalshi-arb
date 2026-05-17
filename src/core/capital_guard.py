import logging

logger = logging.getLogger(__name__)


class CapitalGuard:
    def __init__(self, budgets: dict[str, float]):
        self._budgets = budgets
        self._ledger: dict[str, dict[str, float]] = {}

    def can_execute(self, exchange: str, cost: float) -> bool:
        budget = self._budgets.get(exchange)
        if budget is None:
            return True
        return self.deployed(exchange) + cost <= budget

    def commit(self, exchange: str, order_id: str, cost: float) -> None:
        if exchange not in self._ledger:
            self._ledger[exchange] = {}
        self._ledger[exchange][order_id] = cost

    def release(self, exchange: str, order_id: str) -> None:
        if exchange in self._ledger:
            self._ledger[exchange].pop(order_id, None)

    def headroom(self, exchange: str) -> float:
        budget = self._budgets.get(exchange)
        if budget is None:
            return float("inf")
        return budget - self.deployed(exchange)

    def deployed(self, exchange: str) -> float:
        if exchange not in self._ledger:
            return 0.0
        return sum(self._ledger[exchange].values())
