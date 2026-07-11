#!/usr/bin/env python
import json
import logging
from src.cookies.refresh import refresh_cookies
from src.cookies.manager import CookieManager

logging.basicConfig(level=logging.INFO)

def main():
    # Refresh and save automatically
    session = refresh_cookies()
    print("Fresh session data:")
    print(json.dumps(session, indent=2, default=str))

    # Optionally load and show from manager
    mgr = CookieManager()
    loaded = mgr.load()
    print("\nLoaded from manager:")
    print(json.dumps(loaded, indent=2, default=str))

if __name__ == "__main__":
    main()