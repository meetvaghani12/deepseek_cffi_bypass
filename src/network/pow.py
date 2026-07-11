import json
import base64
import logging
import time

logger = logging.getLogger(__name__)


def capture_auth_token(page, context) -> str:
    """Capture the real Bearer auth token from the browser via CDP."""
    client = context.new_cdp_session(page)
    client.send("Fetch.enable", {
        "patterns": [{"urlPattern": "*deepseek.com/api/*", "requestStage": "Request"}]
    })

    auth_token = [None]
    done = [False]

    def on_fetch(params):
        try:
            req = params["request"]
            req_id = params["requestId"]
            headers = req.get("headers", {})
            if isinstance(headers, list):
                headers = {h["name"]: h["value"] for h in headers}
            auth = headers.get("authorization", "")
            if auth and not auth_token[0]:
                auth_token[0] = auth
                done[0] = True
            # Always continue the request so it doesn't block
            client.send("Fetch.continueRequest", {"requestId": req_id})
        except Exception as e:
            logger.debug("capture_auth fetch error: %s", e)

    client.on("Fetch.requestPaused", on_fetch)

    # Trigger a request by creating a session
    user_token = page.evaluate("JSON.parse(localStorage.getItem('userToken'))?.value")
    page.evaluate('''
    async (token) => {
        await fetch('/api/v0/chat_session/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': token },
            body: JSON.stringify({})
        });
    }
    ''', user_token)

    for _ in range(10):
        time.sleep(1)
        if done[0]:
            break

    try:
        client.send("Fetch.disable")
    except Exception:
        pass

    return auth_token[0]


def get_pow_via_browser(page, auth_token: str) -> str:
    """
    Get a fresh PoW challenge and solve it using the browser's WASM Worker.
    Returns the base64-encoded solution.
    """
    result = page.evaluate('''
    async (authToken) => {
        const chResp = await fetch('/api/v0/chat/create_pow_challenge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': authToken },
            body: JSON.stringify({ target_path: '/api/v0/chat/completion' })
        });
        const chData = await chResp.json();
        if (!chData.data) return { error: JSON.stringify(chData).substring(0, 300) };
        const raw = chData.data.biz_data.challenge;
        const ch = {
            algorithm: raw.algorithm,
            challenge: raw.challenge,
            salt: raw.salt,
            difficulty: raw.difficulty,
            signature: raw.signature,
            expireAt: raw.expire_at,
        };

        const solution = await new Promise((resolve, reject) => {
            const worker = new Worker('https://fe-static.deepseek.com/chat/static/37627.ebf6d8f55d.js');
            let attempts = 0;
            const trySolve = () => {
                attempts++;
                worker.postMessage({ type: 'pow-challenge', challenge: ch });
            };
            const timeout = setTimeout(() => { worker.terminate(); reject(new Error('timeout')); }, 15000);
            worker.onmessage = (evt) => {
                if (evt.data.type === 'pow-answer') {
                    clearTimeout(timeout);
                    worker.terminate();
                    resolve(evt.data.answer);
                } else if (evt.data.type === 'pow-error') {
                    if (attempts < 10) setTimeout(trySolve, 1000);
                    else {
                        clearTimeout(timeout);
                        worker.terminate();
                        const errMsg = evt.data.error && evt.data.error.message ? evt.data.error.message : JSON.stringify(evt.data.error);
                        reject(new Error('pow-error: ' + errMsg));
                    }
                }
            };
            worker.onerror = (e) => { clearTimeout(timeout); worker.terminate(); reject(new Error('worker-error: ' + e.message)); };
            setTimeout(trySolve, 3000);
        });

        return btoa(JSON.stringify(solution));
    }
    ''', auth_token)

    if isinstance(result, dict) and 'error' in result:
        raise Exception(f"PoW solve failed: {result}")

    return result


def get_pow_via_cdp_intercept(page, context) -> dict:
    """
    Use CDP Fetch to intercept the browser's own chat/completion request.
    Captures auth + PoW from the request headers.
    Strips the PoW header so the browser request fails server-side (preserves PoW).
    Returns dict with keys: auth, pow, session_id
    """
    client = context.new_cdp_session(page)
    client.send("Fetch.enable", {
        "patterns": [{"urlPattern": "*chat/completion*", "requestStage": "Request"}]
    })

    captured = {}
    intercepted = [False]

    def on_fetch_request(params):
        try:
            req_id = params["requestId"]
            req = params["request"]
            url = req["url"]

            if "chat/completion" not in url:
                return

            raw_headers = req.get("headers", {})
            if isinstance(raw_headers, list):
                headers = {h["name"]: h["value"] for h in raw_headers}
            elif isinstance(raw_headers, dict):
                headers = dict(raw_headers)
            else:
                headers = {}

            captured["auth"] = headers.get("authorization", "")
            captured["pow"] = headers.get("x-ds-pow-response", "")
            post_data = req.get("postData", "")
            if post_data:
                try:
                    pd = json.loads(post_data)
                    captured["session_id"] = pd.get("chat_session_id")
                except Exception:
                    pass

            intercepted[0] = True

            new_headers = [
                {"name": name, "value": value}
                for name, value in headers.items()
                if name.lower() != "x-ds-pow-response"
            ]
            client.send("Fetch.continueRequest", {
                "requestId": req_id,
                "headers": new_headers,
            })
        except Exception as e:
            logger.debug("Fetch intercept error: %s", e)

    client.on("Fetch.requestPaused", on_fetch_request)

    # Navigate to new chat
    try:
        page.locator('text=New chat').first.click(timeout=3000)
        time.sleep(3)
    except Exception:
        pass

    # Type and send
    try:
        el = page.locator("textarea").first
        el.wait_for(timeout=5000)
        el.click()
        time.sleep(0.3)
        el.fill("hello")
        time.sleep(0.3)
        el.press("Enter")
    except Exception as e:
        logger.warning("Could not type message: %s", e)
        # Try navigating to fresh chat
        try:
            page.goto("https://chat.deepseek.com/", timeout=10000)
            time.sleep(3)
            el = page.locator("textarea").first
            el.wait_for(timeout=5000)
            el.click()
            time.sleep(0.3)
            el.fill("hello")
            time.sleep(0.3)
            el.press("Enter")
        except Exception as e2:
            logger.error("Retry also failed: %s", e2)

    for _ in range(30):
        time.sleep(1)
        if intercepted[0]:
            break

    try:
        client.send("Fetch.disable")
    except Exception:
        pass

    if not captured.get("auth") or not captured.get("pow"):
        raise Exception("Failed to capture auth/pow via CDP intercept")

    logger.info("Captured auth token and PoW via CDP")
    return captured
