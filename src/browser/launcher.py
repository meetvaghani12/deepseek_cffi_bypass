import logging
import os
from playwright.sync_api import sync_playwright, BrowserContext, Playwright
from config.settings import settings

logger = logging.getLogger(__name__)

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "browser_profile")


def launch_browser(headless: bool = False, proxy: dict = None) -> tuple[Playwright, BrowserContext]:
    """
    Launch a Chromium browser with a persistent profile.
    The profile preserves cookies, localStorage, and CAPTCHA state across runs.
    Returns (playwright, context) — the context can be used directly to create pages.
    """
    playwright = sync_playwright().start()

    os.makedirs(PROFILE_DIR, exist_ok=True)

    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
    ]

    ctx_options = {
        "user_data_dir": PROFILE_DIR,
        "headless": headless,
        "args": args,
        "timeout": settings.BROWSER_LAUNCH_TIMEOUT * 1000,
        "viewport": {"width": 1280, "height": 800},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    if proxy:
        ctx_options["proxy"] = proxy

    context = playwright.chromium.launch_persistent_context(**ctx_options)

    logger.info("Browser launched with persistent profile (headful=%s)", headless)
    return playwright, context