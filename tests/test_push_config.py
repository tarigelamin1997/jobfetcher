"""Unit tests for the reusable config functions the control panel (scripts/panel.py) shares
with the CLI (scripts/push_config.py): `validate_config_text` is THE gate (a bad edit never
reaches S3), and `push_config_text` is the shared writer. The panel carries no separate
validation path — these functions do, so they are unit-tested here."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from jobfetcher.core.search_spec import SearchSpec

ROOT = Path(__file__).resolve().parents[1]

# load the standalone script as a module (same harness as test_track.py / test_export.py)
_spec = importlib.util.spec_from_file_location(
    "push_config", ROOT / "scripts" / "push_config.py"
)
push_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_config)


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kw: Any) -> dict:
        self.puts.append(kw)
        return {}


def test_validate_config_text_accepts_the_valid_sample():
    # the committed sample is the complete, valid template — it must pass the gate.
    text = (ROOT / "config" / "search_config.sample.yml").read_text(encoding="utf-8")
    push_config.validate_config_text(text, SearchSpec)  # no raise


def test_validate_config_text_rejects_an_incomplete_config():
    # negative: the all-required contract — a config missing fields fails LOUDLY (never uploaded).
    with pytest.raises(Exception):  # noqa: B017, PT011 — pydantic ValidationError (or a YAML error)
        push_config.validate_config_text("threshold: 60\n", SearchSpec)


def test_push_config_text_uploads_via_injected_client():
    client = _FakeS3()
    push_config.push_config_text(
        bucket="b", key="config/search_config.yml", text="x: 1\n", client=client
    )
    assert len(client.puts) == 1
    call = client.puts[0]
    assert call["Bucket"] == "b"
    assert call["Key"] == "config/search_config.yml"
    assert call["Body"] == b"x: 1\n"
    assert call["ContentType"] == "application/x-yaml"
