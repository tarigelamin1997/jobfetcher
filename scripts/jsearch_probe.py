#!/usr/bin/env python3
"""JSearch free-tier coverage probe — JobFetcher build-plan Step 0.

Loads the validated user SearchSpec (config/search_config.local.yml if present,
else the committed sample), builds the query matrix (job_titles x countries) FROM
the spec, runs it against JSearch Basic (free), and reports the 5 probe metrics:
  1) coverage  2) JD completeness  3) query precision  4) dedup reality  5) depth

Deps: pydantic + pyyaml (the spec) + boto3 (the secret). The API key is read from
AWS Secrets Manager (never printed or committed). Raw responses -> ./probe_output/
(gitignored). A hard request cap (spec.budget.request_budget_per_run) protects the
200/mo free quota.

Usage:
  python scripts/jsearch_probe.py                 # uses .local.yml if present, else the sample
  python scripts/jsearch_probe.py --dry-run       # print the plan; no AWS/JSearch calls
  python scripts/jsearch_probe.py path/to/spec.yml
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jobfetcher.core.search_spec import RemoteMode, SearchSpec  # noqa: E402

HOST = "jsearch.p.rapidapi.com"
OUT_DIR = Path("probe_output")


def load_spec(argv: list[str]) -> SearchSpec:
    """Explicit path arg wins; else the local override; else the committed sample."""
    paths = [a for a in argv[1:] if not a.startswith("--")]
    if paths:
        return SearchSpec.from_yaml(paths[0])
    local = Path("config/search_config.local.yml")
    return SearchSpec.from_yaml(local if local.exists() else "config/search_config.sample.yml")


def get_key(spec: SearchSpec) -> str:
    """JSearch API key from AWS Secrets Manager (best practice); env var as fallback."""
    import os

    try:
        import boto3  # imported lazily so --dry-run needs no AWS SDK
        client = boto3.client("secretsmanager", region_name=spec.aws_region)
        raw = client.get_secret_value(SecretId=spec.secret_name).get("SecretString") or ""
        try:
            return json.loads(raw)["api_key"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return raw
    except Exception as exc:  # boto3 missing / secret absent / AWS unreachable
        env = os.environ.get("JSEARCH_API_KEY") or os.environ.get("RAPIDAPI_KEY")
        if env:
            print(f"[secrets] Secrets Manager unavailable ({type(exc).__name__}); env-var fallback.")
            return env
        sys.exit(
            "ERROR: could not read the JSearch key.\n"
            f"  Store it once:  aws secretsmanager create-secret --name {spec.secret_name} "
            f"--secret-string '{{\"api_key\":\"<KEY>\"}}' --region {spec.aws_region}\n"
            "  Fallback:       set JSEARCH_API_KEY in your environment.\n"
            f"  (underlying error: {exc})"
        )


def fetch(query: str, country: str, page: int, spec: SearchSpec, key: str) -> dict:
    params = urllib.parse.urlencode({
        "query": query,
        "country": country,
        "page": str(page),
        "num_pages": "1",
        "date_posted": spec.date_posted.value,
        "language": spec.language,
        "remote_jobs_only": "true" if spec.remote is RemoteMode.only else "false",
    })
    req = urllib.request.Request(
        f"https://{HOST}/search?{params}",
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": HOST},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    spec = load_spec(sys.argv)
    dry = "--dry-run" in sys.argv
    titles = spec.targeting.job_titles
    countries = spec.targeting.countries
    cap = spec.budget.request_budget_per_run
    max_pages = spec.budget.max_pages_per_query

    print(f"spec: {len(titles)} titles x {len(countries)} countries = {len(titles) * len(countries)} "
          f"base queries | date_posted={spec.date_posted.value} language={spec.language} "
          f"remote={spec.remote.value} | budget <= {cap}")
    if spec.targeting.cities or spec.targeting.states:
        print(f"  gold-filter targets (applied later, not in this probe): "
              f"cities={spec.targeting.cities} states={spec.targeting.states}")

    key = "DRY" if dry else get_key(spec)
    OUT_DIR.mkdir(exist_ok=True)
    made = 0
    rows: list[dict] = []
    seen_ids: dict[str, int] = {}

    for title in titles:
        for country in countries:
            for page in range(1, max_pages + 1):
                if made >= cap:
                    print(f"\n[budget] request cap reached ({cap}); stopping.")
                    summarize(rows, seen_ids, made, cap)
                    return
                if dry:
                    print(f"[dry-run] GET /search query='{title}' country={country} page={page} "
                          f"date_posted={spec.date_posted.value} language={spec.language}")
                    made += 1
                    continue
                try:
                    data = fetch(title, country, page, spec, key)
                except urllib.error.HTTPError as exc:
                    print(f"[http {exc.code}] {title}/{country} p{page}: {exc.reason}")
                    if exc.code in (401, 403, 429):   # auth / quota / rate -> stop politely
                        summarize(rows, seen_ids, made, cap)
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
                print(f"{title:22} {country}  p{page}: {len(jobs):2} results | {full_jd:2} full-JD | "
                      f"med_len {med_len:5} | avg apply_options {avg_apply}")

                if len(jobs) < 10:   # fewer than a full page -> no more pages
                    break
                time.sleep(0.5)      # be polite to the API

    summarize(rows, seen_ids, made, cap)


def summarize(rows: list[dict], seen_ids: dict[str, int], made: int, cap: int) -> None:
    total = sum(r["results"] for r in rows)
    full_jd = sum(r["full_jd"] for r in rows)
    dup_ids = sum(1 for c in seen_ids.values() if c > 1)
    print("\n-------- PROBE SUMMARY --------")
    print(f"requests made         : {made}  (cap {cap}; free Basic = 200/mo)")
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
