"""`JSearchSourceAdapter` — the v0 `SourceAdapter` (ADR-0010), productionized from the
Step-0 coverage probe (`scripts/jsearch_probe.py`).

It fans the spec's `job_titles × countries × pages` matrix out against JSearch `/search`
under the spec's request/page budget, yielding each raw `job` JSON object untouched (the
caller lands it to bronze). Quota/rate-limit (429) and network errors stop the sweep
*gracefully* — never crash the run. Auth failures (401/403) and a missing/unresolvable API
key raise `SourceError` (a broken credential must fail loudly, never as a silent zero-count).

Also holds the deterministic raw→`(jd_text, PostingMetadata)` mapping (moved here from
`tests/helpers.py` per the Step-4 plan): field extraction only, no LLM.

Stdlib `urllib` for HTTP (no requests dependency); boto3 only for the secret (lazy).
"""
from __future__ import annotations

import json
import logging
import socket
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
_AUTH_FAIL_CODES = (401, 403)  # bad/missing key or subscription failure → HARD-FAIL loudly
_RATE_LIMIT_CODE = 429  # rate/quota → stop the sweep gracefully (land what we got)
_POLITE_SLEEP_S = 0.5

# Transient side-channel key the adapter attaches to each yielded job to carry the
# authoritative *query* country (C3). Popped off before the raw payload is persisted to
# bronze, so the stored raw is never mutated. Underscore-prefixed → won't collide with a
# real JSearch field.
QUERY_COUNTRY_KEY = "_jobfetcher_query_country"

log = logging.getLogger(__name__)


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
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            # valid JSON object → it MUST carry the key; never return the whole blob
            key = parsed.get("api_key") or parsed.get("apiKey")
            if isinstance(key, str) and key.strip():
                return key.strip()
            raise SourceError(
                f"the JSearch secret '{spec.secret_name}' is JSON but has no "
                "'api_key'/'apiKey' field"
            )
        if raw.strip():  # a bare-string secret → the key itself
            return raw.strip()
        raise SourceError(f"the JSearch secret '{spec.secret_name}' is empty")
    except SourceError:
        raise  # a clear misconfig from the JSON branch — don't fall through to env
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
    query_params = {
        "query": query,
        "country": country,
        "page": str(page),
        "num_pages": "1",
        "date_posted": spec.date_posted.value,
        "language": spec.language,
        "remote_jobs_only": "true" if spec.remote is RemoteMode.only else "false",
    }
    # Employment-type filter (only when set) — the JSearch `/search` param is a comma-separated
    # list of FULLTIME|PARTTIME|CONTRACTOR|INTERN. Empty spec = no filter (param omitted).
    if spec.employment_types:
        query_params["employment_types"] = ",".join(e.value for e in spec.employment_types)
    params = urllib.parse.urlencode(query_params)
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
    raw: dict[str, Any], *, language: str = "en", query_country: str | None = None
) -> tuple[str, PostingMetadata]:
    """Map a raw JSearch job object → `(jd_text, PostingMetadata)`. Deterministic field
    extraction only — the LLM `Dissector` does the free-text part.

    The `country` *query* param is the authoritative geo scope (C3): when `query_country` is
    threaded in (the country actually queried) it wins; the per-record `job_country` is only
    the fallback (under the enforced `language=en` it IS populated, but the query scope is the
    contract). The stored raw payload is never mutated — only the derived silver `country`."""
    title = raw.get("job_title") or "Untitled role"
    country = query_country if query_country else raw.get("job_country")
    meta = PostingMetadata(
        raw_title=title,
        language=language,
        location=raw.get("job_location"),
        city=raw.get("job_city"),
        country=country,
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
                    made += 1  # count EVERY billed request attempt, success or error
                    try:
                        data = _fetch_page(title, country, page, spec, key)
                    except urllib.error.HTTPError as exc:
                        if exc.code in _AUTH_FAIL_CODES:
                            # C5: 401 = bad/missing key, 403 = auth/subscription failure — a
                            # broken credential must FAIL LOUDLY, else a rotated key turns into
                            # a silent zero-count "success". Distinct from a quota stop.
                            raise SourceError(
                                f"JSearch authentication failed (HTTP {exc.code}) — the API "
                                "key is missing, wrong, revoked, or the subscription lapsed"
                            ) from exc
                        if exc.code == _RATE_LIMIT_CODE:  # 429 quota/rate → stop politely
                            return
                        log.warning(
                            "JSearch HTTP %s for '%s'/%s p%s — skipping query",
                            exc.code, title, country, page,
                        )
                        break  # other HTTP error → skip the rest of this query's pages
                    except (
                        urllib.error.URLError,  # network blip
                        json.JSONDecodeError,   # non-JSON body (e.g. gateway HTML page)
                        TimeoutError,           # bare read timeout (not a URLError on 3.11)
                        socket.timeout,         # older-style socket timeout
                    ) as exc:
                        log.warning(
                            "JSearch transient error for '%s'/%s p%s (%s) — skipping query",
                            title, country, page, type(exc).__name__,
                        )
                        break  # transient → skip the rest of this query's pages

                    # B1: tolerate a malformed `data` shape — land what's valid, never crash
                    raw_data = data.get("data")
                    if not isinstance(raw_data, list):
                        if raw_data is not None:
                            log.warning(
                                "JSearch 'data' is %s, not a list for '%s'/%s p%s — skipping",
                                type(raw_data).__name__, title, country, page,
                            )
                        jobs: list[dict[str, Any]] = []
                    else:
                        jobs = []
                        for item in raw_data:
                            if isinstance(item, dict):
                                # C3: carry the authoritative *query* country on a shallow copy
                                # (the original source dict is never mutated; the bronze landing
                                # pops this key before persisting the raw payload).
                                jobs.append({**item, QUERY_COUNTRY_KEY: country})
                            else:
                                log.warning(
                                    "JSearch job item is %s, not a dict — skipping one item",
                                    type(item).__name__,
                                )
                        yield from jobs

                    # short page (by the raw page size) → no more pages for this query
                    page_len = len(raw_data) if isinstance(raw_data, list) else 0
                    if page_len < _FULL_PAGE:
                        break
                    time.sleep(_POLITE_SLEEP_S)  # be polite to the API
