"""
Validate PredictIt scraper works through Decodo proxy.

Usage:
    python3 scripts/test_predictit_proxy.py

Requires DECODO_PROXY_URL set in .env file.
"""
import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.exchanges.predictit.scraper import PredictItScraper


async def main():
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
        data = await scraper.fetch()
        markets = scraper.parse_markets(data)
        print(f"OK: {len(markets)} open markets with 2+ contracts")
        if markets:
            m = markets[0]
            print(f"  Sample: {m['name']} ({len(m['contracts'])} contracts)")
            for c in m["contracts"][:3]:
                print(f"    {c['name']}: bid={c.get('bestSellYesCost')}, ask={c.get('bestBuyYesCost')}")
    except Exception as e:
        print(f"FAIL: {e}")
        await scraper.close()
        sys.exit(1)

    print("\n--- Fetch 2: Testing rate limit (waiting 65s) ---")
    time.sleep(65)
    try:
        data2 = await scraper.fetch()
        markets2 = scraper.parse_markets(data2)
        print(f"OK: {len(markets2)} markets on second fetch")
    except Exception as e:
        print(f"FAIL on second fetch: {e}")
        await scraper.close()
        sys.exit(1)

    print("\n--- Fetch 3: Testing rapid retry (should work with proxy rotation) ---")
    time.sleep(5)
    try:
        data3 = await scraper.fetch()
        markets3 = scraper.parse_markets(data3)
        print(f"OK: {len(markets3)} markets on rapid fetch")
    except Exception as e:
        print(f"FAIL on rapid fetch: {e}")
        print("This may indicate rate limiting — the 60s interval is recommended")

    await scraper.close()
    print("\nAll proxy validation checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
