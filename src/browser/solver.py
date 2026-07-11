import logging
import uuid
import time
from playwright.sync_api import Page, BrowserContext
from config.settings import settings

logger = logging.getLogger(__name__)


def _click_with_fallback(page, selectors, timeout=10000):
    """Try multiple selectors and click the first one found."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(timeout=timeout)
            loc.click()
            logger.info("Clicked: %s", sel)
            return True
        except:
            continue
    return False


def _fill_with_fallback(page, selectors, value, timeout=10000):
    """Try multiple selectors and fill the first one found."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(timeout=timeout)
            loc.click()
            loc.fill(value)
            logger.info("Filled: %s", sel)
            return True
        except:
            continue
    return False


def _wait_for_login(page, timeout_sec=90):
    """Wait for the chat interface to appear after login."""
    selectors = [
        "textarea",
        "[contenteditable='true']",
        "div[role='textbox']",
        "#chat-input",
        "[data-testid='chat-input']",
    ]
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for sel in selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=1000):
                    return True
            except:
                pass
        time.sleep(2)
    return False


def _automated_login(page: Page) -> bool:
    """
    Perform automated email+password login on chat.deepseek.com.
    Returns True if login succeeded.
    """
    email = settings.DEEPSEEK_EMAIL
    password = settings.DEEPSEEK_PASSWORD

    if not email or not password:
        logger.error("DEEPSEEK_EMAIL / DEEPSEEK_PASSWORD not set in .env")
        return False

    logger.info("Attempting automated login with %s...", email)

    # Check if the login form is already visible (email + password fields present)
    form_visible = False
    try:
        page.wait_for_selector(
            "input[placeholder*='Phone' i], input[placeholder*='email' i], input[type='email'], input[type='password']",
            timeout=8000
        )
        form_visible = True
        logger.info("Login form already visible on page.")
    except:
        pass

    if not form_visible:
        # Try clicking a login button to reveal the form
        logger.info("Login form not visible; looking for login button...")
        _click_with_fallback(page, [
            "a:has-text('Log in')",
            "a:has-text('Sign in')",
            "button:has-text('Log in')",
            "button:has-text('Sign in')",
            "a[href*='login']",
        ], timeout=15000)
        page.wait_for_timeout(3000)

    # Step 1: Enter email
    logger.info("Step 1: Entering email...")
    email_filled = _fill_with_fallback(page, [
        "input[placeholder*='Phone' i]",
        "input[placeholder*='email' i]",
        "input[type='email']",
        "input[name='email']",
        "input[name='username']",
        "input[type='text']",
    ], email, timeout=15000)

    if not email_filled:
        logger.error("Could not find email input field.")
        page.screenshot(path="login_step_email_failed.png")
        return False

    page.wait_for_timeout(1000)

    # Step 2: Enter password (both fields are on the same page)
    logger.info("Step 2: Entering password...")
    password_filled = _fill_with_fallback(page, [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='Password' i]",
    ], password, timeout=15000)

    if not password_filled:
        logger.error("Could not find password input field.")
        page.screenshot(path="login_step_password_failed.png")
        return False

    page.wait_for_timeout(1000)

    # Step 3: Click the "Log in" button
    logger.info("Step 3: Clicking Log in button...")
    login_clicked = _click_with_fallback(page, [
        "button:has-text('Log in')",
        "button:has-text('Sign in')",
        "button:has-text('Login')",
        "button[type='submit']",
        "input[type='submit']",
    ], timeout=10000)

    if not login_clicked:
        # Fallback: press Enter on the password field
        logger.warning("No login button found; pressing Enter...")
        try:
            page.keyboard.press("Enter")
        except:
            pass

    # Step 4: Wait for redirect to chat interface
    logger.info("Step 4: Waiting for chat interface after login...")

    # First wait for the login form to disappear (means we're navigating away)
    logger.info("Waiting for login form to disappear...")
    for i in range(30):
        page.wait_for_timeout(1000)
        try:
            # Check if email input is no longer visible (we navigated away)
            email_input = page.locator("input[placeholder*='Phone' i], input[placeholder*='email' i], input[type='email']").first
            if not email_input.is_visible(timeout=500):
                logger.info("Login form disappeared – redirecting...")
                break
        except:
            logger.info("Login form disappeared – redirecting...")
            break
        if i % 5 == 0:
            logger.info("Still on login page... (%d/30)", i + 1)

    # Now wait for chat interface
    logged_in = _wait_for_login(page, timeout_sec=settings.LOGIN_TIMEOUT)

    if logged_in:
        logger.info("Automated login successful!")
        return True
    else:
        logger.error("Login failed or timed out after submitting credentials.")
        page.screenshot(path="login_failed.png")
        return False


def solve_challenge(page: Page, context: BrowserContext) -> dict:
    """
    Navigate, solve WAF, auto-login, send a dummy chat,
    and capture the Authorization token and other headers.
    """
    try:
        # 1. Navigate to the main page
        page.goto("https://chat.deepseek.com", timeout=settings.CHALLENGE_TIMEOUT * 1000)

        # 2. Wait for the WAF challenge to resolve
        logger.info("Waiting for WAF challenge to resolve...")
        page.wait_for_selector("#root", timeout=60000)
        logger.info("WAF challenge resolved – page loaded.")

        # 3. Automated login
        page.wait_for_timeout(3000)
        if not _automated_login(page):
            raise Exception("Automated login failed – check screenshots in project root.")

        # 4. Wait a bit for the app to fully initialise after login
        page.wait_for_timeout(5000)

        # 5. Now send a dummy message to trigger the API request
        logger.info("Sending dummy message to capture API headers...")
        auth_token = None
        pow_response = None
        hif_leim = None
        chat_session_id = None
        parent_message_id = None
        captured_request = {"found": False}

        def capture_headers(route, request):
            nonlocal auth_token, pow_response, hif_leim, chat_session_id, parent_message_id
            url = request.url
            logger.debug("Intercepted request: %s", url)
            if "/api/" in url and ("chat" in url or "completion" in url):
                logger.info("Captured API request: %s", url)
                auth_token = request.headers.get("authorization", "").replace("Bearer ", "")
                pow_response = request.headers.get("x-ds-pow-response")
                hif_leim = request.headers.get("x-hif-leim")
                if request.post_data:
                    import json
                    try:
                        payload = json.loads(request.post_data)
                        chat_session_id = payload.get("chat_session_id")
                        parent_message_id = payload.get("parent_message_id")
                    except:
                        pass
                captured_request["found"] = True
            route.continue_()

        # Enable interception on API paths only
        page.route("**/api/**", capture_headers)
        page.route("**/chat/**", capture_headers)

        # Type and send a message
        sent = False
        try:
            for selector in [
                "textarea[placeholder]",
                "textarea",
                "[contenteditable='true']",
                "div[role='textbox']",
                "input[type='text']",
                "#chat-input",
                "[data-testid='chat-input']",
            ]:
                try:
                    chat_input = page.locator(selector).first
                    chat_input.wait_for(timeout=3000)
                    chat_input.click()
                    page.wait_for_timeout(500)
                    chat_input.fill("hello")
                    page.wait_for_timeout(500)
                    chat_input.press("Enter")
                    sent = True
                    logger.info("Message sent via selector: %s", selector)
                    break
                except:
                    continue
        except:
            pass

        if not sent:
            try:
                page.keyboard.type("hello")
                page.keyboard.press("Enter")
                sent = True
                logger.info("Message sent via keyboard")
            except Exception as e:
                logger.warning("Failed to send message: %s", e)

        # Wait for the API request to be sent
        logger.info("Waiting for API request to be captured...")
        for i in range(20):
            page.wait_for_timeout(1000)
            if captured_request["found"]:
                logger.info("API request captured successfully!")
                break
            if i % 5 == 0:
                logger.info("Still waiting... (%d/20)", i + 1)

        # Remove the routes
        try:
            page.unroute("**/api/**")
        except:
            pass
        try:
            page.unroute("**/chat/**")
        except:
            pass

        # If still no auth_token, try to extract from page JS context
        if not auth_token:
            logger.warning("Authorization token not captured from network; trying JS extraction...")
            try:
                auth_token = page.evaluate("""
                    () => {
                        const ls = localStorage;
                        const ss = sessionStorage;
                        const keys = ['token', 'authToken', 'accessToken', 'jwt', 'authorization',
                                      'ds_token', 'auth_token', 'Bearer'];
                        for (const key of keys) {
                            let val = ls.getItem(key) || ss.getItem(key);
                            if (val) return val.replace(/^Bearer\\s+/i, '');
                        }
                        const store = ls.getItem('__ds_remote_feature_store');
                        if (store) {
                            try {
                                const data = JSON.parse(store);
                                for (const entry of Object.values(data.entries || {})) {
                                    if (entry.value && typeof entry.value === 'string' &&
                                        (entry.value.startsWith('Bearer ') || entry.value.length > 20)) {
                                        return entry.value.replace(/^Bearer\\s+/i, '');
                                    }
                                }
                            } catch(e) {}
                        }
                        const cookies = document.cookie.split(';').map(c => c.trim());
                        for (const c of cookies) {
                            if (c.startsWith('token=') || c.startsWith('auth=')) {
                                return c.split('=').slice(1).join('=');
                            }
                        }
                        return null;
                    }
                """)
                if auth_token:
                    logger.info("Token extracted from JS storage.")
            except Exception as e:
                logger.debug("JS token extraction failed: %s", e)

        # Try to get pow_response from JS
        if not pow_response:
            try:
                pow_response = page.evaluate("""
                    () => {
                        const ls = localStorage;
                        const ss = sessionStorage;
                        for (const key of ['pow_response', 'x-ds-pow-response', 'powResponse', 'pow']) {
                            let val = ls.getItem(key) || ss.getItem(key);
                            if (val) return val;
                        }
                        return null;
                    }
                """)
                if pow_response:
                    logger.info("pow_response extracted from JS storage.")
            except:
                pass

        # Get cookies
        cookies = context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}

        # Also capture sessionId from sessionStorage
        session_id_from_storage = page.evaluate("sessionStorage.getItem('sessionId')")

        # Log what we captured
        logger.info("=" * 60)
        logger.info("CAPTURE RESULTS:")
        logger.info("  auth_token: %s", "YES" if auth_token else "NO")
        logger.info("  pow_response: %s", "YES" if pow_response else "NO")
        logger.info("  hif_leim: %s", "YES" if hif_leim else "NO")
        logger.info("  chat_session_id: %s", "YES" if chat_session_id else "NO")
        logger.info("  parent_message_id: %s", "YES" if parent_message_id else "NO")
        logger.info("  cookies: %d captured", len(cookie_dict))
        logger.info("=" * 60)

        return {
            "cookies": cookie_dict,
            "auth_token": auth_token,
            "pow_response": pow_response,
            "hif_leim": hif_leim,
            "chat_session_id": chat_session_id or session_id_from_storage or str(uuid.uuid4()),
            "parent_message_id": parent_message_id,
            "expiry": None
        }

    except Exception as e:
        logger.error("Failed to solve challenge: %s", e)
        raise
