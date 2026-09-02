"""Apify actor entry point: resolve people to Google Scholar profiles and scrape them."""

from __future__ import annotations

import asyncio
from typing import Any

from apify import Actor
from selenium.common.exceptions import WebDriverException

from .browser import accept_google_consent, create_driver
from .proxy_relay import ProxyAuthRelay
from .scholar import BlockedError, build_search_query, scrape_profile, search_profile


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        searches = _normalize_searches(actor_input.get('searches') or [])
        profile_urls = [url for url in (actor_input.get('profileUrls') or []) if str(url).strip()]
        if not searches and not profile_urls:
            raise ValueError('Provide at least one entry in `searches` or `profileUrls`.')

        settings = {
            'headless': actor_input.get('headless', True),
            'timeout_seconds': int(actor_input.get('timeoutSeconds', 20)),
            'max_publications': int(actor_input.get('maxPublications', 100)),
            'keyword_top_n': int(actor_input.get('keywordTopN', 6)),
        }
        max_retries = int(actor_input.get('maxRetriesPerTarget', 3))

        proxy_configuration = await Actor.create_proxy_configuration(
            actor_proxy_input=actor_input.get('proxyConfiguration'),
        )

        targets: list[dict[str, Any]] = [{'type': 'search', **search} for search in searches]
        targets += [{'type': 'profile', 'profileUrl': str(url).strip()} for url in profile_urls]
        Actor.log.info('Processing %d target(s).', len(targets))

        for index, target in enumerate(targets, start=1):
            label = target.get('name') or target.get('profileUrl')
            Actor.log.info('[%d/%d] %s', index, len(targets), label)
            result = await _process_with_retries(
                target,
                proxy_configuration=proxy_configuration,
                max_retries=max_retries,
                settings=settings,
            )
            await Actor.push_data(result)


async def _process_with_retries(
    target: dict[str, Any],
    *,
    proxy_configuration: Any,
    max_retries: int,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Run one target, retrying behind a fresh proxy session whenever Google blocks us."""
    last_error = 'unknown error'
    for attempt in range(1, max_retries + 1):
        # A new session id gives a different proxy exit IP on each retry.
        proxy_url = await proxy_configuration.new_url(session_id=f'scholar_{attempt}_{abs(hash(str(target))) % 10**8}') if proxy_configuration else None
        relay = None
        try:
            if proxy_url:
                relay = await ProxyAuthRelay(proxy_url).start()
            result = await asyncio.to_thread(
                _run_target,
                target,
                relay.chrome_proxy_server if relay else None,
                settings,
            )
            if result.get('status') != 'blocked':
                return result
            last_error = result.get('message', 'blocked')
        except (WebDriverException, OSError) as error:
            last_error = f'{type(error).__name__}: {error}'
        finally:
            if relay is not None:
                await relay.stop()
        Actor.log.warning('Attempt %d/%d failed: %s', attempt, max_retries, last_error)

    return {**_target_echo(target), 'status': 'failed', 'message': last_error}


def _run_target(target: dict[str, Any], proxy_server: str | None, settings: dict[str, Any]) -> dict[str, Any]:
    """Blocking Selenium work for a single target; runs in a worker thread."""
    driver = create_driver(
        headless=settings['headless'],
        proxy_server=proxy_server,
        page_load_timeout=settings['timeout_seconds'] + 10,
    )
    try:
        if target['type'] == 'search':
            accept_google_consent(driver)
            found = search_profile(
                driver,
                name=target['name'],
                affiliation=target.get('affiliation', ''),
                timeout_seconds=settings['timeout_seconds'],
            )
            if found['status'] != 'ok':
                return {**_target_echo(target), **found}
            profile_url = found['profileUrl']
            search_meta = {'query': found['query'], 'foundVia': found['via']}
        else:
            profile_url = target['profileUrl']
            search_meta = {'query': None, 'foundVia': 'input'}

        scraped = scrape_profile(
            driver,
            profile_url=profile_url,
            timeout_seconds=settings['timeout_seconds'],
            max_publications=settings['max_publications'],
            keyword_top_n=settings['keyword_top_n'],
        )
        return {**_target_echo(target), **search_meta, **scraped}
    except BlockedError as error:
        return {**_target_echo(target), 'status': 'blocked', 'message': str(error)}
    finally:
        driver.quit()


def _target_echo(target: dict[str, Any]) -> dict[str, Any]:
    """Input fields echoed onto every dataset row so results can be joined back to the request."""
    return {
        'inputName': target.get('name'),
        'inputAffiliation': target.get('affiliation'),
        'inputProfileUrl': target.get('profileUrl'),
        'searchQuery': build_search_query(target['name'], target.get('affiliation', '')) if target.get('name') else None,
    }


def _normalize_searches(searches: list[Any]) -> list[dict[str, str]]:
    """Accept either plain name strings or {name, affiliation} objects."""
    normalized = []
    for entry in searches:
        if isinstance(entry, str):
            name, affiliation = entry.strip(), ''
        elif isinstance(entry, dict):
            name = str(entry.get('name') or '').strip()
            affiliation = str(entry.get('affiliation') or entry.get('university') or '').strip()
        else:
            continue
        if name:
            normalized.append({'name': name, 'affiliation': affiliation})
    return normalized
