# Kalshi Arbitrage Bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python bot that continuously monitors Kalshi multi-outcome events via WebSocket, detects arbitrage opportunities where selling "yes" on all outcomes is profitable after fees and risk checks, and auto-executes trades.

**Architecture:** Three async components — Market Scanner (WebSocket orderbook state), Arbitrage Engine (opportunity detection with fee/risk filters), Execution Manager (batch order placement with timeout-based cancellation). REST poller discovers new events. Config toggles demo/live mode.

**Tech Stack:** Python 3.11, asyncio, websockets, aiohttp, cryptography (RSA-PSS signing), PyYAML

---

### Task 1: Project Scaffolding

**Files:**
- Create: `src/__init__.py`
- Create: `requirements.txt`
- Create: `config.example.yaml`

**Step 1: Create requirements.txt**

```
websockets>=12.0
aiohttp>=3.9
cryptography>=42.0
pyyaml>=6.0
```

Run: `cd /home/pi/source/kalshi-arb && pip3 install -r requirements.txt`

**Step 2: Create config.example.yaml**

```yaml
mode: demo

credentials:
  demo:
    api_key_id: "your-demo-key-id"
    private_key_path: "~/.kalshi/demo_private_key.pem"
  live:
    api_key_id: "your-live-key-id"
    private_key_path: "~/.kalshi/live_private_key.pem"

strategy:
  min_profit_pct: 2.0
  max_exposure_ratio: 3.0
  fill_timeout_secs: 30
  event_poll_interval_secs: 60

logging:
  level: INFO
  file: logs/arb_bot.log
```

**Step 3: Create src/__init__.py**

Empty file.

**Step 4: Commit**

```bash
git add requirements.txt config.example.yaml src/__init__.py
git commit -m "scaffold: project structure, dependencies, example config"
```

---

### Task 2: Data Models

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

Create `tests/__init__.py` (empty) and `tests/test_models.py`:

```python
from src.models import (
    OrderbookLevel,
    Orderbook,
    Market,
    Event,
    Order,
    OrderStatus,
    Position,
    TradeSignal,
)


def test_orderbook_best_bid_returns_highest_price():
    book = Orderbook(
        yes_bids=[OrderbookLevel(price=0.30, quantity=100), OrderbookLevel(price=0.25, quantity=50)],
        no_bids=[],
    )
    assert book.best_yes_bid() == 0.30


def test_orderbook_best_bid_empty_returns_none():
    book = Orderbook(yes_bids=[], no_bids=[])
    assert book.best_yes_bid() is None


def test_event_market_tickers():
    m1 = Market(ticker="T1", event_ticker="E1", title="Outcome 1", status="active")
    m2 = Market(ticker="T2", event_ticker="E1", title="Outcome 2", status="active")
    event = Event(
        event_ticker="E1",
        title="Test Event",
        series_ticker="S1",
        mutually_exclusive=True,
        markets=[m1, m2],
    )
    assert event.market_tickers() == ["T1", "T2"]


def test_trade_signal_has_required_fields():
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("T1", 0.55), ("T2", 0.50)],
        net_profit=0.03,
        profit_pct=3.0,
        exposure_ratio=1.5,
    )
    assert signal.event_ticker == "E1"
    assert len(signal.legs) == 2
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models'`

**Step 3: Write minimal implementation**

Create `src/models.py`:

```python
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class OrderbookLevel:
    price: float
    quantity: float


@dataclass
class Orderbook:
    yes_bids: list[OrderbookLevel] = field(default_factory=list)
    no_bids: list[OrderbookLevel] = field(default_factory=list)

    def best_yes_bid(self) -> float | None:
        if not self.yes_bids:
            return None
        return max(level.price for level in self.yes_bids)


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str


@dataclass
class Event:
    event_ticker: str
    title: str
    series_ticker: str
    mutually_exclusive: bool
    markets: list[Market] = field(default_factory=list)

    def market_tickers(self) -> list[str]:
        return [m.ticker for m in self.markets]


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"


@dataclass
class Order:
    order_id: str
    ticker: str
    action: str
    side: str
    price: float
    quantity: float
    status: OrderStatus
    filled_quantity: float = 0.0


@dataclass
class Position:
    ticker: str
    side: str
    quantity: float
    avg_price: float


@dataclass
class TradeSignal:
    event_ticker: str
    legs: list[tuple[str, float]]
    net_profit: float
    profit_pct: float
    exposure_ratio: float
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_models.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/models.py tests/__init__.py tests/test_models.py
git commit -m "feat: add data models for orderbooks, events, orders, positions"
```

---

### Task 3: Fee Calculator

**Files:**
- Create: `src/fees.py`
- Create: `tests/test_fees.py`

**Step 1: Write the failing tests**

Create `tests/test_fees.py`:

```python
import math
from src.fees import maker_fee, arb_profit, exposure_ratio


def test_maker_fee_at_50_cents():
    fee = maker_fee(0.50)
    assert math.isclose(fee, 0.004375, abs_tol=1e-6)


def test_maker_fee_at_extremes():
    assert math.isclose(maker_fee(0.01), 0.0175 * 0.01 * 0.99, abs_tol=1e-6)
    assert math.isclose(maker_fee(0.99), 0.0175 * 0.99 * 0.01, abs_tol=1e-6)


def test_maker_fee_at_zero_and_one():
    assert maker_fee(0.0) == 0.0
    assert maker_fee(1.0) == 0.0


def test_arb_profit_basic():
    prices = [0.30, 0.25, 0.25, 0.25]
    profit = arb_profit(prices)
    gross = sum(prices) - 1.0  # 0.05
    fees = sum(maker_fee(p) for p in prices)
    assert math.isclose(profit, gross - fees, abs_tol=1e-6)
    assert profit > 0


def test_arb_profit_no_opportunity():
    prices = [0.25, 0.25, 0.25, 0.25]
    profit = arb_profit(prices)
    assert profit < 0


def test_exposure_ratio_basic():
    prices = [0.30, 0.25, 0.25, 0.25]
    ratio = exposure_ratio(prices)
    # worst case: miss the 0.30 leg, owe $1 on a filled leg
    # collected from filled legs: 0.25 + 0.25 + 0.25 = 0.75
    # worst loss: 1.0 - 0.75 = 0.25
    # net premium: 1.05 - 1.0 - fees
    assert ratio > 0
    assert not math.isinf(ratio)


def test_exposure_ratio_no_opportunity():
    prices = [0.25, 0.25, 0.25, 0.25]
    ratio = exposure_ratio(prices)
    assert math.isinf(ratio)


def test_exposure_ratio_safe_arb():
    # Very fat spread — even partial fill is safe
    prices = [0.60, 0.55, 0.50]
    ratio = exposure_ratio(prices)
    # sum = 1.65, miss 0.60 leg, filled collect 1.05, owe $1
    # worst_loss = max(0, 1.0 - 1.05) = 0.0
    assert math.isclose(ratio, 0.0, abs_tol=1e-6)
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_fees.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fees'`

**Step 3: Write minimal implementation**

Create `src/fees.py`:

```python
def maker_fee(price: float) -> float:
    return 0.0175 * price * (1.0 - price)


def arb_profit(bid_prices: list[float]) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(maker_fee(p) for p in bid_prices)
    return gross - fees


def exposure_ratio(bid_prices: list[float]) -> float:
    premiums = sum(bid_prices)
    fees = sum(maker_fee(p) for p in bid_prices)
    net_premium = premiums - 1.0 - fees
    if net_premium <= 0:
        return float("inf")
    worst_loss = max(0.0, 1.0 - (premiums - max(bid_prices)))
    return worst_loss / net_premium
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_fees.py -v`
Expected: 8 passed

**Step 5: Commit**

```bash
git add src/fees.py tests/test_fees.py
git commit -m "feat: add fee calculator with maker_fee, arb_profit, exposure_ratio"
```

---

### Task 4: Config Loader

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import os
import tempfile
import yaml
from src.config import load_config, Config, DEMO_REST_URL, DEMO_WS_URL, LIVE_REST_URL, LIVE_WS_URL


SAMPLE_CONFIG = {
    "mode": "demo",
    "credentials": {
        "demo": {
            "api_key_id": "test-key",
            "private_key_path": "/tmp/test_key.pem",
        },
        "live": {
            "api_key_id": "live-key",
            "private_key_path": "/tmp/live_key.pem",
        },
    },
    "strategy": {
        "min_profit_pct": 2.0,
        "max_exposure_ratio": 3.0,
        "fill_timeout_secs": 30,
        "event_poll_interval_secs": 60,
    },
    "logging": {
        "level": "INFO",
        "file": "logs/arb_bot.log",
    },
}


def test_load_config_demo_mode():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(SAMPLE_CONFIG, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.mode == "demo"
    assert cfg.api_key_id == "test-key"
    assert cfg.rest_base_url == DEMO_REST_URL
    assert cfg.ws_url == DEMO_WS_URL
    assert cfg.min_profit_pct == 2.0
    assert cfg.max_exposure_ratio == 3.0
    assert cfg.fill_timeout_secs == 30


def test_load_config_live_mode():
    live_config = {**SAMPLE_CONFIG, "mode": "live"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(live_config, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.mode == "live"
    assert cfg.api_key_id == "live-key"
    assert cfg.rest_base_url == LIVE_REST_URL
    assert cfg.ws_url == LIVE_WS_URL


def test_load_config_invalid_mode():
    bad_config = {**SAMPLE_CONFIG, "mode": "invalid"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_config, f)
        f.flush()
        try:
            load_config(f.name)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
    os.unlink(f.name)
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.config'`

**Step 3: Write minimal implementation**

Create `src/config.py`:

```python
from dataclasses import dataclass
from pathlib import Path

import yaml

DEMO_REST_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
LIVE_REST_URL = "https://trading-api.kalshi.com/trade-api/v2"
LIVE_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

URLS = {
    "demo": (DEMO_REST_URL, DEMO_WS_URL),
    "live": (LIVE_REST_URL, LIVE_WS_URL),
}


@dataclass
class Config:
    mode: str
    api_key_id: str
    private_key_path: Path
    rest_base_url: str
    ws_url: str
    min_profit_pct: float
    max_exposure_ratio: float
    fill_timeout_secs: int
    event_poll_interval_secs: int
    log_level: str
    log_file: str


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    mode = raw["mode"]
    if mode not in URLS:
        raise ValueError(f"Invalid mode: {mode!r}. Must be 'demo' or 'live'.")

    creds = raw["credentials"][mode]
    rest_url, ws_url = URLS[mode]
    strategy = raw["strategy"]
    logging_cfg = raw.get("logging", {})

    return Config(
        mode=mode,
        api_key_id=creds["api_key_id"],
        private_key_path=Path(creds["private_key_path"]).expanduser(),
        rest_base_url=rest_url,
        ws_url=ws_url,
        min_profit_pct=strategy["min_profit_pct"],
        max_exposure_ratio=strategy["max_exposure_ratio"],
        fill_timeout_secs=strategy["fill_timeout_secs"],
        event_poll_interval_secs=strategy["event_poll_interval_secs"],
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/arb_bot.log"),
    )
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_config.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add config loader with demo/live mode switching"
```

---

### Task 5: Authentication (RSA-PSS Signing)

**Files:**
- Create: `src/auth.py`
- Create: `tests/test_auth.py`

**Step 1: Write the failing test**

Create `tests/test_auth.py`:

```python
import tempfile
import os
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from src.auth import KalshiAuth


def _generate_test_key() -> tuple[str, rsa.RSAPrivateKey]:
    """Generate a temporary RSA key pair for testing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd, path = tempfile.mkstemp(suffix=".pem")
    os.write(fd, pem)
    os.close(fd)
    return path, private_key


def test_auth_headers_have_required_keys():
    path, _ = _generate_test_key()
    auth = KalshiAuth(api_key_id="test-key", private_key_path=path)
    headers = auth.build_headers("GET", "/trade-api/v2/events")
    os.unlink(path)

    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "test-key"


def test_auth_signature_is_verifiable():
    path, private_key = _generate_test_key()
    auth = KalshiAuth(api_key_id="test-key", private_key_path=path)
    headers = auth.build_headers("GET", "/trade-api/v2/events")
    os.unlink(path)

    import base64
    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
    sig_bytes = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    msg = f"{timestamp}GET/trade-api/v2/events".encode("utf-8")

    # Verify using the public key — should not raise
    public_key = private_key.public_key()
    public_key.verify(
        sig_bytes,
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_auth_strips_query_params_from_signature():
    path, private_key = _generate_test_key()
    auth = KalshiAuth(api_key_id="test-key", private_key_path=path)
    headers = auth.build_headers("GET", "/trade-api/v2/events?limit=10&cursor=abc")
    os.unlink(path)

    import base64
    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
    sig_bytes = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    # The signed path should NOT include query params
    msg = f"{timestamp}GET/trade-api/v2/events".encode("utf-8")

    public_key = private_key.public_key()
    public_key.verify(
        sig_bytes,
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.auth'`

**Step 3: Write minimal implementation**

Create `src/auth.py`:

```python
import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiAuth:
    def __init__(self, api_key_id: str, private_key_path: str | Path):
        self.api_key_id = api_key_id
        with open(private_key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(f.read(), password=None)

    def build_headers(self, method: str, path: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        clean_path = path.split("?")[0]
        message = f"{timestamp_ms}{method}{clean_path}".encode("utf-8")

        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_auth.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/auth.py tests/test_auth.py
git commit -m "feat: add RSA-PSS authentication for Kalshi API"
```

---

### Task 6: REST API Client

**Files:**
- Create: `src/api.py`
- Create: `tests/test_api.py`

**Step 1: Write the failing test**

Create `tests/test_api.py`. These tests mock `aiohttp` to verify request construction — no real API calls.

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from src.api import KalshiAPI
from src.models import Event, Market


def _make_api() -> KalshiAPI:
    auth = MagicMock()
    auth.build_headers.return_value = {
        "KALSHI-ACCESS-KEY": "k",
        "KALSHI-ACCESS-TIMESTAMP": "123",
        "KALSHI-ACCESS-SIGNATURE": "sig",
    }
    return KalshiAPI(base_url="https://test.kalshi.co/trade-api/v2", auth=auth)


def test_parse_events_response():
    api = _make_api()
    raw = {
        "events": [
            {
                "event_ticker": "E1",
                "title": "Test",
                "series_ticker": "S1",
                "mutually_exclusive": True,
                "markets": [
                    {"ticker": "M1", "event_ticker": "E1", "title": "Out 1", "status": "active"},
                    {"ticker": "M2", "event_ticker": "E1", "title": "Out 2", "status": "active"},
                ],
            }
        ],
        "cursor": "",
    }
    events = api.parse_events(raw)
    assert len(events) == 1
    assert events[0].event_ticker == "E1"
    assert len(events[0].markets) == 2
    assert events[0].mutually_exclusive is True


def test_parse_events_filters_non_mutually_exclusive():
    api = _make_api()
    raw = {
        "events": [
            {
                "event_ticker": "E1",
                "title": "ME",
                "series_ticker": "S1",
                "mutually_exclusive": True,
                "markets": [{"ticker": "M1", "event_ticker": "E1", "title": "O1", "status": "active"}],
            },
            {
                "event_ticker": "E2",
                "title": "Not ME",
                "series_ticker": "S2",
                "mutually_exclusive": False,
                "markets": [{"ticker": "M3", "event_ticker": "E2", "title": "O3", "status": "active"}],
            },
        ],
        "cursor": "",
    }
    events = api.parse_events(raw)
    assert len(events) == 1
    assert events[0].event_ticker == "E1"


def test_parse_events_filters_single_market():
    api = _make_api()
    raw = {
        "events": [
            {
                "event_ticker": "E1",
                "title": "Single",
                "series_ticker": "S1",
                "mutually_exclusive": True,
                "markets": [{"ticker": "M1", "event_ticker": "E1", "title": "O1", "status": "active"}],
            },
        ],
        "cursor": "",
    }
    events = api.parse_events(raw)
    assert len(events) == 0


def test_build_sell_order():
    api = _make_api()
    order = api.build_sell_order(ticker="M1", yes_price=0.55, quantity=10)
    assert order["ticker"] == "M1"
    assert order["action"] == "sell"
    assert order["side"] == "yes"
    assert order["type"] == "limit"
    assert order["yes_price_cents"] == 55
    assert order["count"] == 10
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api'`

**Step 3: Write minimal implementation**

Create `src/api.py`:

```python
import logging
from typing import Any

import aiohttp

from src.auth import KalshiAuth
from src.models import Event, Market

logger = logging.getLogger(__name__)


class KalshiAPI:
    def __init__(self, base_url: str, auth: KalshiAuth):
        self.base_url = base_url
        self.auth = auth
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, method: str, path: str) -> dict[str, str]:
        return {
            **self.auth.build_headers(method, path),
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict | None = None) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        full_path = f"/trade-api/v2{path}"
        if params:
            full_path += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        headers = self._headers("GET", full_path)
        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, body: dict) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        full_path = f"/trade-api/v2{path}"
        headers = self._headers("POST", full_path)
        async with session.post(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _delete(self, path: str, body: dict | None = None) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        full_path = f"/trade-api/v2{path}"
        headers = self._headers("DELETE", full_path)
        async with session.delete(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            return await resp.json()

    def parse_events(self, raw: dict) -> list[Event]:
        events = []
        for e in raw.get("events", []):
            if not e.get("mutually_exclusive", False):
                continue
            markets = [
                Market(
                    ticker=m["ticker"],
                    event_ticker=m["event_ticker"],
                    title=m["title"],
                    status=m["status"],
                )
                for m in e.get("markets", [])
                if m.get("status") == "active"
            ]
            if len(markets) < 2:
                continue
            events.append(
                Event(
                    event_ticker=e["event_ticker"],
                    title=e["title"],
                    series_ticker=e.get("series_ticker", ""),
                    mutually_exclusive=True,
                    markets=markets,
                )
            )
        return events

    async def fetch_events(self) -> list[Event]:
        all_events: list[Event] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"with_nested_markets": "true", "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            raw = await self._get("/events", params=params)
            all_events.extend(self.parse_events(raw))
            cursor = raw.get("cursor", "")
            if not cursor:
                break
        return all_events

    async def get_orderbook(self, ticker: str) -> dict:
        return await self._get(f"/markets/{ticker}/orderbook")

    def build_sell_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price_cents": round(yes_price * 100),
            "count": quantity,
        }

    async def batch_create_orders(self, orders: list[dict]) -> dict:
        return await self._post("/portfolio/orders/batched", {"orders": orders})

    async def cancel_order(self, order_id: str) -> dict:
        return await self._delete(f"/portfolio/orders/{order_id}")

    async def batch_cancel_orders(self, order_ids: list[str]) -> dict:
        return await self._delete("/portfolio/orders/batched", {"ids": order_ids})

    async def get_positions(self) -> dict:
        return await self._get("/portfolio/positions")

    async def get_open_orders(self) -> dict:
        return await self._get("/portfolio/orders")

    async def get_balance(self) -> dict:
        return await self._get("/portfolio/balance")
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_api.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/api.py tests/test_api.py
git commit -m "feat: add REST API client with event parsing and order building"
```

---

### Task 7: Arbitrage Engine

**Files:**
- Create: `src/engine.py`
- Create: `tests/test_engine.py`

**Step 1: Write the failing tests**

Create `tests/test_engine.py`:

```python
from src.engine import ArbEngine
from src.models import Orderbook, OrderbookLevel, TradeSignal


def _make_engine(min_profit_pct=2.0, max_exposure_ratio=3.0):
    return ArbEngine(min_profit_pct=min_profit_pct, max_exposure_ratio=max_exposure_ratio)


def test_evaluate_profitable_arb():
    engine = _make_engine(min_profit_pct=1.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.event_ticker == "E1"
    assert signal.net_profit > 0
    assert signal.profit_pct >= 1.0
    assert len(signal.legs) == 3


def test_evaluate_no_arb_below_one_dollar():
    engine = _make_engine()
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_rejects_below_profit_threshold():
    engine = _make_engine(min_profit_pct=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_rejects_high_exposure_ratio():
    engine = _make_engine(min_profit_pct=0.1, max_exposure_ratio=0.5)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.25, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.25, quantity=100)], no_bids=[]),
        "M4": Orderbook(yes_bids=[OrderbookLevel(price=0.25, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_skips_markets_with_no_bids():
    engine = _make_engine()
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.60, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_uses_best_bid():
    engine = _make_engine(min_profit_pct=0.5)
    orderbooks = {
        "M1": Orderbook(
            yes_bids=[OrderbookLevel(price=0.40, quantity=50), OrderbookLevel(price=0.35, quantity=100)],
            no_bids=[],
        ),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    # Should use best bid (0.40) not 0.35
    assert any(price == 0.40 for _, price in signal.legs)
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine'`

**Step 3: Write minimal implementation**

Create `src/engine.py`:

```python
import logging

from src.fees import arb_profit, exposure_ratio
from src.models import Orderbook, TradeSignal

logger = logging.getLogger(__name__)


class ArbEngine:
    def __init__(self, min_profit_pct: float, max_exposure_ratio: float):
        self.min_profit_pct = min_profit_pct
        self.max_exposure_ratio = max_exposure_ratio

    def evaluate(self, event_ticker: str, orderbooks: dict[str, Orderbook]) -> TradeSignal | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            legs.append((ticker, best_bid))

        bid_prices = [price for _, price in legs]
        profit = arb_profit(bid_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100
        if profit_pct < self.min_profit_pct:
            return None

        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
        )
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_engine.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: add arbitrage engine with profit and exposure ratio filtering"
```

---

### Task 8: Position Tracker

**Files:**
- Create: `src/positions.py`
- Create: `tests/test_positions.py`

**Step 1: Write the failing tests**

Create `tests/test_positions.py`:

```python
from src.positions import PositionTracker


def test_record_fill_creates_position():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    pos = tracker.get_position("M1")
    assert pos is not None
    assert pos.quantity == 10
    assert pos.avg_price == 0.55


def test_record_multiple_fills_same_ticker():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.50, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="sell")
    pos = tracker.get_position("M1")
    assert pos.quantity == 20
    assert pos.avg_price == 0.55


def test_pnl_all_outcomes_filled():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.40, quantity=10, action="sell")
    tracker.record_fill(ticker="M2", side="yes", price=0.35, quantity=10, action="sell")
    tracker.record_fill(ticker="M3", side="yes", price=0.35, quantity=10, action="sell")
    # Premiums collected: (0.40 + 0.35 + 0.35) * 10 = $11.00
    # Payout on winning leg: $1.00 * 10 = $10.00
    # Gross profit: $1.00
    pnl = tracker.calculate_event_pnl(["M1", "M2", "M3"])
    assert pnl["total_premium"] == 11.0
    assert pnl["max_payout"] == 10.0
    assert pnl["gross_profit"] == 1.0


def test_open_positions_list():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.40, quantity=10, action="sell")
    tracker.record_fill(ticker="M2", side="yes", price=0.35, quantity=10, action="sell")
    assert len(tracker.open_positions()) == 2
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_positions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.positions'`

**Step 3: Write minimal implementation**

Create `src/positions.py`:

```python
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    ticker: str
    side: str
    quantity: float
    avg_price: float


class PositionTracker:
    def __init__(self):
        self._positions: dict[str, TrackedPosition] = {}

    def record_fill(self, ticker: str, side: str, price: float, quantity: float, action: str):
        if ticker in self._positions:
            pos = self._positions[ticker]
            total_cost = pos.avg_price * pos.quantity + price * quantity
            pos.quantity += quantity
            pos.avg_price = total_cost / pos.quantity
        else:
            self._positions[ticker] = TrackedPosition(
                ticker=ticker,
                side=side,
                quantity=quantity,
                avg_price=price,
            )
        logger.info(f"Fill: {action} {quantity}x {ticker} @ {price:.4f}")

    def get_position(self, ticker: str) -> TrackedPosition | None:
        return self._positions.get(ticker)

    def open_positions(self) -> list[TrackedPosition]:
        return [p for p in self._positions.values() if p.quantity > 0]

    def calculate_event_pnl(self, tickers: list[str]) -> dict:
        total_premium = 0.0
        max_quantity = 0.0
        for t in tickers:
            pos = self._positions.get(t)
            if pos:
                total_premium += pos.avg_price * pos.quantity
                max_quantity = max(max_quantity, pos.quantity)
        max_payout = 1.0 * max_quantity
        return {
            "total_premium": total_premium,
            "max_payout": max_payout,
            "gross_profit": total_premium - max_payout,
        }
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_positions.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/positions.py tests/test_positions.py
git commit -m "feat: add position tracker with fill recording and P&L calculation"
```

---

### Task 9: WebSocket Market Scanner

**Files:**
- Create: `src/scanner.py`
- Create: `tests/test_scanner.py`

**Step 1: Write the failing tests**

Create `tests/test_scanner.py`. Scanner state management is testable without a real WebSocket.

```python
from src.scanner import OrderbookManager
from src.models import OrderbookLevel


def test_apply_snapshot():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"], ["0.3500", "50.00"]],
        "no_dollars_fp": [["0.6000", "80.00"]],
    }
    mgr.apply_snapshot("M1", snapshot)
    book = mgr.get_orderbook("M1")
    assert book is not None
    assert len(book.yes_bids) == 2
    assert book.best_yes_bid() == 0.40


def test_apply_delta_add():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    }
    mgr.apply_snapshot("M1", snapshot)
    delta = {
        "market_ticker": "M1",
        "price_dollars": "0.3500",
        "delta_fp": "50.00",
        "side": "yes",
    }
    mgr.apply_delta("M1", delta)
    book = mgr.get_orderbook("M1")
    assert len(book.yes_bids) == 2


def test_apply_delta_remove():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    }
    mgr.apply_snapshot("M1", snapshot)
    delta = {
        "market_ticker": "M1",
        "price_dollars": "0.4000",
        "delta_fp": "-100.00",
        "side": "yes",
    }
    mgr.apply_delta("M1", delta)
    book = mgr.get_orderbook("M1")
    assert len(book.yes_bids) == 0
    assert book.best_yes_bid() is None


def test_apply_delta_update_quantity():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    }
    mgr.apply_snapshot("M1", snapshot)
    delta = {
        "market_ticker": "M1",
        "price_dollars": "0.4000",
        "delta_fp": "-30.00",
        "side": "yes",
    }
    mgr.apply_delta("M1", delta)
    book = mgr.get_orderbook("M1")
    assert len(book.yes_bids) == 1
    assert book.yes_bids[0].quantity == 70.0


def test_get_event_orderbooks():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    mgr.apply_snapshot("M1", {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    })
    mgr.apply_snapshot("M2", {
        "market_ticker": "M2",
        "yes_dollars_fp": [["0.3500", "50.00"]],
        "no_dollars_fp": [],
    })
    event_books = mgr.get_event_orderbooks("E1")
    assert len(event_books) == 2
    assert "M1" in event_books
    assert "M2" in event_books


def test_market_to_event_mapping():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    assert mgr.get_event_for_market("M1") == "E1"
    assert mgr.get_event_for_market("M2") == "E1"
    assert mgr.get_event_for_market("M3") is None
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.scanner'`

**Step 3: Write minimal implementation**

Create `src/scanner.py`:

```python
import asyncio
import json
import logging
from typing import Callable

import websockets

from src.auth import KalshiAuth
from src.models import Orderbook, OrderbookLevel

logger = logging.getLogger(__name__)


class OrderbookManager:
    def __init__(self):
        self._books: dict[str, Orderbook] = {}
        self._event_markets: dict[str, list[str]] = {}
        self._market_to_event: dict[str, str] = {}

    def register_event(self, event_ticker: str, market_tickers: list[str]):
        self._event_markets[event_ticker] = market_tickers
        for t in market_tickers:
            self._market_to_event[t] = event_ticker

    def unregister_event(self, event_ticker: str):
        tickers = self._event_markets.pop(event_ticker, [])
        for t in tickers:
            self._market_to_event.pop(t, None)
            self._books.pop(t, None)

    def get_event_for_market(self, market_ticker: str) -> str | None:
        return self._market_to_event.get(market_ticker)

    def apply_snapshot(self, ticker: str, snapshot: dict):
        yes_bids = [
            OrderbookLevel(price=float(p), quantity=float(q))
            for p, q in snapshot.get("yes_dollars_fp", [])
        ]
        no_bids = [
            OrderbookLevel(price=float(p), quantity=float(q))
            for p, q in snapshot.get("no_dollars_fp", [])
        ]
        self._books[ticker] = Orderbook(yes_bids=yes_bids, no_bids=no_bids)

    def apply_delta(self, ticker: str, delta: dict):
        book = self._books.get(ticker)
        if book is None:
            return

        price = float(delta["price_dollars"])
        delta_qty = float(delta["delta_fp"])
        side = delta["side"]
        levels = book.yes_bids if side == "yes" else book.no_bids

        existing = None
        for level in levels:
            if abs(level.price - price) < 1e-9:
                existing = level
                break

        if existing:
            existing.quantity += delta_qty
            if existing.quantity <= 0:
                levels.remove(existing)
        elif delta_qty > 0:
            levels.append(OrderbookLevel(price=price, quantity=delta_qty))

    def get_orderbook(self, ticker: str) -> Orderbook | None:
        return self._books.get(ticker)

    def get_event_orderbooks(self, event_ticker: str) -> dict[str, Orderbook]:
        tickers = self._event_markets.get(event_ticker, [])
        result = {}
        for t in tickers:
            book = self._books.get(t)
            if book:
                result[t] = book
        return result


class MarketScanner:
    def __init__(
        self,
        ws_url: str,
        auth: KalshiAuth,
        orderbook_mgr: OrderbookManager,
        on_orderbook_update: Callable[[str], None] | None = None,
    ):
        self.ws_url = ws_url
        self.auth = auth
        self.orderbook_mgr = orderbook_mgr
        self.on_orderbook_update = on_orderbook_update
        self._ws = None
        self._sub_id = 0
        self._running = False

    async def connect(self):
        headers = self.auth.build_headers("GET", "/trade-api/ws/v2")
        self._ws = await websockets.connect(self.ws_url, additional_headers=headers)
        self._running = True
        logger.info("WebSocket connected")

    async def subscribe(self, market_tickers: list[str]):
        if not self._ws:
            return
        self._sub_id += 1
        msg = {
            "id": self._sub_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": market_tickers,
            },
        }
        await self._ws.send(json.dumps(msg))
        logger.info(f"Subscribed to orderbook_delta for {len(market_tickers)} markets")

    async def subscribe_fills(self):
        if not self._ws:
            return
        self._sub_id += 1
        msg = {
            "id": self._sub_id,
            "cmd": "subscribe",
            "params": {"channels": ["fill"]},
        }
        await self._ws.send(json.dumps(msg))
        logger.info("Subscribed to fill channel")

    async def listen(self):
        if not self._ws:
            return
        while self._running:
            try:
                raw = await self._ws.recv()
                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "orderbook_snapshot":
                    ticker = data["msg"]["market_ticker"]
                    self.orderbook_mgr.apply_snapshot(ticker, data["msg"])
                    if self.on_orderbook_update:
                        self.on_orderbook_update(ticker)

                elif msg_type == "orderbook_delta":
                    ticker = data["msg"]["market_ticker"]
                    self.orderbook_mgr.apply_delta(ticker, data["msg"])
                    if self.on_orderbook_update:
                        self.on_orderbook_update(ticker)

            except websockets.ConnectionClosed:
                logger.warning("WebSocket disconnected")
                self._running = False
                break
            except Exception:
                logger.exception("Error processing WebSocket message")

    async def close(self):
        self._running = False
        if self._ws:
            await self._ws.close()
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_scanner.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add src/scanner.py tests/test_scanner.py
git commit -m "feat: add WebSocket scanner with orderbook state management"
```

---

### Task 10: Execution Manager

**Files:**
- Create: `src/executor.py`
- Create: `tests/test_executor.py`

**Step 1: Write the failing tests**

Create `tests/test_executor.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.executor import ExecutionManager
from src.models import TradeSignal


def _make_executor(fill_timeout=5):
    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price_cents": round(yes_price * 100),
        "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order_id": "o1", "ticker": "M1", "status": "open"},
            {"order_id": "o2", "ticker": "M2", "status": "open"},
            {"order_id": "o3", "ticker": "M3", "status": "open"},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(api=api, positions=positions, fill_timeout_secs=fill_timeout), api, positions


def test_build_orders_from_signal():
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    orders = executor.build_orders(signal, quantity=10)
    assert len(orders) == 3
    assert orders[0]["ticker"] == "M1"
    assert orders[0]["yes_price_cents"] == 40
    assert orders[0]["count"] == 10


def test_execute_calls_batch_create():
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=10))
    api.batch_create_orders.assert_called_once()


def test_is_executing_flag():
    executor, _, _ = _make_executor()
    assert not executor.is_executing()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=10))
    # After execution completes, flag should be cleared
    assert not executor.is_executing()
```

**Step 2: Run test to verify it fails**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.executor'`

**Step 3: Write minimal implementation**

Create `src/executor.py`:

```python
import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.api import KalshiAPI
from src.models import TradeSignal, OrderStatus
from src.positions import PositionTracker

logger = logging.getLogger(__name__)


@dataclass
class ArbExecution:
    signal: TradeSignal
    order_ids: list[str] = field(default_factory=list)
    filled: dict[str, float] = field(default_factory=dict)
    started_at: float = 0.0


class ExecutionManager:
    def __init__(self, api: KalshiAPI, positions: PositionTracker, fill_timeout_secs: int):
        self.api = api
        self.positions = positions
        self.fill_timeout_secs = fill_timeout_secs
        self._executing = False
        self._active: ArbExecution | None = None

    def is_executing(self) -> bool:
        return self._executing

    def build_orders(self, signal: TradeSignal, quantity: int) -> list[dict]:
        return [
            self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=quantity)
            for ticker, price in signal.legs
        ]

    async def execute(self, signal: TradeSignal, quantity: int = 1):
        if self._executing:
            logger.warning("Already executing, skipping signal for %s", signal.event_ticker)
            return

        self._executing = True
        try:
            orders = self.build_orders(signal, quantity)
            logger.info(
                "Executing arb on %s: %d legs, profit=%.4f (%.2f%%)",
                signal.event_ticker, len(signal.legs), signal.net_profit, signal.profit_pct,
            )

            response = await self.api.batch_create_orders(orders)
            order_list = response.get("orders", [])
            execution = ArbExecution(
                signal=signal,
                order_ids=[o["order_id"] for o in order_list],
                started_at=time.time(),
            )
            self._active = execution

            await self._monitor_fills(execution)
        finally:
            self._executing = False
            self._active = None

    async def _monitor_fills(self, execution: ArbExecution):
        deadline = execution.started_at + self.fill_timeout_secs
        while time.time() < deadline:
            if len(execution.filled) == len(execution.order_ids):
                logger.info("All legs filled for %s", execution.signal.event_ticker)
                return
            await asyncio.sleep(0.5)

        unfilled = [
            oid for oid in execution.order_ids if oid not in execution.filled
        ]
        if unfilled:
            logger.warning(
                "Timeout: %d unfilled legs for %s, cancelling",
                len(unfilled), execution.signal.event_ticker,
            )
            await self.api.batch_cancel_orders(unfilled)

    def handle_fill(self, fill_data: dict):
        order_id = fill_data.get("order_id", "")
        ticker = fill_data.get("ticker", "")
        price = float(fill_data.get("yes_price_cents", 0)) / 100.0
        quantity = int(fill_data.get("count", 0))
        action = fill_data.get("action", "sell")
        side = fill_data.get("side", "yes")

        self.positions.record_fill(
            ticker=ticker,
            side=side,
            price=price,
            quantity=quantity,
            action=action,
        )

        if self._active and order_id in self._active.order_ids:
            self._active.filled[order_id] = price
            logger.info("Leg filled: %s @ %.2f (%d/%d)",
                        ticker, price, len(self._active.filled), len(self._active.order_ids))
```

**Step 4: Run test to verify it passes**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/test_executor.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/executor.py tests/test_executor.py
git commit -m "feat: add execution manager with batch orders and fill timeout"
```

---

### Task 11: Main Entry Point

**Files:**
- Create: `src/main.py`

**Step 1: Write the main application**

Create `src/main.py`:

```python
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from src.auth import KalshiAuth
from src.api import KalshiAPI
from src.config import load_config
from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.positions import PositionTracker
from src.scanner import MarketScanner, OrderbookManager

logger = logging.getLogger("kalshi-arb")


class ArbBot:
    def __init__(self, config_path: str):
        self.cfg = load_config(config_path)
        self.auth = KalshiAuth(
            api_key_id=self.cfg.api_key_id,
            private_key_path=self.cfg.private_key_path,
        )
        self.api = KalshiAPI(base_url=self.cfg.rest_base_url, auth=self.auth)
        self.orderbook_mgr = OrderbookManager()
        self.engine = ArbEngine(
            min_profit_pct=self.cfg.min_profit_pct,
            max_exposure_ratio=self.cfg.max_exposure_ratio,
        )
        self.positions = PositionTracker()
        self.executor = ExecutionManager(
            api=self.api,
            positions=self.positions,
            fill_timeout_secs=self.cfg.fill_timeout_secs,
        )
        self.scanner = MarketScanner(
            ws_url=self.cfg.ws_url,
            auth=self.auth,
            orderbook_mgr=self.orderbook_mgr,
            on_orderbook_update=self._on_orderbook_update,
        )
        self._event_tickers: set[str] = set()

    def _setup_logging(self):
        log_dir = Path(self.cfg.log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(self.cfg.log_file)
        handler.setFormatter(logging.Formatter(
            '{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)s}'
        ))
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        root.setLevel(getattr(logging, self.cfg.log_level))
        root.addHandler(handler)
        root.addHandler(console)

    def _on_orderbook_update(self, market_ticker: str):
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            return

        event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
        signal = self.engine.evaluate(event_ticker, event_books)

        if signal and not self.executor.is_executing():
            logger.info(
                json.dumps({
                    "event": "arb_detected",
                    "event_ticker": event_ticker,
                    "legs": signal.legs,
                    "net_profit": round(signal.net_profit, 6),
                    "profit_pct": round(signal.profit_pct, 2),
                    "exposure_ratio": round(signal.exposure_ratio, 2),
                })
            )
            asyncio.get_event_loop().create_task(self.executor.execute(signal))

    async def _discover_events(self):
        while True:
            try:
                events = await self.api.fetch_events()
                new_tickers = []
                for event in events:
                    if event.event_ticker not in self._event_tickers:
                        self._event_tickers.add(event.event_ticker)
                        market_tickers = event.market_tickers()
                        self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                        new_tickers.extend(market_tickers)
                        logger.info(
                            "Discovered event %s (%s) with %d markets",
                            event.event_ticker, event.title, len(market_tickers),
                        )

                if new_tickers:
                    await self.scanner.subscribe(new_tickers)
                    logger.info("Subscribed to %d new markets", len(new_tickers))

            except Exception:
                logger.exception("Error discovering events")

            await asyncio.sleep(self.cfg.event_poll_interval_secs)

    async def run(self):
        self._setup_logging()
        logger.info("Starting Kalshi Arb Bot in %s mode", self.cfg.mode.upper())

        await self.scanner.connect()
        await self.scanner.subscribe_fills()

        discovery_task = asyncio.create_task(self._discover_events())
        listen_task = asyncio.create_task(self.scanner.listen())

        try:
            await asyncio.gather(discovery_task, listen_task)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.scanner.close()
            await self.api.close()


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Copy config.example.yaml to config.yaml and fill in your credentials.")
        sys.exit(1)

    bot = ArbBot(config_path)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
```

**Step 2: Verify syntax**

Run: `cd /home/pi/source/kalshi-arb && python3 -c "import ast; ast.parse(open('src/main.py').read()); print('Syntax OK')"`
Expected: `Syntax OK`

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add main entry point wiring all components together"
```

---

### Task 12: Integration Smoke Test

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write an integration test**

This test wires the real components together (except the WebSocket/API) to verify the full pipeline from orderbook update → arb detection → order building.

Create `tests/test_integration.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.models import OrderbookLevel
from src.positions import PositionTracker
from src.scanner import OrderbookManager


def test_full_pipeline_detects_and_builds_orders():
    """Wire real components, feed orderbook data, verify arb detection and order building."""
    orderbook_mgr = OrderbookManager()
    engine = ArbEngine(min_profit_pct=1.0, max_exposure_ratio=5.0)
    positions = PositionTracker()

    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price_cents": round(yes_price * 100),
        "count": quantity,
    })

    executor = ExecutionManager(api=api, positions=positions, fill_timeout_secs=5)

    orderbook_mgr.register_event("E1", ["M1", "M2", "M3"])

    orderbook_mgr.apply_snapshot("M1", {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M2", {
        "market_ticker": "M2",
        "yes_dollars_fp": [["0.3500", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M3", {
        "market_ticker": "M3",
        "yes_dollars_fp": [["0.3500", "100.00"]],
        "no_dollars_fp": [],
    })

    event_books = orderbook_mgr.get_event_orderbooks("E1")
    signal = engine.evaluate("E1", event_books)

    assert signal is not None
    assert signal.net_profit > 0

    orders = executor.build_orders(signal, quantity=10)
    assert len(orders) == 3
    tickers = {o["ticker"] for o in orders}
    assert tickers == {"M1", "M2", "M3"}


def test_full_pipeline_no_arb():
    """Verify pipeline correctly rejects non-profitable events."""
    orderbook_mgr = OrderbookManager()
    engine = ArbEngine(min_profit_pct=2.0, max_exposure_ratio=3.0)

    orderbook_mgr.register_event("E1", ["M1", "M2", "M3"])

    orderbook_mgr.apply_snapshot("M1", {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.3000", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M2", {
        "market_ticker": "M2",
        "yes_dollars_fp": [["0.3000", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M3", {
        "market_ticker": "M3",
        "yes_dollars_fp": [["0.3000", "100.00"]],
        "no_dollars_fp": [],
    })

    event_books = orderbook_mgr.get_event_orderbooks("E1")
    signal = engine.evaluate("E1", event_books)
    assert signal is None
```

**Step 2: Run all tests**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/ -v`
Expected: All tests pass (24+ tests)

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration smoke test for full arb pipeline"
```

---

### Task 13: Final Cleanup and Push

**Step 1: Run full test suite one more time**

Run: `cd /home/pi/source/kalshi-arb && python3 -m pytest tests/ -v --tb=short`
Expected: All pass

**Step 2: Push to GitHub**

```bash
git push origin main
```

**Step 3: Verify on GitHub**

Run: `gh repo view HurleySk/kalshi-arb --web` or check the repo page to confirm all files are pushed.

---

## Post-Implementation

After completing all tasks, the bot can be run with:

```bash
cd /home/pi/source/kalshi-arb
cp config.example.yaml config.yaml
# Edit config.yaml with your Kalshi API credentials
python3 -m src.main
```

First test with `mode: demo` using the Kalshi sandbox environment before switching to `mode: live`.
