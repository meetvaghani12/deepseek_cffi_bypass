import random
import time
import logging
from playwright.sync_api import Page

logger = logging.getLogger(__name__)

def simulate_behavior(page: Page):
    """
    Simulate human‑like mouse movements and scrolling.
    """
    # Random scroll
    page.mouse.wheel(delta_x=0, delta_y=random.randint(100, 500))
    time.sleep(random.uniform(0.1, 0.3))

    # Random mouse movement
    x = random.randint(100, 800)
    y = random.randint(100, 600)
    page.mouse.move(x, y)
    time.sleep(random.uniform(0.1, 0.2))

    # Click somewhere harmless
    page.mouse.click(x, y)
    logger.debug("Simulated random behavior on page.")