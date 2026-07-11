import logging
from typing import Dict, Optional
from src.browser.launcher import launch_browser
from src.browser.solver import solve_challenge
from src.anti_detection.fingerprint import spoof_fingerprints
from src.anti_detection.behavior import simulate_behavior
from src.cookies.manager import CookieManager

logger = logging.getLogger(__name__)

def refresh_cookies(proxy: Optional[Dict] = None) -> Dict:
    """
    Spin up a headful browser with a persistent profile, solve the challenge,
    capture tokens, and return the full session dictionary.
    """
    playwright = None
    context = None
    try:
        playwright, context = launch_browser(headless=False, proxy=proxy)
        page = context.new_page()

        # Apply anti-detection scripts
        spoof_fingerprints(page)
        simulate_behavior(page)

        # Solve the challenge and capture session data
        session_data = solve_challenge(page, context)

        # Also simulate more behavior after solving
        simulate_behavior(page)

        # Save the session using the manager
        manager = CookieManager()
        manager.save(session_data)

        return session_data
    except Exception as e:
        logger.error("Failed to refresh cookies: %s", e)
        raise
    finally:
        if context:
            context.close()
        if playwright:
            playwright.stop()