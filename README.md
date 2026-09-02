# Google Scholar Profile Scraper

An [Apify](https://apify.com) actor that resolves researchers to their Google Scholar
citation profiles using Google's **"I'm Feeling Lucky"** redirect, then scrapes the
profile with Selenium: metrics, publications, and the topics they are working on now.

## How it works

1. For each person, it builds the query `<name> <affiliation> google scholar` and hits
   `google.com/search?q=...&btnI=1`. `btnI` skips the results page and redirects straight
   to the top hit — for a name + university that is almost always the Scholar profile.
2. If Google serves a results page anyway (it sometimes ignores `btnI`), the actor falls
   back to the first `scholar.google.com/citations?user=...` link on that page.
3. On the profile it clicks **Show more** until `maxPublications` rows are loaded, then
   parses the profile header, the citation metrics table, and every publication row.
4. Each person becomes one dataset item.

## Input

| Field | Type | Description |
| --- | --- | --- |
| `searches` | array | People to look up: `{ "name": "...", "affiliation": "..." }` (plain name strings also work). |
| `profileUrls` | array | Scholar profile URLs to scrape directly, skipping the search step. |
| `maxPublications` | integer | Publications to load per profile (default `100`). |
| `keywordTopN` | integer | Keywords to extract from the latest year's titles (default `6`). |
| `timeoutSeconds` | integer | Per-page load timeout (default `20`). |
| `proxyConfiguration` | object | Apify proxy settings. **Residential is strongly recommended.** |
| `headless` | boolean | Run Chrome headless (default `true`). |
| `maxRetriesPerTarget` | integer | Retries with a fresh proxy session when Google blocks (default `3`). |

```json
{
  "searches": [
    { "name": "Jayanta Banik", "affiliation": "University of California Riverside" },
    { "name": "Konstantinos Karydis", "affiliation": "UC Riverside" }
  ],
  "maxPublications": 100,
  "proxyConfiguration": { "useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"] }
}
```

## Output

One item per person:

```json
{
  "inputName": "Konstantinos Karydis",
  "searchQuery": "Konstantinos Karydis UC Riverside google scholar",
  "foundVia": "lucky",
  "status": "ok",
  "profileUrl": "https://scholar.google.com/citations?user=4Urexvi1sIcC&hl=en",
  "scholarId": "4Urexvi1sIcC",
  "name": "Konstantinos Karydis",
  "affiliation": "University of California, Riverside",
  "interests": ["Robotics"],
  "citedBy": 2142, "citedBySince": 1743,
  "hIndex": 23, "i10Index": 54,
  "publicationsCount": 40,
  "latestPublicationYear": 2026,
  "latestPublicationYearDomains": ["koopman operators", "learning robotics", "robot"],
  "recentPublications": [],
  "topCitedLast3Years": [],
  "publications": [
    { "title": "...", "year": 2025, "citations": 4, "authors": ["..."], "venue": "...", "url": "..." }
  ],
  "scrapedAt": "2026-09-02T15:48:42+00:00"
}
```

`status` is `ok`, `not_found`, `timeout`, or `failed` (with a `message`). Rows always echo
the input fields, so results can be joined back to the request list.

## Proxies

Chrome silently discards credentials embedded in `--proxy-server`, so authenticated
upstreams like Apify Proxy cannot be handed to it directly. [`src/proxy_relay.py`](src/proxy_relay.py)
runs a small localhost relay that accepts Chrome's `CONNECT` requests, injects the
`Proxy-Authorization` header, and pipes the tunnel through to the upstream. Every retry
uses a new proxy session id, so a blocked attempt comes back on a different exit IP.

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Put your input in `storage/key_value_stores/default/INPUT.json` (use
`"proxyConfiguration": {}` to run without a proxy), then:

```bash
.venv/bin/python -m src
```

Results land in `storage/datasets/default/`. Requires Chrome on the host; Selenium Manager
fetches the matching chromedriver automatically.

## Notes

- Keyword extraction is a dependency-free frequency + position ranker over publication
  titles ([`src/keywords.py`](src/keywords.py)). The prototype used KeyBERT, which pulls a
  ~2 GB torch/sentence-transformers stack into the image for a handful of short strings.
- Google is the fragile step, not Scholar. Without a residential proxy, expect CAPTCHAs
  once you go past a handful of lookups per IP.
