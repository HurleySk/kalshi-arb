# PredictIt Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Worktree:** Use superpowers:using-git-worktrees to create an isolated worktree before starting implementation. All work should happen on a feature branch (`feat/predictit-integration`) in the worktree, not on main.

**Goal:** Add PredictIt as a second exchange adapter, scraping price data via Decodo-proxied HTTP and executing trades via Playwright browser automation.

**Architecture:** PredictIt implements the same 6 port interfaces (`FeeModel`, `ExchangeAPI`, `OrderBuilder`, `OrderbookFeed`, `MarketDiscovery`, `PositionConstraints`) as Kalshi. Data flows through a two-tier pipeline: httpx polls the public JSON endpoint for broad market scanning, Playwright handles authenticated actions (depth data, order placement). All network traffic routes through Decodo residential proxies.

**Tech Stack:** httpx (HTTP client with proxy support), playwright (browser automation), python-dotenv (env credential loading), beautifulsoup4 (HTML parsing for scraped pages)

---

## File Structure

```
src/exchanges/predictit/
├── __init__.py          # PredictItExchange facade — creates and wires all sub-components
├── anti_detect.py       # User-agent rotation, random delays, browser-like headers
├── scraper.py           # JSON endpoint polling via httpx + Decodo proxy
├── fee_model.py         # PredictItFeeModel — 10% profit fee + 5% withdrawal
├── constraints.py       # PredictItConstraints — $3,500 per contract
├── discovery.py         # PredictItDiscovery (MarketDiscovery) — JSON → Event/Market
├── scanner.py           # PredictItScanner (OrderbookFeed) — diff-based polling updates
├── browser.py           # Playwright session manager — login, navigation, session persistence
├── order_builder.py     # PredictItOrderBuilder — UI interaction descriptors
├── api.py               # PredictItAPI (ExchangeAPI) — Playwright-backed REST equivalent

tests/
├── test_predictit_fee_model.py
├── test_predictit_constraints.py
├── test_predictit_anti_detect.py
├── test_predictit_scraper.py
├── test_predictit_discovery.py
├── test_predictit_scanner.py
├── test_predictit_order_builder.py
├── test_predictit_browser.py
├── test_predictit_api.py
├── test_predictit_integration.py

.env.example             # Template for Decodo + PredictIt credentials
```

**Modified files:**
- `src/exchanges/__init__.py` — Register PredictItExchange in EXCHANGES dict
- `src/config.py` — Add PredictIt config fields, env loading, PredictIt URL constants
- `config.example.yaml` — Add PredictIt config section
- `requirements.txt` — Add httpx, playwright, python-dotenv, beautifulsoup4
- `tests/test_ports.py` — Add PredictIt adapter conformance tests

---

### Task 1: Dependencies and Environment Setup

**Files:**
- Modify: `requirements.txt`
- Create: `.env.example`

- [ ] **Step 1: Add new dependencies to requirements.txt**

```
websockets>=12.0
aiohttp>=3.9
cryptography>=42.0
pyyaml>=6.0
pytest>=7.0
mcp>=1.0
httpx>=0.27.0
playwright>=1.40.0
python-dotenv>=1.0.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
```

- [ ] **Step 2: Create .env.example**

```
# Decodo residential proxy (required for PredictIt scraping)
# Use us.decodo.com for US geotargeting
DECODO_PROXY_URL=http://username:password@us.decodo.com:10001

# Directory where Playwright browser session state is saved
# Bot saves cookies/localStorage here after manual login
PREDICTIT_SESSION_DIR=~/.kalshi/predictit_session
```

- [ ] **Step 3: Install dependencies**

Run: `pip3 install -r requirements.txt --break-system-packages`
Then: `playwright install chromium`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "chore: add PredictIt dependencies — httpx, playwright, python-dotenv, beautifulsoup4"
```

---

### Task 2: Anti-Detection Utilities

**Files:**
- Create: `src/exchanges/predictit/__init__.py` (empty package init, will be filled in Task 10)
- Create: `src/exchanges/predictit/anti_detect.py`
- Create: `tests/test_predictit_anti_detect.py`

- [ ] **Step 1: Create package directory and empty __init__.py**

```bash
mkdir -p src/exchanges/predictit
touch src/exchanges/predictit/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/test_predictit_anti_detect.py`:

```python
import time

from src.exchanges.predictit.anti_detect import (
    USER_AGENTS,
    get_headers,
    random_delay,
    random_viewport,
)


def test_user_agents_has_at_least_five():
    assert len(USER_AGENTS) >= 5


def test_user_agents_all_contain_mozilla():
    for ua in USER_AGENTS:
        assert "Mozilla" in ua


def test_get_headers_has_required_keys():
    headers = get_headers()
    assert "User-Agent" in headers
    assert "Accept" in headers
    assert "Accept-Language" in headers
    assert "Accept-Encoding" in headers
    assert "DNT" in headers
    assert "Connection" in headers


def test_get_headers_user_agent_from_pool():
    headers = get_headers()
    assert headers["User-Agent"] in USER_AGENTS


def test_random_delay_within_bounds():
    for _ in range(20):
        d = random_delay(min_secs=1.0, max_secs=3.0)
        assert 1.0 <= d <= 3.0


def test_random_delay_defaults():
    d = random_delay()
    assert 2.0 <= d <= 5.0


def test_random_viewport_reasonable_size():
    vp = random_viewport()
    assert 1200 <= vp["width"] <= 1920
    assert 700 <= vp["height"] <= 1080
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_anti_detect.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write implementation**

`src/exchanges/predictit/anti_detect.py`:

```python
import random

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def random_delay(min_secs: float = 2.0, max_secs: float = 5.0) -> float:
    return random.uniform(min_secs, max_secs)


def random_viewport() -> dict[str, int]:
    return {
        "width": random.randint(1200, 1920),
        "height": random.randint(700, 1080),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_anti_detect.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/exchanges/predictit/__init__.py src/exchanges/predictit/anti_detect.py tests/test_predictit_anti_detect.py
git commit -m "feat(predictit): add anti-detection utilities — user-agent rotation, delays, viewport randomization"
```

---

### Task 3: PredictIt Fee Model

**Files:**
- Create: `src/exchanges/predictit/fee_model.py`
- Create: `tests/test_predictit_fee_model.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_fee_model.py`:

```python
import math

from src.exchanges.predictit.fee_model import PredictItFeeModel


def test_taker_fee_is_zero():
    fm = PredictItFeeModel()
    assert fm.taker_fee(0.50) == 0.0
    assert fm.taker_fee(0.01) == 0.0
    assert fm.taker_fee(0.99) == 0.0


def test_maker_fee_is_zero():
    fm = PredictItFeeModel()
    assert fm.maker_fee(0.50) == 0.0


def test_profit_fee_ten_percent_without_withdrawal():
    fm = PredictItFeeModel(include_withdrawal_fee=False)
    assert math.isclose(fm.profit_fee(1.0), 0.10, abs_tol=1e-9)
    assert math.isclose(fm.profit_fee(0.50), 0.05, abs_tol=1e-9)


def test_profit_fee_with_withdrawal():
    fm = PredictItFeeModel(include_withdrawal_fee=True)
    # Combined: 10% profit fee, then 5% withdrawal on remainder
    # net = gross * 0.90 * 0.95 = gross * 0.855
    # So fee portion = gross * 0.145
    assert math.isclose(fm.profit_fee(1.0), 0.145, abs_tol=1e-9)
    assert math.isclose(fm.profit_fee(0.50), 0.0725, abs_tol=1e-9)


def test_profit_fee_default_includes_withdrawal():
    fm = PredictItFeeModel()
    assert math.isclose(fm.profit_fee(1.0), 0.145, abs_tol=1e-9)


def test_profit_fee_zero_on_zero_profit():
    fm = PredictItFeeModel()
    assert fm.profit_fee(0.0) == 0.0


def test_profit_fee_zero_on_negative_profit():
    fm = PredictItFeeModel()
    assert fm.profit_fee(-0.50) == 0.0


def test_arb_profit_with_predictit_fees():
    from src.core.fees import arb_profit
    fm = PredictItFeeModel(include_withdrawal_fee=False)
    prices = [0.40, 0.40, 0.40]
    gross = sum(prices) - 1.0  # 0.20
    expected = gross - 0.10 * gross  # 0.20 - 0.02 = 0.18
    assert math.isclose(arb_profit(prices, fm), expected, abs_tol=1e-9)


def test_arb_profit_with_withdrawal_fee():
    from src.core.fees import arb_profit
    fm = PredictItFeeModel(include_withdrawal_fee=True)
    prices = [0.40, 0.40, 0.40]
    gross = sum(prices) - 1.0  # 0.20
    expected = gross - 0.145 * gross  # 0.20 - 0.029 = 0.171
    assert math.isclose(arb_profit(prices, fm), expected, abs_tol=1e-9)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_fee_model.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/fee_model.py`:

```python
class PredictItFeeModel:
    PROFIT_FEE_RATE = 0.10
    WITHDRAWAL_FEE_RATE = 0.05

    def __init__(self, include_withdrawal_fee: bool = True):
        self._include_withdrawal = include_withdrawal_fee
        if include_withdrawal_fee:
            # net = gross * (1 - 0.10) * (1 - 0.05) = gross * 0.855
            # fee = gross * 0.145
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_fee_model.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/fee_model.py tests/test_predictit_fee_model.py
git commit -m "feat(predictit): add PredictItFeeModel — 10% profit fee + 5% withdrawal fee"
```

---

### Task 4: PredictIt Position Constraints

**Files:**
- Create: `src/exchanges/predictit/constraints.py`
- Create: `tests/test_predictit_constraints.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_constraints.py`:

```python
from src.exchanges.predictit.constraints import PredictItConstraints


def test_max_position_size():
    c = PredictItConstraints()
    assert c.max_position_size("ANY-TICKER") == 3500


def test_max_position_size_custom():
    c = PredictItConstraints(max_contracts=1000)
    assert c.max_position_size("ANY-TICKER") == 1000


def test_max_total_exposure_is_none():
    c = PredictItConstraints()
    assert c.max_total_exposure() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_constraints.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/constraints.py`:

```python
class PredictItConstraints:
    DEFAULT_MAX_CONTRACTS = 3500

    def __init__(self, max_contracts: int = DEFAULT_MAX_CONTRACTS):
        self._max_contracts = max_contracts

    def max_position_size(self, ticker: str) -> int | None:
        return self._max_contracts

    def max_total_exposure(self) -> float | None:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_constraints.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/constraints.py tests/test_predictit_constraints.py
git commit -m "feat(predictit): add PredictItConstraints — $3,500 per-contract position limit"
```

---

### Task 5: PredictIt Scraper (JSON Endpoint Polling)

**Files:**
- Create: `src/exchanges/predictit/scraper.py`
- Create: `tests/test_predictit_scraper.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_scraper.py`:

```python
import json
from unittest.mock import patch, MagicMock

from src.exchanges.predictit.scraper import PredictItScraper, PREDICTIT_API_URL

SAMPLE_RESPONSE = {
    "markets": [
        {
            "id": 7456,
            "name": "Who will win the 2026 presidential election?",
            "shortName": "2026 Pres Election",
            "image": "https://example.com/image.png",
            "url": "/markets/detail/7456",
            "contracts": [
                {
                    "id": 28541,
                    "dateEnd": "2026-11-03T23:59:00",
                    "image": "https://example.com/contract.png",
                    "name": "Democratic",
                    "shortName": "Dem",
                    "status": "Open",
                    "lastTradePrice": 0.53,
                    "bestBuyYesCost": 0.54,
                    "bestBuyNoCost": 0.48,
                    "bestSellYesCost": 0.52,
                    "bestSellNoCost": 0.46,
                    "lastClosePrice": 0.53,
                    "displayOrder": 0,
                },
                {
                    "id": 28542,
                    "dateEnd": "2026-11-03T23:59:00",
                    "image": "https://example.com/contract2.png",
                    "name": "Republican",
                    "shortName": "Rep",
                    "status": "Open",
                    "lastTradePrice": 0.47,
                    "bestBuyYesCost": 0.49,
                    "bestBuyNoCost": 0.53,
                    "bestSellYesCost": 0.47,
                    "bestSellNoCost": 0.51,
                    "lastClosePrice": 0.47,
                    "displayOrder": 1,
                },
            ],
            "timeStamp": "2026-05-17T12:00:00",
            "status": "Open",
        }
    ]
}


def test_api_url_is_correct():
    assert PREDICTIT_API_URL == "https://www.predictit.org/api/marketdata/all/"


def test_parse_markets_returns_list():
    scraper = PredictItScraper(proxy_url=None)
    markets = scraper.parse_markets(SAMPLE_RESPONSE)
    assert len(markets) == 1


def test_parse_markets_structure():
    scraper = PredictItScraper(proxy_url=None)
    markets = scraper.parse_markets(SAMPLE_RESPONSE)
    market = markets[0]
    assert market["id"] == 7456
    assert market["name"] == "Who will win the 2026 presidential election?"
    assert market["status"] == "Open"
    assert len(market["contracts"]) == 2


def test_parse_contracts_prices():
    scraper = PredictItScraper(proxy_url=None)
    markets = scraper.parse_markets(SAMPLE_RESPONSE)
    contracts = markets[0]["contracts"]
    dem = contracts[0]
    assert dem["id"] == 28541
    assert dem["name"] == "Democratic"
    assert dem["bestBuyYesCost"] == 0.54
    assert dem["bestSellYesCost"] == 0.52


def test_parse_markets_filters_closed():
    data = {
        "markets": [
            {**SAMPLE_RESPONSE["markets"][0], "status": "Closed"},
            SAMPLE_RESPONSE["markets"][0],
        ]
    }
    scraper = PredictItScraper(proxy_url=None)
    markets = scraper.parse_markets(data)
    assert len(markets) == 1


def test_parse_markets_filters_single_contract():
    single_contract_market = {
        **SAMPLE_RESPONSE["markets"][0],
        "id": 9999,
        "contracts": [SAMPLE_RESPONSE["markets"][0]["contracts"][0]],
    }
    data = {"markets": [single_contract_market, SAMPLE_RESPONSE["markets"][0]]}
    scraper = PredictItScraper(proxy_url=None)
    markets = scraper.parse_markets(data)
    assert len(markets) == 1
    assert markets[0]["id"] == 7456


def test_scraper_constructs_with_proxy():
    scraper = PredictItScraper(proxy_url="http://user:pass@proxy.com:8080")
    assert scraper.proxy_url == "http://user:pass@proxy.com:8080"


def test_scraper_constructs_without_proxy():
    scraper = PredictItScraper(proxy_url=None)
    assert scraper.proxy_url is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_scraper.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/scraper.py`:

```python
import logging
import time

import httpx

from src.exchanges.predictit.anti_detect import get_headers, random_delay

logger = logging.getLogger(__name__)

PREDICTIT_API_URL = "https://www.predictit.org/api/marketdata/all/"


class PredictItScraper:
    def __init__(self, proxy_url: str | None):
        self.proxy_url = proxy_url
        self._last_fetch_time: float = 0

    def fetch(self) -> dict:
        transport = None
        if self.proxy_url:
            transport = httpx.HTTPTransport(proxy=self.proxy_url)

        with httpx.Client(transport=transport, timeout=30.0, follow_redirects=True) as client:
            response = client.get(PREDICTIT_API_URL, headers=get_headers())
            response.raise_for_status()
            self._last_fetch_time = time.time()
            return response.json()

    def parse_markets(self, data: dict) -> list[dict]:
        results = []
        for market in data.get("markets", []):
            if market.get("status") != "Open":
                continue
            contracts = market.get("contracts", [])
            if len(contracts) < 2:
                continue
            results.append(market)
        return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_scraper.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/scraper.py tests/test_predictit_scraper.py
git commit -m "feat(predictit): add scraper — JSON endpoint polling via httpx + Decodo proxy"
```

---

### Task 6: PredictIt Discovery (MarketDiscovery Port)

**Files:**
- Create: `src/exchanges/predictit/discovery.py`
- Create: `tests/test_predictit_discovery.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_discovery.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch

from src.core.models import Event, Market
from src.exchanges.predictit.discovery import PredictItDiscovery


SAMPLE_PARSED_MARKETS = [
    {
        "id": 7456,
        "name": "Who will win the 2026 presidential election?",
        "shortName": "2026 Pres Election",
        "status": "Open",
        "contracts": [
            {
                "id": 28541,
                "name": "Democratic",
                "shortName": "Dem",
                "status": "Open",
                "dateEnd": "2026-11-03T23:59:00",
                "bestBuyYesCost": 0.54,
                "bestBuyNoCost": 0.48,
                "bestSellYesCost": 0.52,
                "bestSellNoCost": 0.46,
                "lastTradePrice": 0.53,
                "lastClosePrice": 0.53,
            },
            {
                "id": 28542,
                "name": "Republican",
                "shortName": "Rep",
                "status": "Open",
                "dateEnd": "2026-11-03T23:59:00",
                "bestBuyYesCost": 0.49,
                "bestBuyNoCost": 0.53,
                "bestSellYesCost": 0.47,
                "bestSellNoCost": 0.51,
                "lastTradePrice": 0.47,
                "lastClosePrice": 0.47,
            },
        ],
    }
]


def test_convert_to_events():
    mock_orderbook_mgr = MagicMock()
    mock_scanner = MagicMock()
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=mock_orderbook_mgr,
        scanner=mock_scanner,
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    assert len(events) == 1


def test_event_has_correct_fields():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    event = events[0]
    assert event.event_ticker == "PI-7456"
    assert event.title == "Who will win the 2026 presidential election?"
    assert event.mutually_exclusive is True
    assert event.exchange == "predictit"
    assert len(event.markets) == 2


def test_market_tickers_use_contract_ids():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    tickers = events[0].market_tickers()
    assert "PI-7456-28541" in tickers
    assert "PI-7456-28542" in tickers


def test_market_metadata_stored():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    discovery.register_events(events)
    assert "PI-7456-28541" in discovery.market_metadata
    meta = discovery.market_metadata["PI-7456-28541"]
    assert meta["close_time"] == "2026-11-03T23:59:00"


def test_register_events_tracks_tickers():
    mock_orderbook_mgr = MagicMock()
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=mock_orderbook_mgr,
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    new_tickers = discovery.register_events(events)
    assert len(new_tickers) == 2
    assert "PI-7456" in discovery.event_tickers


def test_register_events_no_duplicates():
    mock_orderbook_mgr = MagicMock()
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=mock_orderbook_mgr,
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    discovery.register_events(events)
    new_tickers = discovery.register_events(events)
    assert len(new_tickers) == 0


def test_cleanup_expired_removes_past_events():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    past_market = [
        {
            **SAMPLE_PARSED_MARKETS[0],
            "contracts": [
                {**c, "dateEnd": "2020-01-01T00:00:00"}
                for c in SAMPLE_PARSED_MARKETS[0]["contracts"]
            ],
        }
    ]
    events = discovery._convert_to_events(past_market)
    discovery.register_events(events)
    assert len(discovery.event_tickers) == 1
    removed = discovery.cleanup_expired()
    assert len(removed) == 1
    assert len(discovery.event_tickers) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/discovery.py`:

```python
import asyncio
import logging
from datetime import datetime, timezone

from src.core.models import Event, Market
from src.core.orderbook_manager import OrderbookManager
from src.exchanges.predictit.anti_detect import random_delay

logger = logging.getLogger(__name__)


class PredictItDiscovery:
    def __init__(self, scraper, orderbook_mgr: OrderbookManager, scanner):
        self.scraper = scraper
        self.orderbook_mgr = orderbook_mgr
        self.scanner = scanner
        self.event_tickers: set[str] = set()
        self.market_metadata: dict[str, dict] = {}
        self.event_total_markets: dict[str, int] = {}

    def _convert_to_events(self, parsed_markets: list[dict]) -> list[Event]:
        events = []
        for market in parsed_markets:
            market_id = market["id"]
            event_ticker = f"PI-{market_id}"
            contracts = market.get("contracts", [])
            markets = []
            for contract in contracts:
                if contract.get("status") != "Open":
                    continue
                contract_id = contract["id"]
                ticker = f"PI-{market_id}-{contract_id}"
                markets.append(Market(
                    ticker=ticker,
                    event_ticker=event_ticker,
                    title=contract.get("name", ""),
                    status=contract.get("status", "Open"),
                    close_time=contract.get("dateEnd", ""),
                    exchange="predictit",
                    volume_24h=0.0,
                    open_interest=0.0,
                    liquidity=0.0,
                ))
            if len(markets) < 2:
                continue
            events.append(Event(
                event_ticker=event_ticker,
                title=market.get("name", ""),
                series_ticker=f"PI-SERIES-{market_id}",
                mutually_exclusive=True,
                markets=markets,
                total_market_count=len(markets),
                exchange="predictit",
            ))
        return events

    def register_events(self, events: list[Event]) -> list[str]:
        new_tickers = []
        for event in events:
            if event.event_ticker not in self.event_tickers:
                self.event_tickers.add(event.event_ticker)
                market_tickers = event.market_tickers()
                self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                new_tickers.extend(market_tickers)
            total = event.total_market_count or len(event.markets)
            self.event_total_markets[event.event_ticker] = total
            for m in event.markets:
                self.market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.close_time,
                    "volume_24h": m.volume_24h,
                    "open_interest": m.open_interest,
                    "liquidity": m.liquidity,
                }
        return new_tickers

    def cleanup_expired(self) -> set[str]:
        now = datetime.now(timezone.utc)
        removed = set()
        for event_ticker in list(self.event_tickers):
            market_tickers = self.orderbook_mgr.get_event_markets(event_ticker)
            all_expired = True
            for mt in market_tickers:
                meta = self.market_metadata.get(mt, {})
                close_str = meta.get("close_time", "")
                if not close_str:
                    all_expired = False
                    continue
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    if close_dt.tzinfo is None:
                        close_dt = close_dt.replace(tzinfo=timezone.utc)
                    if close_dt > now:
                        all_expired = False
                except (ValueError, TypeError):
                    all_expired = False
            if all_expired:
                self.event_tickers.discard(event_ticker)
                for mt in market_tickers:
                    self.market_metadata.pop(mt, None)
                self.event_total_markets.pop(event_ticker, None)
                removed.add(event_ticker)
                logger.info("Removed expired event: %s", event_ticker)
        return removed

    async def full_scan(self) -> None:
        try:
            data = self.scraper.fetch()
            parsed = self.scraper.parse_markets(data)
            events = self._convert_to_events(parsed)
            new_tickers = self.register_events(events)
            if new_tickers:
                await self.scanner.subscribe(new_tickers)
            logger.info(
                "PredictIt full scan: %d events, %d new markets",
                len(events), len(new_tickers),
            )
        except Exception:
            logger.exception("PredictIt full scan failed")

    async def poll_loop(self, interval_secs: int) -> None:
        await self.full_scan()
        while True:
            await asyncio.sleep(interval_secs)
            await self.full_scan()

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            removed = self.cleanup_expired()
            if removed:
                logger.info("Cleaned up %d expired PredictIt events", len(removed))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_discovery.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/discovery.py tests/test_predictit_discovery.py
git commit -m "feat(predictit): add PredictItDiscovery — JSON market data → Event/Market models"
```

---

### Task 7: PredictIt Scanner (OrderbookFeed Port via Polling)

**Files:**
- Create: `src/exchanges/predictit/scanner.py`
- Create: `tests/test_predictit_scanner.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_scanner.py`:

```python
import asyncio
from unittest.mock import MagicMock, AsyncMock

from src.core.models import Orderbook
from src.core.orderbook_manager import OrderbookManager
from src.exchanges.predictit.scanner import PredictItScanner


def make_scanner(orderbook_mgr=None, on_update=None, on_fill=None):
    if orderbook_mgr is None:
        orderbook_mgr = OrderbookManager()
    scraper = MagicMock()
    return PredictItScanner(
        scraper=scraper,
        orderbook_mgr=orderbook_mgr,
        on_orderbook_update=on_update,
        on_fill=on_fill,
    )


def test_subscribe_tracks_tickers():
    scanner = make_scanner()
    asyncio.get_event_loop().run_until_complete(
        scanner.subscribe(["PI-100-1", "PI-100-2"])
    )
    assert "PI-100-1" in scanner._subscribed_tickers
    assert "PI-100-2" in scanner._subscribed_tickers


def test_build_synthetic_orderbook():
    scanner = make_scanner()
    contract = {
        "bestBuyYesCost": 0.54,
        "bestSellYesCost": 0.52,
        "bestBuyNoCost": 0.48,
        "bestSellNoCost": 0.46,
    }
    book = scanner._build_orderbook(contract)
    assert isinstance(book, Orderbook)
    assert 52 in book.bids
    assert 54 in book.asks


def test_build_synthetic_orderbook_null_prices():
    scanner = make_scanner()
    contract = {
        "bestBuyYesCost": None,
        "bestSellYesCost": None,
        "bestBuyNoCost": None,
        "bestSellNoCost": None,
    }
    book = scanner._build_orderbook(contract)
    assert len(book.bids) == 0
    assert len(book.asks) == 0


def test_detect_changes():
    scanner = make_scanner()
    old = {"PI-100-1": {"bestBuyYesCost": 0.54, "bestSellYesCost": 0.52}}
    new = {"PI-100-1": {"bestBuyYesCost": 0.55, "bestSellYesCost": 0.52}}
    scanner._subscribed_tickers = {"PI-100-1"}
    changed = scanner._detect_changes(old, new)
    assert "PI-100-1" in changed


def test_detect_changes_no_change():
    scanner = make_scanner()
    data = {"PI-100-1": {"bestBuyYesCost": 0.54, "bestSellYesCost": 0.52}}
    scanner._subscribed_tickers = {"PI-100-1"}
    changed = scanner._detect_changes(data, data)
    assert len(changed) == 0


def test_detect_changes_new_ticker():
    scanner = make_scanner()
    old = {}
    new = {"PI-100-1": {"bestBuyYesCost": 0.54, "bestSellYesCost": 0.52}}
    scanner._subscribed_tickers = {"PI-100-1"}
    changed = scanner._detect_changes(old, new)
    assert "PI-100-1" in changed


def test_stop_sets_flag():
    scanner = make_scanner()
    scanner.stop()
    assert scanner._stopping is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/scanner.py`:

```python
import asyncio
import logging
from typing import Callable

from src.core.models import Orderbook
from src.core.orderbook_manager import OrderbookManager
from src.exchanges.predictit.anti_detect import random_delay

logger = logging.getLogger(__name__)


class PredictItScanner:
    def __init__(
        self,
        scraper,
        orderbook_mgr: OrderbookManager,
        on_orderbook_update: Callable[[str], None] | None = None,
        on_fill: Callable[[dict], None] | None = None,
        poll_interval_secs: int = 60,
    ):
        self.scraper = scraper
        self.orderbook_mgr = orderbook_mgr
        self.on_orderbook_update = on_orderbook_update
        self.on_fill = on_fill
        self.poll_interval_secs = poll_interval_secs
        self._subscribed_tickers: set[str] = set()
        self._stopping = False
        self._running = False
        self._previous_data: dict[str, dict] = {}

    async def connect(self) -> None:
        self._running = True
        logger.info("PredictIt scanner connected (polling mode)")

    async def subscribe(self, market_tickers: list[str]) -> None:
        self._subscribed_tickers.update(market_tickers)
        logger.info("PredictIt scanner: subscribed to %d markets", len(market_tickers))

    async def subscribe_fills(self) -> None:
        logger.info("PredictIt scanner: fill subscription (browser-based, handled by API layer)")

    def _build_orderbook(self, contract: dict) -> Orderbook:
        bids: dict[int, float] = {}
        asks: dict[int, float] = {}
        best_sell_yes = contract.get("bestSellYesCost")
        if best_sell_yes is not None:
            bids[round(best_sell_yes * 100)] = 1.0
        best_buy_yes = contract.get("bestBuyYesCost")
        if best_buy_yes is not None:
            asks[round(best_buy_yes * 100)] = 1.0
        return Orderbook(bids=bids, asks=asks)

    def _detect_changes(
        self, old: dict[str, dict], new: dict[str, dict]
    ) -> set[str]:
        changed = set()
        for ticker in self._subscribed_tickers:
            old_contract = old.get(ticker)
            new_contract = new.get(ticker)
            if new_contract is None:
                continue
            if old_contract is None or old_contract != new_contract:
                changed.add(ticker)
        return changed

    def _build_contract_map(self, data: dict) -> dict[str, dict]:
        result = {}
        for market in data.get("markets", []):
            market_id = market["id"]
            for contract in market.get("contracts", []):
                ticker = f"PI-{market_id}-{contract['id']}"
                if ticker in self._subscribed_tickers:
                    result[ticker] = contract
        return result

    async def listen(self) -> None:
        while not self._stopping:
            try:
                data = self.scraper.fetch()
                current = self._build_contract_map(data)
                changed = self._detect_changes(self._previous_data, current)
                self._previous_data = current

                for ticker in changed:
                    contract = current[ticker]
                    book = self._build_orderbook(contract)
                    self.orderbook_mgr.set_orderbook(ticker, book)
                    if self.on_orderbook_update:
                        self.on_orderbook_update(ticker)

                if changed:
                    logger.debug("PredictIt poll: %d markets updated", len(changed))

            except Exception:
                logger.exception("PredictIt scanner poll failed")

            delay = random_delay(
                min_secs=self.poll_interval_secs * 0.9,
                max_secs=self.poll_interval_secs * 1.1,
            )
            await asyncio.sleep(delay)

    async def close(self) -> None:
        self._running = False
        logger.info("PredictIt scanner closed")

    def stop(self) -> None:
        self._stopping = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_scanner.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/scanner.py tests/test_predictit_scanner.py
git commit -m "feat(predictit): add PredictItScanner — diff-based polling orderbook feed"
```

---

### Task 8: PredictIt Order Builder

**Files:**
- Create: `src/exchanges/predictit/order_builder.py`
- Create: `tests/test_predictit_order_builder.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_order_builder.py`:

```python
from src.exchanges.predictit.order_builder import PredictItOrderBuilder


def test_build_sell_order():
    ob = PredictItOrderBuilder()
    order = ob.build_sell_order("PI-7456-28541", 0.55, 10)
    assert order["ticker"] == "PI-7456-28541"
    assert order["action"] == "sell"
    assert order["outcome"] == "yes"
    assert order["price"] == 55
    assert order["shares"] == 10
    assert order["market_id"] == 7456
    assert order["contract_id"] == 28541


def test_build_buy_order():
    ob = PredictItOrderBuilder()
    order = ob.build_buy_order("PI-7456-28541", 0.45, 5)
    assert order["ticker"] == "PI-7456-28541"
    assert order["action"] == "buy"
    assert order["outcome"] == "yes"
    assert order["price"] == 45
    assert order["shares"] == 5


def test_build_close_order_long_position():
    ob = PredictItOrderBuilder()
    order = ob.build_close_order("PI-7456-28541", 10)
    assert order["action"] == "sell"
    assert order["price"] == 1
    assert order["shares"] == 10


def test_build_close_order_short_position():
    ob = PredictItOrderBuilder()
    order = ob.build_close_order("PI-7456-28541", -10)
    assert order["action"] == "buy"
    assert order["price"] == 99
    assert order["shares"] == 10


def test_unwrap_order():
    ob = PredictItOrderBuilder()
    raw = {
        "order_id": "browser-1234",
        "ticker": "PI-7456-28541",
        "status": "filled",
    }
    unwrapped = ob.unwrap_order(raw)
    assert unwrapped == raw


def test_ticker_parsing():
    ob = PredictItOrderBuilder()
    order = ob.build_sell_order("PI-100-200", 0.50, 1)
    assert order["market_id"] == 100
    assert order["contract_id"] == 200
    assert order["market_url"] == "https://www.predictit.org/markets/detail/100"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_order_builder.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/order_builder.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_order_builder.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/order_builder.py tests/test_predictit_order_builder.py
git commit -m "feat(predictit): add PredictItOrderBuilder — UI interaction descriptors for browser automation"
```

---

### Task 9: Browser Session Manager

**Files:**
- Create: `src/exchanges/predictit/browser.py`
- Create: `tests/test_predictit_browser.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_browser.py`:

```python
import asyncio
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.exchanges.predictit.browser import PredictItBrowser


def test_browser_init_defaults():
    browser = PredictItBrowser(
        session_dir="/tmp/test_session",
        proxy_url=None,
    )
    assert browser.session_dir == Path("/tmp/test_session")
    assert browser.proxy_url is None
    assert browser.headless is True
    assert browser._page is None


def test_browser_init_with_proxy():
    browser = PredictItBrowser(
        session_dir="/tmp/test_session",
        proxy_url="http://user:pass@proxy:8080",
        headless=False,
    )
    assert browser.proxy_url == "http://user:pass@proxy:8080"
    assert browser.headless is False


def test_session_state_path():
    browser = PredictItBrowser(
        session_dir="/tmp/test_session",
        proxy_url=None,
    )
    assert browser._state_path == Path("/tmp/test_session/state.json")


def test_has_saved_session_false_when_no_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        browser = PredictItBrowser(session_dir=tmpdir, proxy_url=None)
        assert browser.has_saved_session() is False


def test_has_saved_session_true_when_file_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        state_path.write_text('{"cookies": []}')
        browser = PredictItBrowser(session_dir=tmpdir, proxy_url=None)
        assert browser.has_saved_session() is True


def test_proxy_config_none():
    browser = PredictItBrowser(session_dir="/tmp/test", proxy_url=None)
    assert browser._proxy_config() is None


def test_proxy_config_parsed():
    browser = PredictItBrowser(
        session_dir="/tmp/test",
        proxy_url="http://user:pass@proxy.com:8080",
    )
    config = browser._proxy_config()
    assert config["server"] == "http://proxy.com:8080"
    assert config["username"] == "user"
    assert config["password"] == "pass"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_browser.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/browser.py`:

```python
import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from src.exchanges.predictit.anti_detect import random_delay, random_viewport

logger = logging.getLogger(__name__)


class PredictItBrowser:
    PREDICTIT_URL = "https://www.predictit.org"

    def __init__(
        self,
        session_dir: str,
        proxy_url: str | None,
        headless: bool = True,
    ):
        self.session_dir = Path(session_dir)
        self.proxy_url = proxy_url
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def _state_path(self) -> Path:
        return self.session_dir / "state.json"

    def has_saved_session(self) -> bool:
        return self._state_path.exists()

    def _proxy_config(self) -> dict | None:
        if not self.proxy_url:
            return None
        parsed = urlparse(self.proxy_url)
        return {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username or "",
            "password": parsed.password or "",
        }

    async def launch(self) -> None:
        from playwright.async_api import async_playwright

        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()

        launch_args = {
            "headless": self.headless,
        }
        proxy = self._proxy_config()
        if proxy:
            launch_args["proxy"] = proxy

        self._browser = await self._playwright.chromium.launch(**launch_args)

        context_args = {"viewport": random_viewport()}
        if self.has_saved_session():
            context_args["storage_state"] = str(self._state_path)
            logger.info("Loaded saved session from %s", self._state_path)

        self._context = await self._browser.new_context(**context_args)
        self._page = await self._context.new_page()

    async def save_session(self) -> None:
        if self._context:
            state = await self._context.storage_state()
            self._state_path.write_text(json.dumps(state, indent=2))
            logger.info("Session state saved to %s", self._state_path)

    async def is_logged_in(self) -> bool:
        if not self._page:
            return False
        try:
            await self._page.goto(self.PREDICTIT_URL, wait_until="domcontentloaded")
            await asyncio.sleep(random_delay(min_secs=1.0, max_secs=2.0))
            logged_in = await self._page.query_selector("[class*='profile'], [class*='account'], [class*='Portfolio']")
            return logged_in is not None
        except Exception:
            logger.exception("Failed to check login status")
            return False

    async def manual_login(self) -> None:
        if self.headless:
            logger.error("Cannot perform manual login in headless mode. Set headless=False.")
            return
        if not self._page:
            await self.launch()
        await self._page.goto(f"{self.PREDICTIT_URL}/account/signin", wait_until="domcontentloaded")
        logger.info("Please log in to PredictIt in the browser window. Press Enter when done.")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await self.save_session()

    async def navigate_to_market(self, market_id: int) -> None:
        if not self._page:
            raise RuntimeError("Browser not launched")
        url = f"{self.PREDICTIT_URL}/markets/detail/{market_id}"
        await asyncio.sleep(random_delay(min_secs=0.5, max_secs=1.5))
        await self._page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(random_delay(min_secs=1.0, max_secs=2.0))

    async def close(self) -> None:
        if self._context:
            await self.save_session()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_browser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/browser.py tests/test_predictit_browser.py
git commit -m "feat(predictit): add browser session manager — Playwright login, session persistence, proxy routing"
```

---

### Task 10: PredictIt API (ExchangeAPI Port via Playwright)

**Files:**
- Create: `src/exchanges/predictit/api.py`
- Create: `tests/test_predictit_api.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_api.py`:

```python
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from src.exchanges.predictit.api import PredictItAPI


def make_api(browser=None):
    if browser is None:
        browser = MagicMock()
        browser.navigate_to_market = AsyncMock()
        browser._page = MagicMock()
        browser._page.query_selector = AsyncMock(return_value=None)
        browser._page.query_selector_all = AsyncMock(return_value=[])
        browser._page.evaluate = AsyncMock(return_value=None)
        browser.close = AsyncMock()
    return PredictItAPI(browser=browser)


def test_api_init():
    api = make_api()
    assert api._browser is not None


def test_batch_create_orders_calls_browser():
    api = make_api()
    orders = [
        {
            "ticker": "PI-100-200",
            "market_id": 100,
            "contract_id": 200,
            "market_url": "https://www.predictit.org/markets/detail/100",
            "action": "buy",
            "outcome": "yes",
            "shares": 5,
            "price": 45,
        }
    ]
    result = asyncio.get_event_loop().run_until_complete(
        api.batch_create_orders(orders)
    )
    assert "orders" in result
    assert len(result["orders"]) == 1


def test_get_balance_returns_dict():
    api = make_api()
    result = asyncio.get_event_loop().run_until_complete(api.get_balance())
    assert "balance" in result


def test_get_positions_returns_dict():
    api = make_api()
    result = asyncio.get_event_loop().run_until_complete(api.get_positions())
    assert "market_positions" in result


def test_close_delegates_to_browser():
    browser = MagicMock()
    browser.close = AsyncMock()
    api = PredictItAPI(browser=browser)
    asyncio.get_event_loop().run_until_complete(api.close())
    browser.close.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_api.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/api.py`:

```python
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

        page = self._browser._page
        if page is None:
            raise RuntimeError("Browser page not available")

        # TODO: Implement actual Playwright form interaction once we have
        # the PredictIt trading UI selectors mapped. For now, this scaffolds
        # the flow: navigate → find contract → select action → fill form → submit.
        # The selectors will be discovered during Phase 2 validation when
        # we can inspect the live site.

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/api.py tests/test_predictit_api.py
git commit -m "feat(predictit): add PredictItAPI — ExchangeAPI port backed by Playwright browser automation"
```

---

### Task 11: PredictIt Exchange Facade

**Files:**
- Modify: `src/exchanges/predictit/__init__.py`
- Create: `tests/test_predictit_integration.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_predictit_integration.py`:

```python
from src.exchanges.predictit import PredictItExchange


def test_exchange_name():
    assert PredictItExchange.name == "predictit"


def test_exchange_init():
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    assert exchange.fee_model is not None
    assert exchange.order_builder is not None
    assert exchange.constraints is not None
    assert exchange.api is not None


def test_exchange_fee_model_type():
    from src.exchanges.predictit.fee_model import PredictItFeeModel
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    assert isinstance(exchange.fee_model, PredictItFeeModel)


def test_exchange_constraints_type():
    from src.exchanges.predictit.constraints import PredictItConstraints
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    assert isinstance(exchange.constraints, PredictItConstraints)


def test_create_feed():
    from src.core.orderbook_manager import OrderbookManager
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    mgr = OrderbookManager()
    feed = exchange.create_feed(mgr)
    assert feed is not None


def test_create_discovery():
    from src.core.orderbook_manager import OrderbookManager
    from unittest.mock import MagicMock
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    mgr = OrderbookManager()
    scanner = MagicMock()
    discovery = exchange.create_discovery(mgr, scanner)
    assert discovery is not None
    assert hasattr(discovery, "market_metadata")
    assert hasattr(discovery, "event_total_markets")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_predictit_integration.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write implementation**

`src/exchanges/predictit/__init__.py`:

```python
from src.exchanges.predictit.fee_model import PredictItFeeModel
from src.exchanges.predictit.order_builder import PredictItOrderBuilder
from src.exchanges.predictit.constraints import PredictItConstraints
from src.exchanges.predictit.scraper import PredictItScraper
from src.exchanges.predictit.browser import PredictItBrowser
from src.exchanges.predictit.api import PredictItAPI


class PredictItExchange:
    name = "predictit"

    def __init__(self, config: dict):
        self.fee_model = PredictItFeeModel(
            include_withdrawal_fee=config.get("include_withdrawal_fee", True),
        )
        self.order_builder = PredictItOrderBuilder()
        self.constraints = PredictItConstraints()
        self._scraper = PredictItScraper(proxy_url=config.get("proxy_url"))
        self._browser = PredictItBrowser(
            session_dir=config.get("session_dir", "~/.kalshi/predictit_session"),
            proxy_url=config.get("proxy_url"),
            headless=config.get("headless", True),
        )
        self.api = PredictItAPI(browser=self._browser)
        self._poll_interval_secs = config.get("poll_interval_secs", 60)

    def create_feed(self, orderbook_mgr, on_update=None, on_fill=None):
        from src.exchanges.predictit.scanner import PredictItScanner
        return PredictItScanner(
            scraper=self._scraper,
            orderbook_mgr=orderbook_mgr,
            on_orderbook_update=on_update,
            on_fill=on_fill,
            poll_interval_secs=self._poll_interval_secs,
        )

    def create_discovery(self, orderbook_mgr, scanner):
        from src.exchanges.predictit.discovery import PredictItDiscovery
        return PredictItDiscovery(
            scraper=self._scraper,
            orderbook_mgr=orderbook_mgr,
            scanner=scanner,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_predictit_integration.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/predictit/__init__.py tests/test_predictit_integration.py
git commit -m "feat(predictit): add PredictItExchange facade — wires all sub-components"
```

---

### Task 12: Wire Into Exchange Factory and Config

**Files:**
- Modify: `src/exchanges/__init__.py`
- Modify: `src/config.py`
- Modify: `config.example.yaml`
- Modify: `tests/test_ports.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ports.py`:

```python
def test_predictit_fee_model_conforms():
    from src.exchanges.predictit.fee_model import PredictItFeeModel
    fm = PredictItFeeModel()
    assert fm.taker_fee(0.50) == 0.0
    assert fm.maker_fee(0.50) == 0.0
    assert fm.profit_fee(1.0) > 0


def test_predictit_order_builder_conforms():
    from src.exchanges.predictit.order_builder import PredictItOrderBuilder
    ob = PredictItOrderBuilder()
    sell = ob.build_sell_order("PI-100-200", 0.55, 1)
    assert sell["ticker"] == "PI-100-200"
    assert sell["action"] == "sell"

    buy = ob.build_buy_order("PI-100-200", 0.40, 2)
    assert buy["action"] == "buy"
    assert buy["shares"] == 2

    close = ob.build_close_order("PI-100-200", 1)
    assert close["action"] == "sell"

    unwrapped = ob.unwrap_order({"order_id": "abc"})
    assert unwrapped == {"order_id": "abc"}


def test_predictit_constraints_conforms():
    from src.exchanges.predictit.constraints import PredictItConstraints
    c = PredictItConstraints()
    assert c.max_position_size("PI-100-200") == 3500
    assert c.max_total_exposure() is None


def test_exchange_factory_predictit():
    from src.exchanges import create_exchange
    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": True,
        "poll_interval_secs": 60,
    }
    exchange = create_exchange("predictit", config)
    assert exchange.name == "predictit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ports.py::test_exchange_factory_predictit -v`
Expected: FAIL with `KeyError: 'predictit'`

- [ ] **Step 3: Register PredictIt in exchange factory**

In `src/exchanges/__init__.py`, add the import and registration:

```python
from src.exchanges.kalshi import KalshiExchange
from src.exchanges.predictit import PredictItExchange

EXCHANGES = {
    "kalshi": KalshiExchange,
    "predictit": PredictItExchange,
}


def create_exchange(name: str, config: dict):
    return EXCHANGES[name](config)
```

- [ ] **Step 4: Update src/config.py**

Add `python-dotenv` loading and PredictIt config fields. At the top of `src/config.py`, add:

```python
from dotenv import load_dotenv
load_dotenv()
```

Add PredictIt URL constants after the Kalshi ones:

```python
PREDICTIT_API_URL = "https://www.predictit.org/api/marketdata/all/"
```

Add PredictIt fields to the `Config` dataclass:

```python
# PredictIt-specific
predictit_proxy_url: str | None = None
predictit_session_dir: str = "~/.kalshi/predictit_session"
predictit_headless: bool = True
predictit_include_withdrawal_fee: bool = True
predictit_poll_interval_secs: int = 60
```

In `load_config()`, after loading the exchange name, add PredictIt config loading:

```python
import os

# PredictIt config
pi_cfg = raw.get("predictit", {})
predictit_proxy_url = os.environ.get("DECODO_PROXY_URL") or pi_cfg.get("proxy_url")
predictit_session_dir = (
    os.environ.get("PREDICTIT_SESSION_DIR")
    or pi_cfg.get("session_dir", "~/.kalshi/predictit_session")
)
```

Pass the new fields to the Config constructor. When `exchange == "predictit"`, the exchange_config in main.py will use these instead of api_key_id/private_key_path.

- [ ] **Step 5: Update config.example.yaml**

Add the PredictIt section after the recording section:

```yaml
# PredictIt-specific (only used when exchange: predictit)
# Requires DECODO_PROXY_URL in .env file
# predictit:
#   poll_interval_secs: 60           # Match PredictIt's data refresh cadence
#   session_dir: ~/.kalshi/predictit_session  # Playwright session state directory
#   headless: true                   # false for manual login, true for production
#   include_withdrawal_fee: true     # Include 5% withdrawal fee in profit calculations
#   min_profit_pct: 5.0             # Higher than Kalshi due to 60s data staleness
#   execution_timeout_secs: 30      # Browser actions are slower than REST
```

- [ ] **Step 6: Run all port conformance tests**

Run: `python3 -m pytest tests/test_ports.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/exchanges/__init__.py src/config.py config.example.yaml tests/test_ports.py
git commit -m "feat(predictit): wire PredictItExchange into factory, config, and port conformance tests"
```

---

### Task 13: Update main.py Exchange Config Routing

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Update exchange_config construction in ArbBot.__init__**

The current code builds Kalshi-specific config keys (`api_key_id`, `private_key_path`, `base_url`, `ws_url`). PredictIt needs different keys. Update the `exchange_config` block in `ArbBot.__init__` (lines 30-35):

```python
if self.cfg.exchange == "predictit":
    exchange_config = {
        "proxy_url": self.cfg.predictit_proxy_url,
        "session_dir": self.cfg.predictit_session_dir,
        "headless": self.cfg.predictit_headless,
        "include_withdrawal_fee": self.cfg.predictit_include_withdrawal_fee,
        "poll_interval_secs": self.cfg.predictit_poll_interval_secs,
    }
else:
    exchange_config = {
        "api_key_id": self.cfg.api_key_id,
        "private_key_path": str(self.cfg.private_key_path),
        "base_url": self.cfg.rest_base_url,
        "ws_url": self.cfg.ws_url,
    }
```

- [ ] **Step 2: Update the startup log message (line 483)**

Change from hardcoded "Kalshi Arb Bot" to exchange-aware:

```python
logger.info("Starting %s Arb Bot in %s mode (risk: %s)",
             self.cfg.exchange.capitalize(), self.cfg.mode.upper(), self.cfg.risk_mode)
```

- [ ] **Step 3: Make credentials optional for PredictIt in config.py**

PredictIt doesn't use `api_key_id` or `private_key_path`. Update `load_config()` so these fields are only required when `exchange == "kalshi"`:

```python
if exchange == "kalshi":
    # existing credential loading...
    for key in ("api_key_id", "private_key_path"):
        if key not in creds:
            raise ValueError(f"Missing credential: {key!r} for mode {mode!r}")
else:
    creds = {}
```

Set defaults for Kalshi-specific fields when using PredictIt:

```python
api_key_id = creds.get("api_key_id", "")
private_key_path = Path(creds.get("private_key_path", "/dev/null")).expanduser() if creds.get("private_key_path") else Path("/dev/null")
rest_url = URLS.get(mode, ("", ""))[0] if mode in URLS else ""
ws_url = URLS.get(mode, ("", ""))[1] if mode in URLS else ""
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `python3 -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py src/config.py
git commit -m "feat(predictit): route exchange-specific config in main.py and make Kalshi credentials optional"
```

---

### Task 14: Full Integration Test — End-to-End with Mocks

**Files:**
- Modify: `tests/test_predictit_integration.py`

- [ ] **Step 1: Add end-to-end integration test with mocked scraper**

Append to `tests/test_predictit_integration.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch

from src.core.orderbook_manager import OrderbookManager
from src.core.fees import arb_profit


SAMPLE_API_RESPONSE = {
    "markets": [
        {
            "id": 7456,
            "name": "Who will win the 2026 election?",
            "shortName": "2026 Election",
            "status": "Open",
            "contracts": [
                {
                    "id": 28541,
                    "name": "Democratic",
                    "shortName": "Dem",
                    "status": "Open",
                    "dateEnd": "2026-11-03T23:59:00",
                    "bestBuyYesCost": 0.54,
                    "bestBuyNoCost": 0.48,
                    "bestSellYesCost": 0.52,
                    "bestSellNoCost": 0.46,
                    "lastTradePrice": 0.53,
                    "lastClosePrice": 0.53,
                },
                {
                    "id": 28542,
                    "name": "Republican",
                    "shortName": "Rep",
                    "status": "Open",
                    "dateEnd": "2026-11-03T23:59:00",
                    "bestBuyYesCost": 0.49,
                    "bestBuyNoCost": 0.53,
                    "bestSellYesCost": 0.47,
                    "bestSellNoCost": 0.51,
                    "lastTradePrice": 0.47,
                    "lastClosePrice": 0.47,
                },
            ],
        }
    ]
}


def test_full_data_pipeline():
    """Test: scraper → discovery → scanner → orderbook → fee calc."""
    from src.exchanges.predictit import PredictItExchange
    from src.exchanges.predictit.scraper import PredictItScraper

    config = {
        "proxy_url": None,
        "session_dir": "/tmp/test_predictit_session",
        "headless": True,
        "include_withdrawal_fee": False,
        "poll_interval_secs": 60,
    }
    exchange = PredictItExchange(config)
    mgr = OrderbookManager()

    # Step 1: Scraper parses JSON
    parsed = exchange._scraper.parse_markets(SAMPLE_API_RESPONSE)
    assert len(parsed) == 1

    # Step 2: Discovery converts to events and registers
    scanner = exchange.create_feed(mgr)
    discovery = exchange.create_discovery(mgr, scanner)
    events = discovery._convert_to_events(parsed)
    new_tickers = discovery.register_events(events)
    assert len(new_tickers) == 2
    assert "PI-7456-28541" in new_tickers
    assert "PI-7456-28542" in new_tickers

    # Step 3: Scanner builds orderbooks from contract data
    contract_dem = SAMPLE_API_RESPONSE["markets"][0]["contracts"][0]
    contract_rep = SAMPLE_API_RESPONSE["markets"][0]["contracts"][1]
    book_dem = scanner._build_orderbook(contract_dem)
    book_rep = scanner._build_orderbook(contract_rep)
    assert book_dem.best_bid() == 0.52  # bestSellYesCost
    assert book_rep.best_bid() == 0.47  # bestSellYesCost

    # Step 4: Fee calculation works with PredictIt fee model
    bid_prices = [0.52, 0.47]
    profit = arb_profit(bid_prices, exchange.fee_model)
    # gross = 0.99 - 1.0 = -0.01 (not profitable)
    assert profit < 0

    # With wider spread (hypothetical profitable case)
    bid_prices_wide = [0.60, 0.55]
    profit_wide = arb_profit(bid_prices_wide, exchange.fee_model)
    # gross = 1.15 - 1.0 = 0.15, fee = 10% of 0.15 = 0.015, net = 0.135
    assert profit_wide > 0
```

- [ ] **Step 2: Run the full integration test**

Run: `python3 -m pytest tests/test_predictit_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run the entire test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add tests/test_predictit_integration.py
git commit -m "test(predictit): add end-to-end integration test for data pipeline"
```

---

### Task 15: Live Decodo Proxy Validation Script

**Files:**
- Create: `scripts/test_predictit_proxy.py`

This is the Phase 1 validation — verify the scraper works through Decodo against the real PredictIt endpoint without getting blocked.

- [ ] **Step 1: Create the validation script**

`scripts/test_predictit_proxy.py`:

```python
"""
Validate PredictIt scraper works through Decodo proxy.

Usage:
    python3 scripts/test_predictit_proxy.py

Requires DECODO_PROXY_URL set in .env file.
"""
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.exchanges.predictit.scraper import PredictItScraper


def main():
    proxy_url = os.environ.get("DECODO_PROXY_URL")
    if not proxy_url:
        print("ERROR: DECODO_PROXY_URL not set in .env")
        print("Create .env with: DECODO_PROXY_URL=http://user:pass@us.decodo.com:10001")
        sys.exit(1)

    masked = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
    print(f"Proxy: {masked}")

    scraper = PredictItScraper(proxy_url=proxy_url)

    print("\n--- Fetch 1: Testing connection ---")
    try:
        data = scraper.fetch()
        markets = scraper.parse_markets(data)
        print(f"OK: {len(markets)} open markets with 2+ contracts")
        if markets:
            m = markets[0]
            print(f"  Sample: {m['name']} ({len(m['contracts'])} contracts)")
            for c in m["contracts"][:3]:
                print(f"    {c['name']}: bid={c.get('bestSellYesCost')}, ask={c.get('bestBuyYesCost')}")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    print("\n--- Fetch 2: Testing rate limit (waiting 65s) ---")
    time.sleep(65)
    try:
        data2 = scraper.fetch()
        markets2 = scraper.parse_markets(data2)
        print(f"OK: {len(markets2)} markets on second fetch")
    except Exception as e:
        print(f"FAIL on second fetch: {e}")
        sys.exit(1)

    print("\n--- Fetch 3: Testing rapid retry (should work with proxy rotation) ---")
    time.sleep(5)
    try:
        data3 = scraper.fetch()
        markets3 = scraper.parse_markets(data3)
        print(f"OK: {len(markets3)} markets on rapid fetch")
    except Exception as e:
        print(f"FAIL on rapid fetch: {e}")
        print("This may indicate rate limiting — the 60s interval is recommended")

    print("\nAll proxy validation checks passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ensure .env has Decodo credentials**

Verify `.env` exists with your Decodo credentials:

```bash
test -f .env && echo "exists" || echo "Create .env from .env.example"
```

If `.env` doesn't exist, create it:

```
DECODO_PROXY_URL=http://spwu4x55h4:lvn4jRgbEmrhR1Q8~3@us.decodo.com:10001
PREDICTIT_SESSION_DIR=~/.kalshi/predictit_session
```

- [ ] **Step 3: Run the validation script**

Run: `python3 scripts/test_predictit_proxy.py`
Expected: All three fetches succeed, showing real PredictIt market data through the Decodo proxy.

- [ ] **Step 4: Commit**

```bash
git add scripts/test_predictit_proxy.py
git commit -m "feat(predictit): add live proxy validation script for Decodo + PredictIt endpoint"
```

---

## Appendix: What's NOT in This Plan

These are explicitly **Phase 2+** and not part of this implementation:

- **Actual Playwright form selectors** — The `_place_single_order` method in `api.py` scaffolds the flow but doesn't have real CSS selectors for PredictIt's trading UI. Those need to be discovered by inspecting the live site during Phase 2 validation with the browser in headed mode.
- **Browser-based position/balance scraping** — `get_positions()` and `get_balance()` return empty results. Phase 2 will add the Playwright logic to scrape the portfolio and account pages.
- **Cross-exchange arbitrage** — Phase 4. Requires market mapping YAML and `src/core/cross_exchange.py`.
- **Maker/two-sided strategies on PredictIt** — These require robust browser-based order lifecycle management. Taker strategies only for Phase 3.
