from collections import OrderedDict
from typing import Dict

def build_headers(extra: Dict = None, auth_token: str = None, pow_response: str = None) -> Dict:
    headers = OrderedDict()
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Accept-Encoding"] = "gzip, deflate, br"
    headers["Accept-Language"] = "en-US,en;q=0.9"
    headers["Cache-Control"] = "no-cache"
    headers["Connection"] = "keep-alive"
    headers["Content-Type"] = "application/json"
    headers["Origin"] = "https://chat.deepseek.com"
    headers["Referer"] = "https://chat.deepseek.com/a/chat/s/..."  # dynamic
    headers["Sec-Ch-Ua"] = '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    headers["Sec-Ch-Ua-Mobile"] = "?0"
    headers["Sec-Ch-Ua-Platform"] = '"Windows"'
    headers["Sec-Fetch-Dest"] = "empty"
    headers["Sec-Fetch-Mode"] = "cors"
    headers["Sec-Fetch-Site"] = "same-origin"
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ..."

    # 🔥 New required headers
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if pow_response:
        headers["x-ds-pow-response"] = pow_response
    headers["x-client-bundle-id"] = "com.deepseek.chat"
    headers["x-client-locale"] = "en_US"
    headers["x-client-platform"] = "web"
    headers["x-client-timezone-offset"] = "19800"      # India timezone offset
    headers["x-client-version"] = "2.2.0"
    # You may also need x-hif-leim – capture it similarly.

    if extra:
        for k, v in extra.items():
            headers[k] = v
    return dict(headers)
    """
    Build HTTP headers in the exact order used by modern Chrome.
    """
    headers = OrderedDict()
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Accept-Encoding"] = "gzip, deflate, br"
    headers["Accept-Language"] = "en-US,en;q=0.9"
    headers["Cache-Control"] = "no-cache"
    headers["Connection"] = "keep-alive"
    headers["Content-Type"] = "application/json"
    headers["Origin"] = "https://chat.deepseek.com"
    headers["Referer"] = "https://chat.deepseek.com/"
    headers["Sec-Ch-Ua"] = '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    headers["Sec-Ch-Ua-Mobile"] = "?0"
    headers["Sec-Ch-Ua-Platform"] = '"Windows"'
    headers["Sec-Fetch-Dest"] = "empty"
    headers["Sec-Fetch-Mode"] = "cors"
    headers["Sec-Fetch-Site"] = "same-origin"
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    if extra:
        for k, v in extra.items():
            headers[k] = v

    return dict(headers)