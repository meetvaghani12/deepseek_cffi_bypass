import logging
import threading
import queue
import time
from typing import Optional, Dict
from curl_cffi.requests import Session as TLSSession

logger = logging.getLogger(__name__)


class BrowserSession:
    """Manages a persistent browser session for auth token and PoW solving."""

    def __init__(self):
        self._pw = None
        self._context = None
        self._page = None
        self._user_token = None
        self._auth_token = None

    def start(self):
        from src.browser.launcher import launch_browser
        from src.anti_detection.fingerprint import spoof_fingerprints

        self._pw, self._context = launch_browser(headless=False)
        self._page = self._context.new_page()
        spoof_fingerprints(self._page)

        self._page.goto('https://chat.deepseek.com', timeout=60000)
        self._wait_for_page_ready()
        time.sleep(3)

        self._ensure_logged_in()
        self._user_token = self._page.evaluate(
            "JSON.parse(localStorage.getItem('userToken'))?.value"
        )
        if not self._user_token:
            raise Exception("Login failed — no userToken in localStorage")

        self._auth_token = f"Bearer {self._user_token}"
        logger.info("Browser session started (auth token captured)")

    def _wait_for_page_ready(self, timeout_sec=180):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                body = self._page.evaluate("document.body?.innerText?.substring(0, 200) || ''")
            except Exception:
                time.sleep(2)
                continue
            if "new chat" in body.lower():
                logger.info("App loaded")
                return
            if "log in" in body.lower() or "sign in" in body.lower():
                return
            if "verification" in body.lower() or "human" in body.lower():
                logger.warning("CAPTCHA — solve it in the browser window")
            time.sleep(2)

    def _ensure_logged_in(self):
        from src.browser.solver import _automated_login
        token = self._page.evaluate("JSON.parse(localStorage.getItem('userToken'))?.value")
        if token:
            return
        body = self._page.evaluate("document.body?.innerText?.substring(0, 300) || ''")
        if 'log in' in body.lower():
            _automated_login(self._page)
            time.sleep(5)

    def stop(self):
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def get_auth_token(self) -> str:
        return self._auth_token

    def create_session(self) -> str:
        token = self._user_token
        session_id = self._page.evaluate('''
        async (token) => {
            const r = await fetch('/api/v0/chat_session/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': token },
                body: JSON.stringify({})
            });
            const d = await r.json();
            return d.data?.biz_data?.id || null;
        }
        ''', token)
        return session_id

    def solve_pow(self) -> str:
        from src.network.pow import get_pow_via_browser
        return get_pow_via_browser(self._page, self.get_auth_token())

    def intercept_pow(self) -> dict:
        from src.network.pow import get_pow_via_cdp_intercept
        return get_pow_via_cdp_intercept(self._page, self._context)

    def get_page(self):
        return self._page

    def get_context(self):
        return self._context


class PersistentSession:
    """Thread-safe session: all Playwright work runs on a single dedicated thread."""

    def __init__(self, impersonate: str = "chrome99", proxy: Optional[Dict] = None):
        from src.network.tls import create_tls_session
        self.tls_session = create_tls_session(impersonate)
        self.impersonate = impersonate
        self.proxy = proxy
        self.browser_session = BrowserSession()
        self._worker_ready = threading.Event()
        self._work_q = queue.Queue()
        self._result_q = queue.Queue()
        self._worker = threading.Thread(target=self._browser_worker, daemon=True)
        self._worker.start()
        self._worker_ready.wait()
        self._sync_cookies()

    def _browser_worker(self):
        """Single thread that owns all Playwright calls."""
        self.browser_session.start()
        self._worker_ready.set()
        while True:
            fn, args, kwargs = self._work_q.get()
            if fn is None:
                break
            try:
                result = fn(*args, **kwargs)
                self._result_q.put(("ok", result))
            except Exception as e:
                self._result_q.put(("err", e))

    def _run(self, fn, *args, **kwargs):
        self._work_q.put((fn, args, kwargs))
        status, payload = self._result_q.get()
        if status == "err":
            raise payload
        return payload

    def _sync_cookies(self):
        def _do():
            ctx = self.browser_session.get_context()
            if ctx:
                for ck in ctx.cookies():
                    self.tls_session.cookies.set(ck["name"], ck["value"], domain=ck["domain"])
        self._run(_do)

    def create_session(self) -> str:
        return self._run(self.browser_session.create_session)

    def _get_headers(self, auth_token: str = None, pow_response: str = None) -> Dict:
        from src.anti_detection.headers import build_headers
        headers = build_headers()
        if auth_token:
            headers["Authorization"] = auth_token
        if pow_response:
            headers["x-ds-pow-response"] = pow_response
        return headers

    def request(self, method: str, url: str, **kwargs):
        if "/api/v0/chat/completion" in url:
            return self._chat_request(method, url, **kwargs)
        return self.tls_session.request(method, url, **kwargs)

    def _chat_request(self, method: str, url: str, **kwargs):
        """Make a chat request with auth + fresh PoW (browser calls run on browser thread)."""
        auth = self.browser_session.get_auth_token()
        pow_resp = None

        # Try Worker-based PoW first (fast, no UI interaction)
        try:
            pow_resp = self._run(self.browser_session.solve_pow)
            logger.info("PoW solved via Worker")
        except Exception as e:
            logger.warning("Worker PoW failed (%s), falling back to CDP intercept", e)

        # Fallback: CDP intercept (slower, types in browser)
        if not pow_resp:
            try:
                captured = self._run(self.browser_session.intercept_pow)
                auth = captured.get("auth", auth)
                pow_resp = captured.get("pow")
                if captured.get("session_id") and "json" in kwargs:
                    kwargs["json"]["chat_session_id"] = captured["session_id"]
                logger.info("PoW captured via CDP intercept")
            except Exception as e:
                logger.error("CDP intercept also failed: %s", e)
                raise

        headers = self._get_headers(auth_token=auth, pow_response=pow_resp)
        headers.update(kwargs.pop("headers", {}))

        response = self.tls_session.request(method, url, headers=headers, **kwargs)

        # Retry on INVALID_POW_RESPONSE
        try:
            resp_data = response.json()
            if resp_data.get("code") in (40301, 40003):
                logger.warning("PoW rejected (%s); retrying...", resp_data.get("msg"))
                self._sync_cookies()
                captured = self._run(self.browser_session.intercept_pow)
                headers = self._get_headers(
                    auth_token=captured.get("auth", auth),
                    pow_response=captured.get("pow"),
                )
                if "json" in kwargs and captured.get("session_id"):
                    kwargs["json"]["chat_session_id"] = captured["session_id"]
                response = self.tls_session.request(method, url, headers=headers, **kwargs)
        except Exception:
            pass

        return response

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        self._work_q.put((None, (), {}))
        self.browser_session.stop()
