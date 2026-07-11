import logging
from curl_cffi import requests as curl_requests
from config.settings import settings

logger = logging.getLogger(__name__)

def create_tls_session(impersonate: str = "chrome99") -> curl_requests.Session:
    """
    Create a curl_cffi session that impersonates a real browser's TLS stack (JA3/JA4).
    """
    session = curl_requests.Session(impersonate=impersonate)
    # Optionally set a default user‑agent
    session.headers.update({"User-Agent": settings.DEFAULT_USER_AGENT})
    logger.info("TLS session created with impersonation: %s", impersonate)
    return session