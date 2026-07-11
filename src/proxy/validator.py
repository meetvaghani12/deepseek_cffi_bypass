import logging
import requests
from typing import Dict, Optional

logger = logging.getLogger(__name__)

def validate_proxy(proxy: Dict) -> bool:
    """
    Check if a proxy is working and returns a reasonable response.
    """
    try:
        proxy_url = f"{proxy.get('type', 'http')}://{proxy['host']}:{proxy['port']}"
        if "username" in proxy and "password" in proxy:
            proxy_url = f"{proxy['type']}://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://chat.deepseek.com", proxies=proxies, timeout=10)
        if resp.status_code == 200:
            logger.info("Proxy %s is valid", proxy.get("host"))
            return True
        else:
            logger.warning("Proxy %s returned status %d", proxy.get("host"), resp.status_code)
            return False
    except Exception as e:
        logger.warning("Proxy %s failed: %s", proxy.get("host"), e)
        return False