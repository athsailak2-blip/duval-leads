#!/usr/bin/env python3
"""
TinyFish Browser API helper for the general scraping framework.

Some Duval County sources (realforeclose.com, realtaxdeed.com) block
datacenter IPs with 403 / 504. TinyFish spins up a managed browser on
its own clean IP and hands back a CDP WebSocket URL. We attach Playwright
to that and drive the page normally -- exactly like a local browser, but
without the bot-block.

Flow:
  1. POST https://api.browser.tinyfish.ai  (X-API-Key header)
     body {"url": <start url>, "timeout_seconds": 300}
  2. Response gives cdp_url (wss://...). Connect via
     playwright.chromium.connect_over_cdp(cdp_url).
  3. Use the page. When done, DELETE the session to free it.

The API key is read from the .tinyfish_key file (gitignored) or the
TINYFISH_API_KEY env var -- never hard-coded.
"""
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE.parent / ".tinyfish_key"
API_URL = "https://api.browser.tinyfish.ai"


def _api_key():
    if os.environ.get("TINYFISH_API_KEY"):
        return os.environ["TINYFISH_API_KEY"].strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    raise RuntimeError("No TinyFish API key: set TINYFISH_API_KEY or put it in .tinyfish_key")


def create_session(start_url="about:blank", timeout_seconds=300):
    """Create a TinyFish browser session; return (session_id, cdp_url)."""
    import urllib.request

    key = _api_key()
    req = urllib.request.Request(
        API_URL,
        data=json.dumps({"url": start_url, "timeout_seconds": timeout_seconds}).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    # session start can take 10-30s
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read())
    return body["session_id"], body["cdp_url"]


def destroy_session(session_id):
    import urllib.request

    key = _api_key()
    req = urllib.request.Request(
        f"{API_URL}/{session_id}",
        headers={"X-API-Key": key},
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"[tinyfish] destroy session {session_id} warning: {e}")


class TinyFishBrowser:
    """Context manager: yields a Playwright page running on TinyFish's IP.

    Usage:
        with TinyFishBrowser("https://www.duval.realtaxdeed.com") as page:
            page.goto(...)
            ...
    Always destroys the session on exit.
    """

    def __init__(self, start_url="about:blank", timeout_seconds=300):
        self.start_url = start_url
        self.timeout_seconds = timeout_seconds
        self.session_id = None
        self.cdp_url = None
        self._p = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.session_id, self.cdp_url = create_session(self.start_url, self.timeout_seconds)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return self.page

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass
        if self.session_id:
            destroy_session(self.session_id)


def fetch_html(url, wait_for="domcontentloaded", extra_wait=2000, timeout_seconds=300):
    """Convenience: open `url` in a TinyFish browser, return rendered HTML.

    Use for sources that block direct requests but render server-side HTML
    (e.g. realtaxdeed.com listing pages). For JS-heavy grids, use the
    TinyFishBrowser context manager and drive the page directly.
    """
    with TinyFishBrowser(url, timeout_seconds) as page:
        page.goto(url, wait_until=wait_for, timeout=60000)
        if extra_wait:
            time.sleep(extra_wait / 1000)
        return page.content()


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "https://www.duval.realtaxdeed.com"
    html = fetch_html(target, extra_wait=3000)
    print(f"fetched {len(html)} bytes from {target}")
    print(html[:300])
