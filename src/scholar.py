"""Find and scrape Google Scholar citation profiles with Selenium."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .keywords import extract_keywords

LUCKY_URL = 'https://www.google.com/search?q={query}&btnI=1&hl=en'
RESULTS_URL = 'https://www.google.com/search?q={query}&hl=en&num=20'
SCHOLAR_HOST = 'scholar.google.com'
SCHOLAR_PATH_MARKER = '/citations'
BLOCK_MARKERS = ('/sorry/', 'unusual traffic', 'not a robot')


class BlockedError(RuntimeError):
    """Raised when Google serves a CAPTCHA or rate-limit page instead of results."""


def build_search_query(name: str, affiliation: str = '') -> str:
    return ' '.join(part for part in (name, affiliation, 'google scholar') if part).strip()


def search_profile(driver, *, name: str, affiliation: str = '', timeout_seconds: int = 20) -> dict[str, Any]:
    """Resolve a person to a Scholar profile URL via Google's "I'm Feeling Lucky" redirect.

    `btnI=1` usually lands straight on the profile; when Google decides to show a results
    page anyway, the first Scholar citations link on that page is used instead.
    """
    query = build_search_query(name, affiliation)

    driver.get(LUCKY_URL.format(query=quote_plus(query)))
    _raise_if_blocked(driver)
    landed = _as_profile_match(driver.current_url, title=driver.title)
    if landed is not None:
        return {'status': 'ok', 'query': query, 'via': 'lucky', **landed}

    driver.get(RESULTS_URL.format(query=quote_plus(query)))
    _raise_if_blocked(driver)
    try:
        WebDriverWait(driver, timeout_seconds).until(lambda active: active.find_elements(By.CSS_SELECTOR, 'a[href]'))
    except TimeoutException:
        return {'status': 'timeout', 'query': query, 'message': 'Timed out waiting for Google search results.'}

    match = _first_scholar_result(driver)
    if match is None:
        return {
            'status': 'not_found',
            'query': query,
            'message': 'No Google Scholar profile link was found for this person.',
        }
    return {'status': 'ok', 'query': query, 'via': 'results_page', **match}


def scrape_profile(
    driver,
    *,
    profile_url: str,
    timeout_seconds: int = 20,
    max_publications: int = 100,
    keyword_top_n: int = 6,
) -> dict[str, Any]:
    """Load a Scholar profile and return its metadata, publications and derived analytics."""
    driver.get(_normalize_profile_url(profile_url))
    _raise_if_blocked(driver)
    try:
        WebDriverWait(driver, timeout_seconds).until(EC.presence_of_element_located((By.CSS_SELECTOR, '#gsc_prf_in')))
    except TimeoutException:
        return {
            'status': 'timeout',
            'profileUrl': profile_url,
            'message': 'Timed out while loading the Google Scholar profile page.',
        }

    _expand_publications(driver, max_publications=max_publications, timeout_seconds=timeout_seconds)
    publications = _extract_publications(driver, max_publications=max_publications)
    metrics = _extract_metrics(driver)

    return {
        'status': 'ok',
        'profileUrl': driver.current_url,
        'scholarId': _scholar_id_from_url(driver.current_url),
        'name': _text(driver, '#gsc_prf_in'),
        'affiliation': _text(driver, '.gsc_prf_il'),
        'interests': [element.text.strip() for element in driver.find_elements(By.CSS_SELECTOR, '#gsc_prf_int a') if element.text.strip()],
        'imageUrl': _attribute(driver, '#gsc_prf_pup-img', 'src'),
        **metrics,
        'publications': publications,
        **analyze_publications(publications, keyword_top_n=keyword_top_n),
        'scrapedAt': datetime.now(timezone.utc).isoformat(),
    }


def analyze_publications(
    publications: list[dict[str, Any]],
    *,
    current_year: int | None = None,
    keyword_top_n: int = 6,
) -> dict[str, Any]:
    """Summarize a publication list: recent output, top recent papers, current topics."""
    effective_year = current_year or datetime.now(timezone.utc).year
    recent_publications = sorted(
        (
            publication
            for publication in publications
            if publication.get('year') in {effective_year, effective_year - 1}
        ),
        key=lambda publication: (-(publication['year'] or 0), publication['title'].lower()),
    )
    window_start = effective_year - 2
    top_cited_last_3_years = sorted(
        (publication for publication in publications if (publication.get('year') or 0) >= window_start),
        key=lambda publication: (publication.get('citations', 0), publication.get('year') or 0),
        reverse=True,
    )[:3]
    latest_year = max((publication['year'] for publication in publications if publication.get('year')), default=None)

    return {
        'publicationsCount': len(publications),
        'recentPublications': recent_publications,
        'topCitedLast3Years': top_cited_last_3_years,
        'latestPublicationYear': latest_year,
        'latestPublicationYearDomains': _domains_for_year(publications, latest_year, keyword_top_n=keyword_top_n),
    }


def _domains_for_year(publications: list[dict[str, Any]], year: int | None, *, keyword_top_n: int) -> list[str]:
    if year is None:
        return []
    corpus = [
        ' '.join(filter(None, (publication.get('title', ''), publication.get('venue', '')))).strip()
        for publication in publications
        if publication.get('year') == year
    ]
    return extract_keywords([text for text in corpus if text], top_n=keyword_top_n)


def _expand_publications(driver, *, max_publications: int, timeout_seconds: int) -> None:
    """Click "Show more" until enough rows are loaded or Scholar runs out of them."""
    while True:
        row_count = len(driver.find_elements(By.CSS_SELECTOR, 'tr.gsc_a_tr'))
        if row_count >= max_publications:
            return
        # Scholar disables the button while a batch loads and leaves it disabled once the
        # list is exhausted, so wait it out rather than reading `disabled` mid-flight.
        try:
            WebDriverWait(driver, timeout_seconds).until(_more_button_is_enabled)
        except TimeoutException:
            return
        button = _more_button(driver)
        if button is None:
            return
        driver.execute_script('arguments[0].click();', button)
        try:
            WebDriverWait(driver, timeout_seconds).until(
                lambda active: len(active.find_elements(By.CSS_SELECTOR, 'tr.gsc_a_tr')) > row_count,
            )
        except TimeoutException:
            return


def _more_button(driver):
    buttons = driver.find_elements(By.CSS_SELECTOR, '#gsc_bpf_more')
    return buttons[0] if buttons else None


def _more_button_is_enabled(driver) -> bool:
    button = _more_button(driver)
    return button is not None and not button.get_attribute('disabled')


def _extract_publications(driver, *, max_publications: int) -> list[dict[str, Any]]:
    publications = []
    for row in driver.find_elements(By.CSS_SELECTOR, 'tr.gsc_a_tr')[:max_publications]:
        title_elements = row.find_elements(By.CSS_SELECTOR, '.gsc_a_at')
        if not title_elements:
            continue
        title = title_elements[0].text.strip()
        if not title:
            continue
        gray_lines = [element.text.strip() for element in row.find_elements(By.CSS_SELECTOR, '.gs_gray')]
        publications.append(
            {
                'title': title,
                'year': _to_int(_row_text(row, '.gsc_a_y')),
                'citations': _to_int(_row_text(row, '.gsc_a_c')) or 0,
                'authors': [author.strip() for author in gray_lines[0].split(',')] if gray_lines else [],
                'venue': gray_lines[1] if len(gray_lines) > 1 else '',
                'url': title_elements[0].get_attribute('href') or '',
            },
        )
    return publications


def _extract_metrics(driver) -> dict[str, Any]:
    """Read the Cited by / h-index / i10-index table (all-time and last-5-years columns)."""
    metrics: dict[str, Any] = {'citedBy': 0, 'citedBySince': 0, 'hIndex': 0, 'hIndexSince': 0, 'i10Index': 0, 'i10IndexSince': 0}
    label_to_keys = {
        'citations': ('citedBy', 'citedBySince'),
        'h-index': ('hIndex', 'hIndexSince'),
        'i10-index': ('i10Index', 'i10IndexSince'),
    }
    for row in driver.find_elements(By.CSS_SELECTOR, '#gsc_rsb_st tbody tr'):
        cells = row.find_elements(By.CSS_SELECTOR, 'td')
        if len(cells) < 3:
            continue
        keys = label_to_keys.get(cells[0].text.strip().lower())
        if keys is None:
            continue
        metrics[keys[0]] = _to_int(cells[1].text) or 0
        metrics[keys[1]] = _to_int(cells[2].text) or 0
    return metrics


def _first_scholar_result(driver) -> dict[str, Any] | None:
    for anchor in driver.find_elements(By.CSS_SELECTOR, 'a[href]'):
        match = _as_profile_match(_unwrap_redirect(anchor.get_attribute('href') or ''), title=anchor.text.strip())
        if match is not None:
            return match
    return None


def _as_profile_match(url: str, *, title: str = '') -> dict[str, Any] | None:
    parsed = urlparse(url)
    if SCHOLAR_HOST not in parsed.netloc or SCHOLAR_PATH_MARKER not in parsed.path:
        return None
    scholar_id = parse_qs(parsed.query).get('user', [''])[0]
    if not scholar_id:
        return None
    return {'profileUrl': _normalize_profile_url(url), 'scholarId': scholar_id, 'resultTitle': title}


def _normalize_profile_url(url: str) -> str:
    scholar_id = parse_qs(urlparse(url).query).get('user', [''])[0]
    return f'https://{SCHOLAR_HOST}/citations?user={scholar_id}&hl=en' if scholar_id else url


def _scholar_id_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query).get('user', [''])[0]


def _unwrap_redirect(href: str) -> str:
    """Google sometimes wraps results in /url?q=<target>."""
    parsed = urlparse(href)
    if parsed.netloc.endswith('google.com') and parsed.path == '/url':
        return parse_qs(parsed.query).get('q', [''])[0]
    return href


def _raise_if_blocked(driver) -> None:
    try:
        haystack = f'{driver.current_url} {driver.title}'.lower()
    except WebDriverException:
        return
    if any(marker in haystack for marker in BLOCK_MARKERS):
        raise BlockedError(f'Google served a block/CAPTCHA page: {driver.current_url}')


def _text(driver, selector: str) -> str:
    elements = driver.find_elements(By.CSS_SELECTOR, selector)
    return elements[0].text.strip() if elements else ''


def _attribute(driver, selector: str, attribute: str) -> str:
    elements = driver.find_elements(By.CSS_SELECTOR, selector)
    return (elements[0].get_attribute(attribute) or '') if elements else ''


def _row_text(row, selector: str) -> str:
    elements = row.find_elements(By.CSS_SELECTOR, selector)
    return elements[0].text.strip() if elements else ''


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
