#!/usr/bin/env python3
"""JSearch free-tier coverage probe — JobFetcher build-plan Step 0.

Runs the decided query matrix (ADR-0010 addendum / config/search_config.sample.yml)
against JSearch Basic (free) and reports the 5 probe metrics:
  1) coverage        - results per (title x country)
  2) JD completeness - share of postings with a full job_description (+ median length)
  3) query precision - eyeball via the dumped raw JSON
  4) dedup reality   - apply_options counts (JSearch pre-merge) + duplicate job_id check
  5) depth           - pages pulled per query / whether more remained

Deps: stdlib + boto3 (only to read the secret; the env-var fallback needs no boto3).
The API key is read from AWS Secrets Manager (never printed or committed). Raw responses
go to ./probe_output/ (gitignored). A hard request cap protects the 200/mo free quota.

Key resolution (best practice first):
  1) AWS Secrets Manager secret `jobfetcher/jsearch` (SecretString JSON {"api_key": "..."}),
     read with the jobfetcher-dev credentials in us-east-1.   <-- preferred
  2) env var JSEARCH_API_KEY (or RAPIDAPI_KEY)                <-- fallback for quick tests

Store the key once:
  aws secretsmanager create-secret --name jobfetcher/jsearch \
    --secret-string '{"api_key":"<YOUR_KEY>"}' --region us-east-1

Usage:
  python scripts/jsearch_probe.py     # --dry-run prints the plan without calling AWS/JSearch
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# --- decided matrix (mirrors config/search_config.sample.yml) ---------------
TITLES = ["Data Engineer", "Data Platform Engineer", "Data Architect"]
COUNTRIES = ["sa", "ae", "qa", "kw", "bh", "om"]
DATE_POSTED = "month"          # 30-day backfill window
LANGUAGE = "en"               # force English metadata (else Arabic for GCC);
                              # also populates job_city/job_country + the UTC timestamp
REMOTE_ONLY = False
MAX_PAGES_PER_QUERY = 5        # ~10 results/page, ~1 request/page
REQUEST_BUDGET = 70           # hard ceiling for one sweep (free Basic = 200/mo)

HOST = "jsearch.p.rapidapi.com"
OUT_DIR = Path("probe_output")
SECRET_NAME = "jobfetcher/jsearch"   # AWS Secrets Manager — value: {"api_key": "..."}
AWS_REGION = "us-east-1"


def get_key() -> str:
    """JSearch API key from AWS Secrets Manager (best practice); env var as fallback."""
    try:
        import boto3  # imported lazily so --dry-run needs no AWS SDK
        client = boto3.client("secretsmanager", region_name=AWS_REGION)
        raw = client.get_secret_value(SecretId=SECRET_NAME).get("SecretString") or ""
        try:
            return json.loads(raw)["api_key"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return raw  # tolerate a plain-string secret
    except Exception as exc:  # boto3 missing / secret absent / AWS unreachable
        env = os.environ.get("JSEARCH_API_KEY") or os.environ.get("RAPIDAPI_KEY")
        if env:
            print(f"[secrets] Secrets Manager unavailable ({type(exc).__name__}); "
                  "using env-var fallback.")
            return env
        sys.exit(
            "ERROR: could not read the JSearch key.\n"
            f"  Preferred - store it once in AWS Secrets Manager, then re-run:\n"
            f"    aws secretsmanager create-secret --name {SECRET_NAME} \\\n"
            f"      --secret-string '{{\"api_key\":\"<YOUR_KEY>\"}}' --region {AWS_REGION}\n"
            "  Fallback - set JSEARCH_API_KEY in your environment.\n"
            f"  (underlying error: {exc})"
        )


def fetch(query: str, country: str, page: int, key: str) -> dict:
    params = urllib.parse.urlencode({
        "query": query,
        "country": country,
        "page": str(page),
        "num_pages": "1",
        "date_posted": DATE_POSTED,
        "language": LANGUAGE,
        "remote_jobs_only": "true" if REMOTE_ONLY else "false",
    })
    req = urllib.request.Request(
        f"https://{HOST}/search?{params}",
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": HOST},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    dry = "--dry-run" in sys.argv
    key = "DRY" if dry else get_key()
    OUT_DIR.mkdir(exist_ok=True)

    made = 0
    rows: list[dict] = []
    seen_ids: dict[str, int] = {}

    for title in TITLES:
        for country in COUNTRIES:
            for page in range(1, MAX_PAGES_PER_QUERY + 1):
                if made >= REQUEST_BUDGET:
                    print(f"\n[budget] request cap reached ({REQUEST_BUDGET}); stopping.")
                    summarize(rows, seen_ids, made)
                    return
                if dry:
                    print(f"[dry-run] GET /search query='{title}' country={country} "
                          f"page={page} date_posted={DATE_POSTED}")
                    made += 1
                    continue
                try:
                    data = fetch(title, country, page, key)
                except urllib.error.HTTPError as exc:
                    print(f"[http {exc.code}] {title}/{country} p{page}: {exc.reason}")
                    if exc.code in (401, 403, 429):   # auth / quota / rate -> stop politely
                        summarize(rows, seen_ids, made)
                        return
                    break
                except urllib.error.URLError as exc:
                    print(f"[network] {title}/{country} p{page}: {exc.reason}")
                    break

                made += 1
                jobs = data.get("data") or []
                fname = f"{title.replace(' ', '_')}__{country}__p{page}.json"
                (OUT_DIR / fname).write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

                descs = [j.get("job_description") or "" for j in jobs]
                full_jd = sum(1 for d in descs if len(d) > 200)
                med_len = int(statistics.median([len(d) for d in descs])) if descs else 0
                apply_opts = [len(j.get("apply_options") or []) for j in jobs]
                avg_apply = round(sum(apply_opts) / len(apply_opts), 2) if apply_opts else 0
                for j in jobs:
                    jid = j.get("job_id") or ""
                    seen_ids[jid] = seen_ids.get(jid, 0) + 1

                rows.append({"results": len(jobs), "full_jd": full_jd})
                print(f"{title:22} {country}  p{page}: {len(jobs):2} results | "
                      f"{full_jd:2} full-JD | med_len {med_len:5} | avg apply_options {avg_apply}")

                if len(jobs) < 10:   # fewer than a full page -> no more pages
                    break
                time.sleep(0.5)      # be polite to the API

    summarize(rows, seen_ids, made)


def summarize(rows: list[dict], seen_ids: dict[str, int], made: int) -> None:
    total = sum(r["results"] for r in rows)
    full_jd = sum(r["full_jd"] for r in rows)
    dup_ids = sum(1 for c in seen_ids.values() if c > 1)
    print("\n-------- PROBE SUMMARY --------")
    print(f"requests made         : {made}  (cap {REQUEST_BUDGET}; free Basic = 200/mo)")
    print(f"queries run           : {len(rows)}")
    print(f"total results         : {total}")
    print(f"unique job_id         : {len(seen_ids)}")
    print(f"job_id seen > 1 (dups): {dup_ids}   (low => exact-id dedup is enough for v0)")
    if total:
        print(f"full-JD rate          : {full_jd}/{total} = {round(100 * full_jd / total)}%")
    print("raw dumped to         : ./probe_output/  (gitignored - eyeball precision by hand)")
    print("-------------------------------")


if __name__ == "__main__":
    main()
