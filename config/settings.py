import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Proxy
    PROXY_HOST = os.getenv("PROXY_HOST")
    PROXY_PORT = int(os.getenv("PROXY_PORT", 0)) if os.getenv("PROXY_PORT") else None
    PROXY_USERNAME = os.getenv("PROXY_USERNAME")
    PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
    PROXY_TYPE = os.getenv("PROXY_TYPE", "http")

    # Timeouts
    BROWSER_LAUNCH_TIMEOUT = int(os.getenv("BROWSER_LAUNCH_TIMEOUT", 30))
    CHALLENGE_TIMEOUT = int(os.getenv("CHALLENGE_TIMEOUT", 120))
    LOGIN_TIMEOUT = int(os.getenv("LOGIN_TIMEOUT", 90))
    API_REQUEST_TIMEOUT = int(os.getenv("API_REQUEST_TIMEOUT", 30))
    COOKIE_REFRESH_MARGIN = int(os.getenv("COOKIE_REFRESH_MARGIN", 120))

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Default user-agent
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # DeepSeek login credentials
    DEEPSEEK_EMAIL = os.getenv("DEEPSEEK_EMAIL")
    DEEPSEEK_PASSWORD = os.getenv("DEEPSEEK_PASSWORD")

    # Placeholder endpoints – replace with your own test server
    API_BASE_URL = "https://chat.deepseek.com"
    CHAT_ENDPOINT = "https://chat.deepseek.com/api/v0/chat/completion"

settings = Settings()