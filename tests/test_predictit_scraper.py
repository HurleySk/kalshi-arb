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
