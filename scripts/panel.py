#!/usr/bin/env python3
"""panel.py — a LOCAL operator control panel (Streamlit) for JobFetcher (ADR-0033).

    pip install -e '.[panel]'
    streamlit run scripts/panel.py

Three tabs, all against the LIVE Aurora + S3 (nothing hosted, no auth — a personal tool):
  • Browse — every scored job, filter/search/sort (reuses scripts/export.py::read_data).
  • Curate — override a score / record an outcome (reuses scripts/track.py's write paths).
  • Config — edit the search params in a form → validate → push to S3 (reuses push_config).

NEVER bundled in the Lambda (streamlit is the optional `[panel]` extra). The panel reuses the
EXACT validated write paths the CLIs use, so it can never do anything the CLIs couldn't — a bad
config edit is blocked before S3, and every curation goes through the same append-only lineage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import export  # noqa: E402 — read_data(): the flat jobs table + supporting tables
import push_config  # noqa: E402 — validate_config_text + push_config_text + _resolve_bucket
import track  # noqa: E402 — apply_override + the DB-URL resolver, reused verbatim
from jobfetcher.core.models import APPLICATION_STATUSES  # noqa: E402
from jobfetcher.core.ports import RepositoryError  # noqa: E402
from jobfetcher.core.search_spec import (  # noqa: E402
    DatePosted,
    EmploymentType,
    RemoteMode,
    SearchSpec,
)
from jobfetcher.db.engine import make_engine, wait_for_db_resume  # noqa: E402

CONFIG_LOCAL = ROOT / "config" / "search_config.local.yml"
CONFIG_S3_KEY = "config/search_config.yml"

st.set_page_config(page_title="JobFetcher — Control Panel", layout="wide")


@st.cache_resource
def _engine_and_repo():
    """One engine + repo per session (cached). Waits out Aurora's scale-to-0 resume once."""
    from jobfetcher.adapters.repository_postgres import PostgresRepository

    engine = make_engine(track._resolve_db_url())
    with st.spinner("Connecting to Aurora (it may be resuming from scale-to-0)…"):
        wait_for_db_resume(engine)
    return engine, PostgresRepository.from_engine(engine)


@st.cache_data(ttl=60)
def _load_jobs() -> list[dict]:
    """The flat `jobs` table (all scored postings) — reuses export.py's SELECTs. Cached for
    60s; any curation clears the cache so the grid refreshes."""
    engine, _ = _engine_and_repo()
    return export.read_data(engine)["jobs"]


st.title("JobFetcher — Control Panel")
st.caption("A local operator view of the live pipeline data. Reuses the CLI write paths.")
tab_browse, tab_curate, tab_config = st.tabs(["📋 Browse", "✏️ Curate", "⚙️ Config"])

# --------------------------------------------------------------------------- Browse
with tab_browse:
    try:
        jobs = _load_jobs()
    except Exception as exc:  # noqa: BLE001 — a DB/connection error shows in the UI, not a stack trace
        st.error(f"Could not load jobs: {exc}")
        jobs = []
    if jobs:
        q = st.text_input("Search (title / company / location)")
        c1, c2, c3 = st.columns(3)
        cats = sorted({j["fit_category"] for j in jobs if j.get("fit_category")})
        countries = sorted({j["country"] for j in jobs if j.get("country")})
        cat_sel = c1.multiselect("Fit category", cats)
        country_sel = c2.multiselect("Country", countries)
        min_score = c3.slider("Min score", 0, 100, 0)

        def _keep(j: dict) -> bool:
            hay = f"{j.get('normalized_title', '')} {j.get('company', '')} {j.get('location', '')}"
            return (
                (not q or q.lower() in hay.lower())
                and (not cat_sel or j.get("fit_category") in cat_sel)
                and (not country_sel or j.get("country") in country_sel)
                and (j.get("score") or 0) >= min_score
            )

        rows = [j for j in jobs if _keep(j)]
        st.caption(f"{len(rows)} of {len(jobs)} scored jobs")
        st.dataframe(rows, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------- Curate
with tab_curate:
    st.subheader("Override a score")
    st.caption("Records a human score override + a `human-override` lineage event (append-only).")
    ov_pid = st.text_input("Posting id", key="ov_pid")
    ov_score = st.number_input("New score (0-100)", min_value=0, max_value=100, value=60, key="ov")
    if st.button("Apply override", type="primary"):
        if not ov_pid.strip():
            st.warning("Enter a posting id.")
        else:
            try:
                engine, repo = _engine_and_repo()
                r = track.apply_override(engine, repo, posting_id=ov_pid.strip(), score=int(ov_score))
                st.success(
                    f"Override {r['score']} ({r['fit_category']}) → {r['label']} "
                    f"(was {r['previous_score']})"
                )
                _load_jobs.clear()  # refresh the browse grid on the next render
            except RepositoryError as exc:
                st.error(str(exc))

    st.divider()
    st.subheader("Record an outcome")
    out_pid = st.text_input("Posting id", key="out_pid")
    out_status = st.selectbox("Status", list(APPLICATION_STATUSES))
    out_note = st.text_input("Note (optional)", key="out_note")
    if st.button("Record outcome", type="primary"):
        if not out_pid.strip():
            st.warning("Enter a posting id.")
        else:
            try:
                _, repo = _engine_and_repo()
                repo.track_application_event(
                    posting_id=out_pid.strip(), status=out_status, note=out_note or None
                )
                st.success(f"'{out_status}' recorded for {out_pid.strip()}")
                _load_jobs.clear()
            except RepositoryError as exc:
                st.error(str(exc))

# --------------------------------------------------------------------------- Config
with tab_config:
    st.subheader("Search settings")
    st.caption("Edit → validate → push to S3. The next pipeline run uses it (no redeploy).")
    if not CONFIG_LOCAL.exists():
        st.warning(
            f"No {CONFIG_LOCAL.name} yet — copy `config/search_config.sample.yml` to it first."
        )
    else:
        # Start from the FULL current spec so the required fields not shown here (source /
        # secret_name / aws_region / language / states / budget / age bounds) are preserved —
        # the all-required SearchSpec contract holds on save.
        d = SearchSpec.from_yaml(str(CONFIG_LOCAL)).model_dump(mode="json")
        tgt = d["targeting"]
        c1, c2, c3 = st.columns(3)
        threshold = c1.number_input("Threshold", 0, 100, int(d["threshold"]))
        hard_floor = c2.number_input("Hard floor", 0, 100, int(d["hard_floor"]))
        near_miss = c3.number_input("Near-miss band", 0, 100, int(d["near_miss_band"]))
        titles = st.text_area("Job titles (one per line)", "\n".join(tgt["job_titles"]))
        countries = st.text_input("Countries (comma-separated ISO2)", ", ".join(tgt["countries"]))
        cities = st.text_input("Cities (comma-separated, optional)", ", ".join(tgt["cities"]))
        cc1, cc2 = st.columns(2)
        dp_opts = [e.value for e in DatePosted]
        rm_opts = [e.value for e in RemoteMode]
        date_posted = cc1.selectbox("Date posted", dp_opts, index=dp_opts.index(d["date_posted"]))
        remote = cc2.selectbox("Remote", rm_opts, index=rm_opts.index(d["remote"]))
        emp = st.multiselect(
            "Employment types", [e.value for e in EmploymentType], d["employment_types"]
        )

        if st.button("Validate + push to S3", type="primary"):
            new = dict(d)
            new["threshold"] = int(threshold)
            new["hard_floor"] = int(hard_floor)
            new["near_miss_band"] = int(near_miss)
            new["targeting"] = {
                **tgt,
                "job_titles": [t.strip() for t in titles.splitlines() if t.strip()],
                "countries": [c.strip().lower() for c in countries.split(",") if c.strip()],
                "cities": [c.strip() for c in cities.split(",") if c.strip()],
            }
            new["date_posted"] = date_posted
            new["remote"] = remote
            new["employment_types"] = emp
            text = yaml.safe_dump(new, sort_keys=False, allow_unicode=True)
            # THE gate: validate the full spec before anything is written; a bad edit blocks here.
            try:
                push_config.validate_config_text(text, SearchSpec)
            except Exception as exc:  # noqa: BLE001 — a ValidationError shows in the UI, not saved
                st.error(f"Invalid config — nothing saved: {exc}")
            else:
                try:
                    CONFIG_LOCAL.write_text(text, encoding="utf-8")  # local file stays the source
                    bucket = push_config._resolve_bucket(None)  # env / terraform output
                    push_config.push_config_text(bucket=bucket, key=CONFIG_S3_KEY, text=text)
                    st.success(
                        f"Saved locally + pushed to s3://{bucket}/{CONFIG_S3_KEY} — the next "
                        "run uses it (no redeploy)."
                    )
                except SystemExit as exc:  # _resolve_bucket exits if no bucket is configured
                    st.error(f"Saved locally, but no S3 bucket to push to: {exc}")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Saved locally, but the S3 push failed: {exc}")
