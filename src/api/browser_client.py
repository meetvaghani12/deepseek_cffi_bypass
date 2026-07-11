import uuid
import json
import logging
import time
import threading
from typing import Optional
from playwright.sync_api import Page, Browser, Playwright
from config.settings import settings

logger = logging.getLogger(__name__)


class BrowserClient:
    """Makes API calls through a live browser, letting the browser handle PoW."""

    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._chat_session_id: Optional[str] = None
        self._parent_message_id: Optional[str] = None

    def start(self):
        from src.browser.launcher import launch_browser
        from src.browser.context import create_context
        from src.anti_detection.fingerprint import spoof_fingerprints
        from src.browser.solver import _automated_login

        self._pw, self._browser = launch_browser(headless=False)
        context = create_context(self._browser)
        self._page = context.new_page()
        spoof_fingerprints(self._page)

        self._page.goto("https://chat.deepseek.com")
        self._page.wait_for_selector("#root", timeout=60000)
        time.sleep(3)

        if not _automated_login(self._page):
            raise Exception("Login failed")
        time.sleep(3)

        self._chat_session_id = str(uuid.uuid4())
        logger.info("BrowserClient started.")

    def stop(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def chat(self, message: str) -> str:
        if not self._page:
            raise Exception("BrowserClient not started. Call start() first.")

        captured = {"text": "", "done": False}

        def on_response(response):
            url = response.url
            if "chat/completion" in url or "chat/regenerate" in url:
                try:
                    body = response.text()
                    # SSE format: each line starts with "data: "
                    full_text = ""
                    for line in body.split("\n"):
                        line = line.strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                captured["done"] = True
                                continue
                            try:
                                data = json.loads(data_str)
                                # Extract content from SSE delta
                                choices = data.get("choices", [])
                                for choice in choices:
                                    delta = choice.get("delta", {})
                                    if "content" in delta:
                                        full_text += delta["content"]
                                # Also try non-streaming format
                                if "content" in data:
                                    full_text += data["content"]
                                # Store chat_session_id and parent_message_id
                                if "id" in data:
                                    captured["msg_id"] = data["id"]
                            except json.JSONDecodeError:
                                pass
                    if full_text:
                        captured["text"] += full_text
                except Exception as e:
                    logger.debug("Response parse error: %s", e)

        self._page.on("response", on_response)

        # Find and type the message
        sent = False
        for sel in ["textarea", "textarea[placeholder]", "[contenteditable='true']"]:
            try:
                el = self._page.locator(sel).first
                el.wait_for(timeout=5000)
                el.click()
                time.sleep(0.5)
                el.fill(message)
                time.sleep(0.5)
                el.press("Enter")
                sent = True
                logger.info("Sent via %s", sel)
                break
            except:
                continue

        if not sent:
            self._page.remove_listener("response", on_response)
            raise Exception("Could not find chat input")

        # Wait for response
        deadline = time.time() + 60
        while time.time() < deadline:
            if captured["text"] and captured["done"]:
                break
            time.sleep(1)

        self._page.remove_listener("response", on_response)

        if captured["text"]:
            return captured["text"].strip()

        # Fallback: scrape DOM
        return self._scrape_last_response()

    def _scrape_last_response(self) -> str:
        """Fallback: scrape the last assistant message from the DOM."""
        try:
            text = self._page.evaluate('''
            () => {
                // Look for markdown content blocks (assistant messages)
                const blocks = document.querySelectorAll('[class*="ds-markdown"], [class*="markdown"], [class*="message-content"]');
                if (blocks.length > 0) {
                    return blocks[blocks.length - 1].innerText;
                }
                return null;
            }
            ''')
            if text:
                return text.strip()
        except:
            pass
        return "(no response)"
