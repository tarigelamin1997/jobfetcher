"""OPTIONAL live Step-4 smoke: 1 real JSearch query/page → bronze (moto S3 + real Postgres) →
a real DeepSeek dissect → a `posting` row. The one end-to-end test that touches both live APIs.

SKIPS unless all three are present: a JSearch key (env or Secrets Manager), a DeepSeek key
(`$DEEPSEEK_API_KEY`), and a reachable Postgres (`$JOBFETCHER_DB_URL`). Honors the request
budget (1 query, 1 page) so it costs ~1 JSearch request + a handful of dissections.
"""
from __future__ import annotations

import os

import pytest

from jobfetcher.core.search_spec import SearchSpec

pytestmark = pytest.mark.integration

moto = pytest.importorskip("moto", reason="moto not installed (dev extra)")
from moto import mock_aws  # noqa: E402

BUCKET = "jobfetcher-live-data"


def _have_jsearch_key() -> bool:
    if os.environ.get("JSEARCH_API_KEY") or os.environ.get("RAPIDAPI_KEY"):
        return True
    try:
        from jobfetcher.adapters.jsearch_source import get_key
        from jobfetcher.core.ports import SourceError

        try:
            get_key(_spec())
            return True
        except SourceError:
            return False
    except Exception:
        return False


def _spec() -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "source": "jsearch", "secret_name": "jobfetcher/jsearch", "aws_region": "us-east-1",
            "targeting": {"job_titles": ["data engineer"], "countries": ["sa"],
                          "cities": [], "states": []},
            "date_posted": "month", "language": "en", "employment_types": [],
            "remote": "off", "threshold": 60,
            "budget": {"max_pages_per_query": 1, "request_budget_per_run": 1},
        }
    )


def test_live_one_query_to_posting():
    db_url = os.environ.get("JOBFETCHER_DB_URL")
    if not (db_url and db_url.strip()):
        pytest.skip("no $JOBFETCHER_DB_URL — live ingest needs a real Postgres")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("no $DEEPSEEK_API_KEY — live dissect needs a DeepSeek key")
    if not _have_jsearch_key():
        pytest.skip("no JSearch key (env or Secrets Manager)")

    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from jobfetcher.adapters.jsearch_source import JSearchSourceAdapter
    from jobfetcher.adapters.llm_openai import OpenAICompatLlmClient
    from jobfetcher.adapters.repository_postgres import PostgresRepository
    from jobfetcher.adapters.s3_raw import S3RawStore
    from jobfetcher.core.dissector import Dissector
    from jobfetcher.core.ingest import ingest
    from jobfetcher.db.engine import make_engine

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    repo = PostgresRepository.from_engine(make_engine(db_url))
    dissector = Dissector(OpenAICompatLlmClient())

    with mock_aws():
        import boto3

        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        store = S3RawStore(bucket=BUCKET, client=boto3.client("s3", region_name="us-east-1"))
        summary = ingest(
            _spec(), run_id="live-smoke", source_adapter=JSearchSourceAdapter(),
            raw_store=store, repo=repo, dissector=dissector,
        )
    assert summary["fetched"] >= 1
    assert summary["silvered"] >= 1  # at least one JD dissected into a posting
