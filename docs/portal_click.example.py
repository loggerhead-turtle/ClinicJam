#!/usr/bin/env python3
"""
Then copy to /etc/clinicjam/portal_click.py

clinicjam-portal-watch runs this script whenever it detects the Pi's own
uplink is stuck behind the company Wi-Fi's captive portal. Exit 0 means
"handled it"; any other exit code means "didn't work", and the watcher
tries again next cycle.

If your portal is JUST a single "I agree" / "Connect" button — no
username, no email, no CAPTCHA — OPTION A below (the default, already
wired up in __main__) usually works completely unmodified: it follows
the portal's redirect to find the splash page, then submits whatever
form or link is on it automatically. No manual capture needed. Try it
as-is first:

    cp /opt/clinicjam/docs/portal_click.example.py /etc/clinicjam/portal_click.py
    sudo systemctl restart clinicjam-portal-watch
    sudo journalctl -u clinicjam-portal-watch -f

If that doesn't get you online within a couple of check cycles, fall
back to OPTION B (manual capture) or OPTION C (headless browser) below.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from html.parser import HTMLParser

CONFIG_PATH = "/etc/clinicjam/config.toml"

# Words that mark a link as "the accept button" rather than, say, a link
# to the terms-of-service PDF sitting next to it on the same splash page.
ACCEPT_KEYWORDS = ("accept", "agree", "connect", "continue", "login", "guest", "ok")


# =======================================================================
# OPTION A (default) — automatic: no portal-specific edits needed for a
# plain "click to accept" page. Finds the splash page by following the
# same connectivity probe portal_watch.py uses, then submits the first
# <form> it finds (carrying along whatever hidden fields — session
# tokens, your device's MAC — the portal already baked in), or failing
# that, follows the most button-like <a href> link on the page.
# =======================================================================

class _SplashPageParts(HTMLParser):
    """Pulls the first <form> (action/method/fields) and every <a href>
    out of the splash page's HTML — enough to drive either portal style."""

    def __init__(self):
        super().__init__()
        self.form: dict | None = None
        self.links: list[tuple[str, str]] = []
        self._in_form = False
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and self.form is None:
            self._in_form = True
            self.form = {
                "action": attrs.get("action", ""),
                "method": attrs.get("method", "get").lower(),
                "fields": {},
            }
        elif tag in ("input", "button") and self._in_form:
            name = attrs.get("name")
            if name:
                self.form["fields"][name] = attrs.get("value", "")
        elif tag == "a":
            self._link_href = attrs.get("href")
            self._link_text = []

    def handle_data(self, data):
        if self._link_href is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "a" and self._link_href is not None:
            self.links.append((self._link_href, "".join(self._link_text).strip()))
            self._link_href = None


def _probe_url() -> str:
    try:
        with open(CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("uplink", {}).get("captive_portal", {}).get(
            "probe_url", "http://connectivitycheck.gstatic.com/generate_204"
        )
    except OSError:
        return "http://connectivitycheck.gstatic.com/generate_204"


def _find_splash_page(probe_url: str) -> tuple[str, str] | None:
    """Follow the probe URL to wherever the portal redirects it. Returns
    (final_url, html) — or None if we already have real internet."""
    req = urllib.request.Request(
        probe_url, headers={"User-Agent": "Mozilla/5.0 (compatible; ClinicJam)"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
        if resp.status == 204 and not body:
            return None
        return resp.geturl(), body.decode("utf-8", "ignore")


def _submit_form(base_url: str, form: dict) -> bool:
    action = urllib.parse.urljoin(base_url, form["action"] or base_url)
    data = urllib.parse.urlencode(form["fields"]).encode()
    if form["method"] == "post":
        req = urllib.request.Request(action, data=data, method="POST")
    else:
        req = urllib.request.Request(f"{action}?{data.decode()}", method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status < 400


def _follow_best_link(base_url: str, links: list[tuple[str, str]]) -> bool:
    def score(link):
        href, text = link
        haystack = f"{href} {text}".lower()
        return sum(kw in haystack for kw in ACCEPT_KEYWORDS)

    candidates = [l for l in links if l[0] and not l[0].startswith("#")]
    if not candidates:
        return False
    best = max(candidates, key=score)
    if score(best) == 0:
        return False  # nothing looked like an accept link — don't guess wrong
    with urllib.request.urlopen(urllib.parse.urljoin(base_url, best[0]), timeout=10) as resp:
        return resp.status < 400


def click_auto() -> bool:
    found = _find_splash_page(_probe_url())
    if found is None:
        return True  # already online, nothing to click
    splash_url, html = found
    parts = _SplashPageParts()
    parts.feed(html)
    if parts.form is not None:
        return _submit_form(splash_url, parts.form)
    return _follow_best_link(splash_url, parts.links)


# =======================================================================
# OPTION B — manual capture, for portals OPTION A doesn't handle (e.g.
# a token computed by inline JavaScript before the form submits).
#
#   1. On a laptop, forget/rejoin the guest Wi-Fi so the portal triggers.
#   2. DevTools -> Network tab -> check "Preserve log".
#   3. Click the portal's button as normal.
#   4. Find the request that fired at that instant. Right-click it ->
#      Copy -> Copy as cURL.
#   5. Paste it below almost unmodified.
# =======================================================================

def click_via_curl() -> bool:
    result = subprocess.run(
        [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "http://192.0.2.1/guest/accept?token=REPLACE_ME",  # <- paste your captured URL
        ],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip() in ("200", "204", "302")


# =======================================================================
# OPTION C — real (headless) browser click, for portals whose button
# runs JavaScript instead of a plain link/form submit. Heavier: needs
#   /opt/clinicjam/venv/bin/pip install playwright
#   /opt/clinicjam/venv/bin/playwright install --with-deps chromium
# Only reach for this if Options A/B genuinely don't apply.
# =======================================================================

def click_via_browser() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://192.0.2.1/guest/splash", timeout=15000)  # <- edit
        page.click("text=I agree")  # <- edit to match your portal's button text
        page.wait_for_timeout(2000)
        browser.close()
    return True


if __name__ == "__main__":
    # Swap this for click_via_curl() or click_via_browser() if the
    # automatic option doesn't work for your portal.
    ok = click_auto()
    sys.exit(0 if ok else 1)
