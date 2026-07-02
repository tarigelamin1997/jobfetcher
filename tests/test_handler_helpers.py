"""Unit tests for the Step-7 handler's pure helpers — DB-URL resolution (local vs Data API),
config-path resolution, and run_id/run_date derivation. No DB, no AWS, no network: the
env-driven branches are exercised with a plain dict so the wiring is provable without an
integration run."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from jobfetcher.handlers.pipeline import (
    _DEFAULT_PROFILE_PATH,
    _DEFAULT_SEARCH_CONFIG_PATH,
    resolve_db_url,
    resolve_deadline,
    resolve_max_workers,
    resolve_profile_path,
    resolve_run_date,
    resolve_run_id,
    resolve_search_config_path,
)


# --------------------------------------------------------------------------- DB URL
def test_resolve_db_url_prefers_explicit_local_url():
    env = {
        "JOBFETCHER_DB_URL": "postgresql://u:p@localhost:5433/jobfetcher",
        # ARNs present but must be ignored when the explicit URL is set
        "DB_CLUSTER_ARN": "arn:aws:rds:...:cluster/x",
        "DB_SECRET_ARN": "arn:aws:secretsmanager:...:secret/y",
        "DB_NAME": "jobfetcher",
    }
    assert resolve_db_url(env) == "postgresql://u:p@localhost:5433/jobfetcher"


def test_resolve_db_url_builds_data_api_url_from_arns():
    env = {
        "DB_CLUSTER_ARN": "arn:aws:rds:us-east-1:1:cluster/jf",
        "DB_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret/jf",
        "DB_NAME": "jobfetcher",
    }
    url = resolve_db_url(env)
    assert url.startswith("postgresql+auroradataapi://:@/jobfetcher?")
    # the ARNs are carried as url-encoded query params; the cluster param is the dialect's
    # `aurora_cluster_arn` connect kwarg (verified live in the Step-10 deploy)
    assert "aurora_cluster_arn=arn%3Aaws%3Ards%3Aus-east-1%3A1%3Acluster%2Fjf" in url
    assert "secret_arn=arn%3Aaws%3Asecretsmanager%3Aus-east-1%3A1%3Asecret%2Fjf" in url


def test_resolve_db_url_blank_explicit_falls_through_to_arns():
    env = {
        "JOBFETCHER_DB_URL": "   ",
        "DB_CLUSTER_ARN": "arn:c",
        "DB_SECRET_ARN": "arn:s",
        "DB_NAME": "db",
    }
    assert resolve_db_url(env).startswith("postgresql+auroradataapi://:@/db?")


def test_resolve_db_url_raises_when_nothing_configured():
    with pytest.raises(ValueError, match="no DB connection configured"):
        resolve_db_url({})


def test_resolve_db_url_raises_on_partial_data_api_config():
    # cluster + secret present but no DB_NAME → a clear misconfig, not a silent default
    with pytest.raises(ValueError):
        resolve_db_url({"DB_CLUSTER_ARN": "arn:c", "DB_SECRET_ARN": "arn:s"})


# --------------------------------------------------------------------------- config paths
def test_resolve_config_paths_default_when_unset():
    assert resolve_search_config_path({}) == _DEFAULT_SEARCH_CONFIG_PATH
    assert resolve_profile_path({}) == _DEFAULT_PROFILE_PATH


def test_resolve_config_paths_from_env():
    env = {"SEARCH_CONFIG_PATH": "/pkg/search.yml", "PROFILE_PATH": "/pkg/profile.yml"}
    assert resolve_search_config_path(env) == "/pkg/search.yml"
    assert resolve_profile_path(env) == "/pkg/profile.yml"


# --------------------------------------------------------------------------- run_id / run_date
def test_resolve_run_id_from_event():
    assert resolve_run_id({"run_id": "abc123"}) == "abc123"


def test_resolve_run_id_generates_short_uuid_when_absent():
    rid = resolve_run_id({})
    assert isinstance(rid, str) and len(rid) == 8

    # the None branch also generates a fresh 8-char hex id, distinct from the {} one
    none_rid = resolve_run_id(None)
    assert isinstance(none_rid, str) and len(none_rid) == 8
    int(none_rid, 16)  # raises if it isn't lowercase hex — proves the uuid shape
    assert none_rid != rid  # a freshly generated id, not the previous one


def test_resolve_run_id_ignores_blank_event_value():
    rid = resolve_run_id({"run_id": "   "})
    assert len(rid) == 8  # blank → generated, not the blank string


def test_resolve_run_date_from_event_iso():
    assert resolve_run_date({"run_date": "2026-06-28"}) == date(2026, 6, 28)


def test_resolve_run_date_defaults_to_utc_today():
    assert resolve_run_date({}) == datetime.now(timezone.utc).date()
    assert resolve_run_date(None) == datetime.now(timezone.utc).date()


def test_resolve_run_date_raises_on_malformed_override():
    with pytest.raises(ValueError):
        resolve_run_date({"run_date": "not-a-date"})


# --------------------------------------------------------------------------- H-2 knobs
def test_resolve_max_workers_default_and_override():
    assert resolve_max_workers({}) == 8
    assert resolve_max_workers({"PIPELINE_MAX_WORKERS": "4"}) == 4
    assert resolve_max_workers({"PIPELINE_MAX_WORKERS": "  16 "}) == 16


def test_resolve_max_workers_rejects_junk_and_zero():
    # negative: a misconfigured knob must fail loudly, never silently fall back
    with pytest.raises(ValueError):
        resolve_max_workers({"PIPELINE_MAX_WORKERS": "many"})
    with pytest.raises(ValueError, match="must be >= 1"):
        resolve_max_workers({"PIPELINE_MAX_WORKERS": "0"})


def test_resolve_deadline_from_lambda_context():
    class _Ctx:
        def get_remaining_time_in_millis(self):
            return 900_000  # 15 min left

    deadline = resolve_deadline(_Ctx())
    assert deadline is not None
    assert not deadline.expired  # 900s - 60s margin is comfortably in the future


def test_resolve_deadline_expired_when_context_nearly_out_of_time():
    # negative: less remaining time than the safety margin → the deadline is already expired
    class _Ctx:
        def get_remaining_time_in_millis(self):
            return 5_000  # 5s left < the 60s margin

    deadline = resolve_deadline(_Ctx())
    assert deadline is not None and deadline.expired


def test_resolve_deadline_none_without_real_context():
    # local runs / tests pass None (or an object without the Lambda method) → no time budget
    assert resolve_deadline(None) is None
    assert resolve_deadline(object()) is None
