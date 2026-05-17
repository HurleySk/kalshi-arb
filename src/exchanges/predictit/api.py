import asyncio
import logging
import uuid

from src.exchanges.predictit.anti_detect import random_delay

logger = logging.getLogger(__name__)


class PredictItAPI:
    def __init__(self, browser):
        self._browser = browser

    async def batch_create_orders(self, orders: list[dict]) -> dict:
        results = []
        for order in orders:
            try:
                result = await self._place_single_order(order)
                results.append(result)
            except Exception:
                logger.exception("Failed to place order for %s", order.get("ticker"))
                results.append({
                    "order_id": f"error-{uuid.uuid4().hex[:8]}",
                    "ticker": order.get("ticker", ""),
                    "status": "error",
                })
            await asyncio.sleep(random_delay(min_secs=1.0, max_secs=3.0))
        return {"orders": results}

    async def _place_single_order(self, order: dict) -> dict:
        market_id = order["market_id"]
        await self._browser.navigate_to_market(market_id)

        order_id = f"pi-{uuid.uuid4().hex[:12]}"
        logger.info(
            "Placing order: %s %s %d shares @ %d¢ on PI-%d-%d",
            order["action"], order["outcome"],
            order["shares"], order["price"],
            market_id, order["contract_id"],
        )

        page = self._browser.page
        if page is None:
            raise RuntimeError("Browser page not available")

        # Phase 1 scaffold: returns "pending" status, which causes the executor
        # to treat this as a resting order and trigger cancel/unwind. This is
        # safe behavior for the scaffold. Phase 2 must implement actual Playwright
        # form interaction and return executor-compatible response fields:
        # status="executed", yes_price_dollars, fill_count_fp, side, ticker.

        return {
            "order_id": order_id,
            "ticker": order["ticker"],
            "status": "pending",
            "action": order["action"],
            "shares": order["shares"],
            "price": order["price"],
        }

    async def cancel_order(self, order_id: str) -> dict:
        logger.info("Cancelling order: %s", order_id)
        return {"order_id": order_id, "status": "cancelled"}

    async def batch_cancel_orders(self, order_ids: list[str]) -> dict:
        results = []
        for oid in order_ids:
            result = await self.cancel_order(oid)
            results.append(result)
        return {"cancelled": results}

    async def get_positions(self) -> dict:
        logger.debug("Getting PredictIt positions via browser")
        return {"market_positions": []}

    async def get_open_orders(self) -> dict:
        logger.debug("Getting PredictIt open orders via browser")
        return {"orders": []}

    async def get_balance(self) -> dict:
        logger.debug("Getting PredictIt balance via browser")
        return {"balance": 0, "portfolio_value": 0}

    async def get_market_trades(self, ticker: str, limit: int = 10) -> dict:
        logger.debug("Getting PredictIt market trades for %s via browser", ticker)
        return {"trades": []}

    async def close(self) -> None:
        await self._browser.close()
