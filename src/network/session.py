import logging
import threading
import queue
import time
import json
import base64
from collections import deque
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

    def refresh_auth_token(self) -> Optional[str]:
        """
        Re-read the userToken from the live browser's localStorage (it rotates over long
        sessions). Runs on the browser thread. Returns the new Bearer token, or None if the
        page has no token (logged out) — caller may then re-login.
        """
        try:
            tok = self._page.evaluate(
                "JSON.parse(localStorage.getItem('userToken'))?.value"
            )
        except Exception as e:
            logger.warning("refresh_auth_token: page.evaluate failed: %s", e)
            return None
        if tok:
            self._user_token = tok
            self._auth_token = f"Bearer {tok}"
            logger.info("auth token refreshed from browser localStorage")
            return self._auth_token
        return None

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


def _pow_expire_at_ms(pow_token: str) -> Optional[int]:
    """Read `expire_at` (epoch ms) out of a base64-encoded PoW solution, if present."""
    try:
        raw = base64.b64decode(pow_token)
        obj = json.loads(raw)
        # The solution echoes the challenge fields, incl. expire_at (camel or snake).
        return obj.get("expire_at") or obj.get("expireAt")
    except Exception:
        return None


class PoWPool:
    """
    A small pool of pre-solved, unexpired PoW tokens.

    DeepSeek's PoW is bound to the target path (/api/v0/chat/completion) and carries an
    expiry (~5 min). Solving takes 2-4s and blocks the request. A background refiller keeps
    a few valid tokens ready so most chat requests grab one instantly. `solve_fn` performs
    one solve (returns a base64 token); it MUST be called under the caller's browser lock —
    the pool takes `lock` and holds it only for the solve itself.

    This is a BEST-EFFORT optimization: a pooled token can occasionally be rejected
    (INVALID_POW_RESPONSE) if it went stale. The caller must handle that by re-solving
    fresh (see _chat_request's rejection retry), so a bad pool token costs one re-solve,
    never a failed request. The generous safety margin minimizes how often that happens.
    """

    def __init__(self, solve_fn, lock: threading.Lock, target_size: int = 3,
                 safety_margin_s: float = 90.0):
        self._solve_fn = solve_fn
        self._lock = lock                     # the shared browser lock (_chat_lock)
        self._target = target_size
        self._margin = safety_margin_s
        self._pool = deque()                  # (token, expire_at_ms)
        self._pool_lock = threading.Lock()
        self._refill_event = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._refiller, daemon=True)
        self._thread.start()

    def _valid(self, entry) -> bool:
        _tok, exp = entry
        if exp is None:
            return True  # unknown expiry — assume usable (real solves carry it)
        return (exp / 1000.0) - time.time() > self._margin

    def take(self) -> Optional[str]:
        """Return a ready valid token instantly, or None if the pool is empty."""
        with self._pool_lock:
            while self._pool:
                entry = self._pool.popleft()
                if self._valid(entry):
                    # Nudge the refiller to top back up.
                    self._refill_event.set()
                    return entry[0]
            # Pool empty — signal a refill and let the caller solve inline this time.
            self._refill_event.set()
            return None

    def put(self, token: str) -> None:
        if not token:
            return
        with self._pool_lock:
            self._pool.append((token, _pow_expire_at_ms(token)))

    def _count_valid(self) -> int:
        with self._pool_lock:
            # Drop expired while counting.
            self._pool = deque(e for e in self._pool if self._valid(e))
            return len(self._pool)

    def _refiller(self):
        first = True
        while not self._stop:
            # Fill immediately on startup; thereafter wait for a nudge or idle interval.
            if not first:
                self._refill_event.wait(timeout=20.0)
                self._refill_event.clear()
            first = False
            try:
                while not self._stop and self._count_valid() < self._target:
                    # Solve one token under the browser lock (competes politely with live
                    # requests — they hold the lock only briefly per solve).
                    with self._lock:
                        token = self._solve_fn()
                    if token:
                        self.put(token)
                    else:
                        break
            except Exception as e:
                logger.debug("PoW pool refill error: %s", e)
                time.sleep(2.0)

    def stop(self):
        self._stop = True
        self._refill_event.set()


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
        # Serializes a whole chat request's browser work (PoW solve / CDP intercept).
        # The CDP-intercept fallback NAVIGATES the shared page; if two chat requests
        # interleave, one navigation destroys the other's JS execution context
        # ("Execution context was destroyed, most likely because of a navigation").
        # Concurrent clients (Claude Code, opencode) fire parallel requests, so the
        # browser-PoW phase must be atomic per request.
        self._chat_lock = threading.Lock()
        self._worker = threading.Thread(target=self._browser_worker, daemon=True)
        self._worker.start()
        self._worker_ready.wait()
        self._sync_cookies()
        # Pre-solve PoW tokens in the background so most chat requests don't wait 2-4s.
        # The pool's solve runs the Worker solve on the browser thread, under _chat_lock.
        self._pow_pool = PoWPool(
            solve_fn=lambda: self._run(self.browser_session.solve_pow),
            lock=self._chat_lock,
        )

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

        # Fast path: grab a pre-solved token from the pool instantly (no lock, no 2-4s solve).
        pow_resp = self._pow_pool.take()
        if pow_resp:
            logger.info("PoW from pre-solved pool")

        # Serialize the browser-PoW phase: the CDP-intercept fallback navigates the shared
        # page, so two concurrent requests' browser work must not interleave.
        if not pow_resp:
            with self._chat_lock:
                # Try Worker-based PoW first (fast, no UI interaction)
                try:
                    pow_resp = self._run(self.browser_session.solve_pow)
                    logger.info("PoW solved via Worker (pool empty)")
                except Exception as e:
                    logger.warning("Worker PoW failed (%s), falling back to CDP intercept", e)

            # Fallback: CDP intercept (slower, types in browser) — retry up to 2 times
            if not pow_resp:
                for attempt in range(2):
                    try:
                        captured = self._run(self.browser_session.intercept_pow)
                        auth = captured.get("auth", auth)
                        pow_resp = captured.get("pow")
                        if captured.get("session_id") and "json" in kwargs:
                            kwargs["json"]["chat_session_id"] = captured["session_id"]
                        logger.info("PoW captured via CDP intercept")
                        break
                    except Exception as e:
                        logger.warning("CDP intercept attempt %d failed: %s", attempt + 1, e)
                        if attempt == 1:
                            raise

        headers = self._get_headers(auth_token=auth, pow_response=pow_resp)
        headers.update(kwargs.pop("headers", {}))

        # Large prompts (Claude Code sends 100KB+ system prompts) take DeepSeek well over
        # the curl_cffi default 30s. Give the chat request generous headroom.
        kwargs.setdefault("timeout", 180)
        response = self.tls_session.request(method, url, headers=headers, **kwargs)

        # Recover from an expired userToken (401/403): refresh it from the live browser
        # localStorage and retry once, instead of surfacing a mystifying auth error.
        if response.status_code in (401, 403):
            logger.warning("auth %d — refreshing token from browser and retrying", response.status_code)
            new_auth = self._run(self.browser_session.refresh_auth_token)
            if new_auth and new_auth != auth:
                auth = new_auth
                self._sync_cookies()
                headers = self._get_headers(auth_token=auth, pow_response=pow_resp)
                response = self.tls_session.request(method, url, headers=headers, **kwargs)

        # Retry on INVALID_POW_RESPONSE. A pooled token can occasionally be stale/rejected;
        # recover with a FRESH Worker solve first (fast, reliable), and only fall back to
        # the slow page-navigating CDP intercept if the Worker solve itself fails.
        try:
            resp_data = response.json()
        except Exception:
            resp_data = None
        if isinstance(resp_data, dict) and resp_data.get("code") in (40301, 40003):
            logger.warning("PoW rejected (%s); re-solving fresh", resp_data.get("msg"))
            fresh_pow = None
            with self._chat_lock:
                try:
                    fresh_pow = self._run(self.browser_session.solve_pow)
                    logger.info("PoW re-solved via Worker after rejection")
                except Exception as e:
                    logger.warning("Worker re-solve failed (%s); trying CDP intercept", e)
                    try:
                        captured = self._run(self.browser_session.intercept_pow)
                        auth = captured.get("auth", auth)
                        fresh_pow = captured.get("pow")
                        if "json" in kwargs and captured.get("session_id"):
                            kwargs["json"]["chat_session_id"] = captured["session_id"]
                    except Exception as e2:
                        logger.error("PoW recovery failed entirely: %s", e2)
            if fresh_pow:
                self._sync_cookies()
                headers = self._get_headers(auth_token=auth, pow_response=fresh_pow)
                response = self.tls_session.request(method, url, headers=headers, **kwargs)

        return response

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        try:
            self._pow_pool.stop()
        except Exception:
            pass
        self._work_q.put((None, (), {}))
        self.browser_session.stop()
