"""DbConfig URL resolution (no DB). Each case carries a negative."""
import pytest
from pydantic import ValidationError

from jobfetcher.config import DbConfig


def test_from_env_reads_url(monkeypatch):
    monkeypatch.setenv("JOBFETCHER_DB_URL", "postgresql://u:p@localhost:5432/jf")
    cfg = DbConfig.from_env()
    assert cfg is not None
    assert cfg.connection_url == "postgresql://u:p@localhost:5432/jf"


def test_from_env_strips_whitespace(monkeypatch):
    monkeypatch.setenv("JOBFETCHER_DB_URL", "  postgresql://localhost/jf  ")
    cfg = DbConfig.from_env()
    assert cfg is not None and cfg.connection_url == "postgresql://localhost/jf"


def test_from_env_unset_returns_none(monkeypatch):
    # negative: no env var -> None (callers / the integration test skip cleanly).
    monkeypatch.delenv("JOBFETCHER_DB_URL", raising=False)
    assert DbConfig.from_env() is None


def test_from_env_blank_returns_none(monkeypatch):
    # negative: a blank/whitespace value is treated as unset, not a valid URL.
    monkeypatch.setenv("JOBFETCHER_DB_URL", "   ")
    assert DbConfig.from_env() is None


def test_empty_connection_url_rejected():
    # negative: the model itself forbids an empty connection_url.
    with pytest.raises(ValidationError):
        DbConfig(connection_url="")


def test_aurora_data_api_url_accepted():
    # the deployed form (the dialect selects Aurora by scheme) is a valid URL too.
    url = "postgresql+auroradataapi://:@/jobfetcher?aurora_cluster_arn=arn&secret_arn=arn"
    assert DbConfig(connection_url=url).connection_url == url
