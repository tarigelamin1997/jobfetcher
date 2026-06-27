"""Shared test helpers: a scripted fake LLM, canned data, and probe-JD loading."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobfetcher.adapters.jsearch_source import (  # productionized in Step 4 — re-export for tests
    _seniority_from_title,
    jd_and_metadata_from_jsearch,
)
from jobfetcher.core.models import PostingMetadata

__all__ = [
    "CANNED_JD",
    "CANNED_LLM_JSON",
    "FakeLlm",
    "_seniority_from_title",
    "jd_and_metadata_from_jsearch",
    "load_probe",
]

PROBE_DIR = Path(__file__).resolve().parents[1] / "probe_output"

# A canned JD + a matching valid LLM reply, for unit tests (no network).
CANNED_JD = (
    "Required: 3+ years with Python and SQL. Experience with Airflow is a plus. "
    "You will build ETL pipelines on AWS."
)
CANNED_LLM_JSON = json.dumps(
    {
        "skills": [
            {"name": "Python", "level": "must", "evidence": "Required: 3+ years with Python and SQL"},
            {"name": "Airflow", "level": "nice", "evidence": "Experience with Airflow is a plus"},
        ],
        "sector": None,
        "normalized_title": "Data Engineer",
    }
)


class FakeLlm:
    """A scripted `LlmClient`: returns the queued replies in order (the last repeats)."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or [CANNED_LLM_JSON]
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def load_probe(name: str) -> tuple[str, PostingMetadata]:
    """Load a real probe JD (skips the test if the gitignored fixture isn't present)."""
    path = PROBE_DIR / name
    if not path.exists():
        pytest.skip(f"probe fixture not present: {path} (gitignored raw data)")
    return jd_and_metadata_from_jsearch(json.loads(path.read_text(encoding="utf-8")))
