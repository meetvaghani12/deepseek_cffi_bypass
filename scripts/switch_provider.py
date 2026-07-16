#!/usr/bin/env python3
"""
Toggle Claude Code's model provider between real Anthropic Claude and the local
DeepSeek proxy, by editing `env.ANTHROPIC_BASE_URL` in ~/.claude/settings.json.

Usage:
    python scripts/switch_provider.py deepseek   # point Claude Code at the DeepSeek proxy
    python scripts/switch_provider.py claude      # restore the previous provider
    python scripts/switch_provider.py toggle      # flip between the two
    python scripts/switch_provider.py status      # show current provider

Safety:
  - The value that was in ANTHROPIC_BASE_URL before switching to DeepSeek is saved
    under a private key so `claude` restores exactly what was there (e.g. an existing
    auth proxy at http://127.0.0.1:48219), never blindly deleted.
  - Writes are atomic (temp file + os.replace) and a timestamped backup is kept.
  - The proxy is health-checked before switching TO deepseek, so you don't silently
    point Claude Code at a dead endpoint.
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.request

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
DEFAULT_PROXY_URL = os.environ.get("DEEPSEEK_PROXY_URL", "http://127.0.0.1:5051")
# Where we stash the pre-DeepSeek base URL so `claude` can restore it.
SAVED_KEY = "ANTHROPIC_BASE_URL_SAVED_BY_DEEPSEEK_SWITCH"


def _load():
    if not os.path.exists(SETTINGS_PATH):
        return {}
    with open(SETTINGS_PATH, "r") as f:
        return json.load(f)


def _atomic_write(obj):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    if os.path.exists(SETTINGS_PATH):
        backup = f"{SETTINGS_PATH}.bak.{int(time.time())}"
        shutil.copy2(SETTINGS_PATH, backup)
    tmp = f"{SETTINGS_PATH}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS_PATH)


def _proxy_up(url, timeout=2.0):
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _current(settings):
    env = settings.get("env", {}) or {}
    url = env.get("ANTHROPIC_BASE_URL", "")
    if url.rstrip("/") == DEFAULT_PROXY_URL.rstrip("/"):
        return "deepseek", url
    if url:
        return "claude", url
    return "claude", "(default Anthropic API)"


def cmd_status():
    settings = _load()
    provider, url = _current(settings)
    saved = (settings.get("env", {}) or {}).get(SAVED_KEY)
    print(f"Claude Code provider: {provider}")
    print(f"  ANTHROPIC_BASE_URL = {url or '(unset — default Anthropic)'}")
    if saved is not None:
        print(f"  (saved pre-DeepSeek value: {saved or '(was unset)'})")
    return 0


def cmd_deepseek():
    settings = _load()
    env = settings.setdefault("env", {})
    provider, current_url = _current(settings)

    if provider == "deepseek":
        print(f"Already on DeepSeek ({DEFAULT_PROXY_URL}).")
        return 0

    if not _proxy_up(DEFAULT_PROXY_URL):
        print(f"ERROR: DeepSeek proxy not reachable at {DEFAULT_PROXY_URL}/health")
        print("Start it first:  PYTHONPATH=. python scripts/proxy_server.py")
        return 1

    # Save whatever is there now so `claude` can restore it (may be an existing proxy).
    if SAVED_KEY not in env:
        env[SAVED_KEY] = env.get("ANTHROPIC_BASE_URL", "")
    env["ANTHROPIC_BASE_URL"] = DEFAULT_PROXY_URL
    _atomic_write(settings)
    print(f"Switched Claude Code -> DeepSeek proxy ({DEFAULT_PROXY_URL}).")
    print("Restart Claude Code for the change to take effect.")
    return 0


def cmd_claude():
    settings = _load()
    env = settings.setdefault("env", {})

    saved = env.pop(SAVED_KEY, None)
    if saved:
        env["ANTHROPIC_BASE_URL"] = saved
        restored = saved
    else:
        # Nothing saved -> remove our override entirely (default Anthropic API).
        env.pop("ANTHROPIC_BASE_URL", None)
        restored = "(default Anthropic API)"
    if not env:
        settings.pop("env", None)
    _atomic_write(settings)
    print(f"Restored Claude Code provider -> {restored}.")
    print("Restart Claude Code for the change to take effect.")
    return 0


def cmd_toggle():
    provider, _ = _current(_load())
    return cmd_claude() if provider == "deepseek" else cmd_deepseek()


def main():
    p = argparse.ArgumentParser(description="Toggle Claude Code between Claude and the DeepSeek proxy.")
    p.add_argument("action", choices=["deepseek", "claude", "toggle", "status"])
    args = p.parse_args()
    return {
        "deepseek": cmd_deepseek,
        "claude": cmd_claude,
        "toggle": cmd_toggle,
        "status": cmd_status,
    }[args.action]()


if __name__ == "__main__":
    sys.exit(main())
