#!/usr/bin/env python3
"""
CUSTOMIZE ME — then copy to /etc/clinicjam/portal_click.py

This is the ONE part of the captive-portal automation that's specific to
your company's guest Wi-Fi and can't be shipped generically. clinicjam-
portal-watch runs this script whenever it detects the Pi's uplink is
stuck behind the portal. Exit 0 means "handled it"; any other exit code
means "didn't work", and the watcher tries again next cycle.

────────────────────────────────────────────────────────────────────────
STEP 1 — CAPTURE WHAT YOUR PORTAL ACTUALLY DOES (5 minutes, one time)
────────────────────────────────────────────────────────────────────────
1. On a laptop, forget/rejoin the company guest Wi-Fi so the portal
   triggers fresh.
2. Open your browser's DevTools -> Network tab -> check "Preserve log".
3. Click through the portal exactly as a guest would ("I agree",
   "Connect", whatever it says).
4. In the Network tab, find the request that fires the instant you click
   that button — usually a GET with a long token in the URL, or a POST
   with form fields like `agree=true`. Right-click it -> Copy -> Copy as
   cURL. That single request is almost always the whole story.
5. Paste it into OPTION B below almost unmodified, or translate it into
   OPTION A if you'd rather use plain urllib. Delete whichever option(s)
   you don't use.

If the button doesn't fire a plain request but runs JavaScript instead
(check the Network tab — if nothing new appears when you click, that's
your sign), use OPTION C, which drives a real (headless) browser.
────────────────────────────────────────────────────────────────────────
"""
import re
import subprocess
import sys
import urllib.request


# ---------------------------------------------------------------------
# OPTION A — plain GET/POST via Python's stdlib. Good fit when the
# portal is a single link, possibly with a session token you need to
# read out of the splash page's HTML first.
# ---------------------------------------------------------------------
def click_simple_accept() -> bool:
    PORTAL_PAGE = "http://192.0.2.1/guest/splash"          # <- edit
    ACCEPT_URL_TEMPLATE = "http://192.0.2.1/guest/accept?token={token}"  # <- edit

    page = urllib.request.urlopen(PORTAL_PAGE, timeout=10).read().decode("utf-8", "ignore")
    match = re.search(r'name="token"\s+value="([^"]+)"', page)  # <- edit to match your HTML
    if not match:
        return False
    resp = urllib.request.urlopen(ACCEPT_URL_TEMPLATE.format(token=match.group(1)), timeout=10)
    return resp.status in (200, 204, 302)


# ---------------------------------------------------------------------
# OPTION B — shell out to a curl command pasted almost verbatim from
# your browser's "Copy as cURL". Usually the fastest path to something
# that works, since you don't have to translate headers/cookies by hand.
# ---------------------------------------------------------------------
def click_via_curl() -> bool:
    result = subprocess.run(
        [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "http://192.0.2.1/guest/accept?token=REPLACE_ME",  # <- paste your captured URL
        ],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip() in ("200", "204", "302")


# ---------------------------------------------------------------------
# OPTION C — real (headless) browser click, for portals whose button
# runs JavaScript instead of a plain link. Heavier: needs
#   /opt/clinicjam/venv/bin/pip install playwright
#   /opt/clinicjam/venv/bin/playwright install --with-deps chromium
# Only reach for this if Options A/B genuinely don't apply.
# ---------------------------------------------------------------------
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
    # Keep exactly one of these — delete the function calls (and the
    # unused functions above) once you know which pattern fits your portal.
    ok = click_simple_accept()
    sys.exit(0 if ok else 1)
