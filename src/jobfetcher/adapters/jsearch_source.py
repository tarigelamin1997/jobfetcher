"""`JSearchSourceAdapter` — the v0 `SourceAdapter` (ADR-0010), productionized from the
Step-0 coverage probe (`scripts/jsearch_probe.py`).

It fans the spec's `job_titles × countries × pages` matrix out against JSearch `/search`
under the spec's request/page budget, yielding each raw `job` JSON object untouched (the
caller lands it to bronze). Quota/rate-limit/auth (401/403/429) and network errors stop the
sweep *gracefully* — never crash the run; only a missing API key raises `SourceError`.

Also holds the deterministic raw→`(jd_text, PostingMetadata)` mapping (moved here from
`tests/helpers.py` per the Step-4 plan): field extraction only, no LLM.

Stdlib `urllib` for HTTP (no requests dependency); boto3 only for the secret (lazy).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from ..core.models import PostingMetadata
from ..core.ports import SourceError
from ..core.search_spec import RemoteMode, SearchSpec

if TYPE_CHECKING:
    from collections.abc import Iterator

HOST = "jsearch.p.rapidapi.com"
_FULL_PAGE = 10  # JSearch returns ~10/page; fewer means the last page → stop paging
_STOP_CODES = (401, 403, 429)  # auth / quota / rate-limit → stop the sweep politely
_POLITE_SLEEP_S = 0.5


# --------------------------------------------------------------------------- key resolution
def get_key(spec: SearchSpec) -> str:
    """JSearch API key from AWS Secrets Manager (`spec.secret_name`), env var as fallback.

    Accepts a raw key or a JSON blob `{"api_key": "..."}`. Raises `SourceError` if no key
    can be resolved (a genuine misconfiguration — distinct from a transient quota stop). The
    key is never logged.
    """
    import os

    try:
        import boto3  # lazy: the env-var path needs no AWS SDK
        client = boto3.client("secretsmanager", region_name=spec.aws_region)
        raw = client.get_secret_value(SecretId=spec.secret_name).get("SecretString") or ""
        try:
            return json.loads(raw)["api_key"]
        except (json.JSONDecodeError, KeyError, TypeError):
            if raw.strip():
                return raw.strip()
            raise
    except Exception:  # boto3 missing / secret absent / AWS unreachable / empty secret
        env = os.environ.get("JSEARCH_API_KEY") or os.environ.get("RAPIDAPI_KEY")
        if env and env.strip():
            return env.strip()
        raise SourceError(
            "could not resolve the JSearch API key "
            f"(Secrets Manager '{spec.secret_name}' or env $JSEARCH_API_KEY/$RAPIDAPI_KEY)"
        ) from None


# --------------------------------------------------------------------------- HTTP fetch
def _fetch_page(query: str, country: str, page: int, spec: SearchSpec, key: str) -> dict:
    """One JSearch `/search` call → parsed JSON. Productionized from the probe's `fetch`."""
    params = urllib.parse.urlencode(
        {
            "query": query,
            "country": country,
            "page": str(page),
            "num_pages": "1",
            "date_posted": spec.date_posted.value,
            "language": spec.language,
            "remote_jobs_only": "true" if spec.remote is RemoteMode.only else "false",
        }
    )
    req = urllib.request.Request(
        f"https://{HOST}/search?{params}",
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": HOST},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- raw → metadata
def _seniority_from_title(title: str) -> str | None:
    """Deterministic seniority from the title (the LLM never re-guesses it). Most-specific
    first so 'Senior Staff Engineer' resolves to 'staff' before 'senior' if both appear —
    matches the probe/helpers behavior."""
    t = title.lower()
    for kw in ("principal", "staff", "lead", "senior", "junior", "mid", "entry"):
        if kw in t:
            return kw
    return None


def jd_and_metadata_from_jsearch(
    raw: dict[str, Any], *, language: str = "en"
) -> tuple[str, PostingMetadata]:
    """Map a raw JSearch job object → `(jd_text, PostingMetadata)`. Deterministic field
    extraction only — the LLM `Dissector` does the free-text part. The `country` *query*
    param is the authoritative geo scope; the per-record `job_country` is unreliable, but we
    keep it as the source-stated value (analytics can prefer the query scope)."""
    title = raw.get("job_title") or "Untitled role"
    meta = PostingMetadata(
        raw_title=title,
        language=language,
        location=raw.get("job_location"),
        city=raw.get("job_city"),
        country=raw.get("job_country"),
        employment_type=raw.get("job_employment_type"),
        seniority=_seniority_from_title(title),
    )
    return raw.get("job_description") or "", meta


# --------------------------------------------------------------------------- the adapter
class JSearchSourceAdapter:
    """`SourceAdapter` over JSearch. Construct with an optional pre-resolved `api_key`
    (tests pass one to skip AWS); otherwise the key is resolved lazily on first fetch."""

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _key(self, spec: SearchSpec) -> str:
        if not self._api_key:
            self._api_key = get_key(spec)
        return self._api_key

    def fetch(self, spec: SearchSpec, *, run_id: str) -> "Iterator[dict[str, Any]]":  # noqa: ARG002
        """Yield each raw `job` across the title×country×page matrix, budget-capped. `run_id`
        is accepted for the port (correlation) but JSearch needs no per-call id."""
        key = self._key(spec)  # raises SourceError if unresolvable — a real misconfig
        cap = spec.budget.request_budget_per_run
        max_pages = spec.budget.max_pages_per_query
        made = 0

        for title in spec.targeting.job_titles:
            for country in spec.targeting.countries:
                for page in range(1, max_pages + 1):
                    if made >= cap:
                        return  # request budget exhausted — stop the whole sweep
                    try:
                        data = _fetch_page(title, country, page, spec, key)
                    except urllib.error.HTTPError as exc:
                        if exc.code in _STOP_CODES:  # auth / quota / rate → stop politely
                            return
                        break  # other HTTP error → skip the rest of this query's pages
                    except urllib.error.URLError:
                        break  # network blip → skip the rest of this query's pages
                    made += 1

                    jobs = data.get("data") or []
                    yield from jobs

                    if len(jobs) < _FULL_PAGE:  # short page → no more pages for this query
                        break
                    time.sleep(_POLITE_SLEEP_S)  # be polite to the API
