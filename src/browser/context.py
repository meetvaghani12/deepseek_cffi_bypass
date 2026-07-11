import logging
from playwright.sync_api import BrowserContext, Browser
from config.settings import settings
from config.user_agents import USER_AGENTS
import random

logger = logging.getLogger(__name__)

def create_context(browser: Browser, user_agent: str = None, proxy: dict = None) -> BrowserContext:
    """
    Create a realistic browser context with viewport, locale, timezone, and extra headers.
    """
    if user_agent is None:
        user_agent = random.choice(USER_AGENTS)

    context_options = {
        "user_agent": user_agent,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "permissions": ["geolocation"],
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        },
    }
    if proxy:
        context_options["proxy"] = proxy

    context = browser.new_context(**context_options)
    logger.info("Browser context created with UA: %s", user_agent[:50])
    return context