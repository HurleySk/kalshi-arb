import asyncio
import json
import logging
import tempfile
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
        self.session_dir = Path(session_dir).expanduser()
        self.proxy_url = proxy_url
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self):
        return self._page

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
            tmp = tempfile.NamedTemporaryFile(
                mode="w", dir=self.session_dir, suffix=".tmp", delete=False,
            )
            try:
                tmp.write(json.dumps(state, indent=2))
                tmp.close()
                Path(tmp.name).replace(self._state_path)
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise
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
        await asyncio.get_running_loop().run_in_executor(None, input)
        await self.save_session()

    async def navigate_to_market(self, market_id: int) -> None:
        if not self._page:
            raise RuntimeError("Browser not launched")
        url = f"{self.PREDICTIT_URL}/markets/detail/{market_id}"
        await asyncio.sleep(random_delay(min_secs=0.5, max_secs=1.5))
        await self._page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(random_delay(min_secs=1.0, max_secs=2.0))

    async def close(self) -> None:
        try:
            if self._context:
                await self.save_session()
        except Exception:
            logger.exception("Failed to save session during close")
        finally:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
