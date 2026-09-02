"""Chrome driver setup shared by the search and profile-scraping steps."""

from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions

USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
)


def create_driver(*, headless: bool = True, proxy_server: str | None = None, page_load_timeout: int = 30):
    """Build a Chrome driver tuned for Google, optionally behind an unauthenticated proxy endpoint.

    `proxy_server` must be credential-free (Chrome drops embedded credentials); point it at a
    `ProxyAuthRelay` when the upstream proxy needs a username and password.
    """
    options = ChromeOptions()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1600,1200')
    options.add_argument('--lang=en-US')
    options.add_argument(f'--user-agent={USER_AGENT}')
    options.add_argument('--disable-blink-features=AutomationControlled')
    if proxy_server:
        options.add_argument(f'--proxy-server={proxy_server}')

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(page_load_timeout)
    return driver


def accept_google_consent(driver) -> None:
    """Pre-set Google's consent cookie so EU proxy exits skip the interstitial."""
    try:
        driver.get('https://www.google.com/')
        driver.add_cookie({'name': 'SOCS', 'value': 'CAESHAgBEhIaAB', 'domain': '.google.com'})
        driver.add_cookie({'name': 'CONSENT', 'value': 'YES+', 'domain': '.google.com'})
    except Exception:  # noqa: BLE001 - consent priming is best-effort
        pass
