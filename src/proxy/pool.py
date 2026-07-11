import random
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

class ProxyPool:
    """
    A simple pool of residential proxies (loaded from data/proxies.json or environment).
    """
    def __init__(self, proxies: Optional[List[Dict]] = None):
        self.proxies = proxies or []
        if not self.proxies:
            # Attempt to load from data/proxies.json
            try:
                import json
                with open("data/proxies.json", "r") as f:
                    self.proxies = json.load(f)
                logger.info("Loaded %d proxies from data/proxies.json", len(self.proxies))
            except FileNotFoundError:
                logger.warning("No proxies found; using direct connection.")
                self.proxies = []

    def get_proxy(self) -> Optional[Dict]:
        """Return a random proxy from the pool."""
        if self.proxies:
            proxy = random.choice(self.proxies)
            logger.debug("Selected proxy: %s", proxy.get("host"))
            return proxy
        return None

    def rotate(self) -> Optional[Dict]:
        """Rotate to a different proxy (same as get_proxy for simplicity)."""
        return self.get_proxy()