"""
Build the HTTP headers for chat.deepseek.com API requests, ordered like real Chrome.

Auth is NOT handled here — the caller (PersistentSession) owns the Authorization and
x-ds-pow-response headers because it captures them per request from the live browser.
This module only supplies the stable browser-shaped headers.
"""
import os
from collections import OrderedDict
from typing import Dict, Optional

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Client timezone offset in minutes, matching the browser. Defaults to UTC; override via
# env to match the login profile's locale (e.g. 330 for IST, -300 for US Eastern).
_TZ_OFFSET = os.getenv("DEEPSEEK_TZ_OFFSET_MINUTES", "0")


def build_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = OrderedDict()
    h["Accept"] = "*/*"
    h["Accept-Encoding"] = "gzip, deflate, br"
    h["Accept-Language"] = "en-US,en;q=0.9"
    h["Content-Type"] = "application/json"
    h["Origin"] = "https://chat.deepseek.com"
    h["Referer"] = "https://chat.deepseek.com/"
    h["Sec-Ch-Ua"] = '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    h["Sec-Ch-Ua-Mobile"] = "?0"
    h["Sec-Ch-Ua-Platform"] = '"Windows"'
    h["Sec-Fetch-Dest"] = "empty"
    h["Sec-Fetch-Mode"] = "cors"
    h["Sec-Fetch-Site"] = "same-origin"
    h["User-Agent"] = _UA
    h["x-client-platform"] = "web"
    h["x-client-version"] = "2.2.0"
    h["x-client-locale"] = "en_US"
    h["x-client-timezone-offset"] = _TZ_OFFSET
    if extra:
        h.update(extra)
    return dict(h)
