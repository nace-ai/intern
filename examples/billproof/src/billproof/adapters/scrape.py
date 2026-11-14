"""Public payer-policy page fetch. Stub only (PLAN decision 11).

``fetch_policy_page`` raises ``NotImplementedError`` unless ``BRIGHTDATA_API_TOKEN``
or ``APIFY_TOKEN`` is set. A set token still has no client in v1.
"""

from __future__ import annotations

import os


def fetch_policy_page(url: str) -> str:
    """Return the text of a public policy page.

    Bright Data is preferred when ``BRIGHTDATA_API_TOKEN`` is set, otherwise
    Apify when ``APIFY_TOKEN`` is set. Neither client is wired in v1.
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("url must be an http(s) policy page")
    provider = _provider()
    if provider is None:
        raise NotImplementedError(
            "policy-page scraping is off until BRIGHTDATA_API_TOKEN or APIFY_TOKEN is set"
        )
    # Shape of the later call: POST the page URL to the provider and return body text.
    # Bright Data: POST https://api.brightdata.com/request  {"zone", "url", "format": "raw"}
    # Apify: POST https://api.apify.com/v2/acts/apify~website-content-crawler/run-sync-get-dataset-items
    raise RuntimeError(f"{provider} policy-page fetch is not wired in v1")


def _provider() -> str | None:
    if os.getenv("BRIGHTDATA_API_TOKEN", "").strip():
        return "brightdata"
    if os.getenv("APIFY_TOKEN", "").strip():
        return "apify"
    return None
