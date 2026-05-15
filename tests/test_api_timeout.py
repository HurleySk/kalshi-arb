import asyncio
from unittest.mock import MagicMock

from src.api import KalshiAPI
from src.auth import KalshiAuth


def _make_api():
    auth = MagicMock(spec=KalshiAuth)
    auth.build_headers.return_value = {
        "KALSHI-ACCESS-KEY": "test",
        "KALSHI-ACCESS-TIMESTAMP": "123",
        "KALSHI-ACCESS-SIGNATURE": "sig",
    }
    return KalshiAPI(base_url="https://test.kalshi.com", auth=auth)


def test_session_has_timeout():
    """aiohttp session must have a ClientTimeout configured."""
    api = _make_api()

    async def _run():
        session = await api._ensure_session()
        assert session.timeout is not None
        assert session.timeout.total is not None
        assert session.timeout.total <= 30
        assert session.timeout.connect is not None
        assert session.timeout.sock_read is not None
        await api.close()

    asyncio.run(_run())
