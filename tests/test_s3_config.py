"""`S3ConfigStore` + `read_config_text` unit tests (no AWS, no moto — a fake S3 client): the
unset-bucket error, a get_text hit, the NoSuchKey -> ConfigNotFound mapping, the s3:// URI
parse (valid + malformed), and the read_config_text dispatch (local file vs s3://). Each
carries a negative. Mirrors tests/test_s3_raw.py's fake-client style."""
from __future__ import annotations

import io

import pytest

from jobfetcher.adapters.s3_config import (
    _BUCKET_ENV,
    ConfigNotFound,
    S3ConfigStore,
    parse_s3_uri,
    read_config_text,
)


class _NoSuchKey(Exception):
    """A botocore ClientError 404 shape (the `response` dict is what get_text reads)."""

    def __init__(self) -> None:
        super().__init__("NoSuchKey")
        self.response = {
            "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }


class _FakeS3:
    """Serves one key -> text; any other key raises NoSuchKey (like real S3)."""

    def __init__(self, objects: dict[str, str]) -> None:
        self._objects = objects
        self.calls: list[tuple[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803 (boto3 kwarg case)
        self.calls.append((Bucket, Key))
        if Key not in self._objects:
            raise _NoSuchKey()
        return {"Body": io.BytesIO(self._objects[Key].encode("utf-8"))}


# ── S3ConfigStore ───────────────────────────────────────────────────────────
def test_unset_bucket_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(_BUCKET_ENV, raising=False)
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3ConfigStore(client=_FakeS3({}))


def test_get_text_returns_decoded_body():
    store = S3ConfigStore(bucket="b", client=_FakeS3({"config/x.yml": "threshold: 60\n"}))
    assert store.get_text("config/x.yml") == "threshold: 60\n"


def test_missing_object_raises_config_not_found():
    # negative: a missing config must fail loudly + actionably, never a silent empty config
    store = S3ConfigStore(bucket="b", client=_FakeS3({}))
    with pytest.raises(ConfigNotFound, match="push_config"):
        store.get_text("config/missing.yml")


def test_non_404_error_propagates():
    # negative: an ambiguous client error is NOT swallowed as "absent"
    class _Boom:
        def get_object(self, **kw):  # noqa: ANN003, ARG002
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        S3ConfigStore(bucket="b", client=_Boom()).get_text("config/x.yml")


# ── parse_s3_uri ─────────────────────────────────────────────────────────────
def test_parse_s3_uri_valid():
    assert parse_s3_uri("s3://my-bucket/config/search_config.yml") == (
        "my-bucket",
        "config/search_config.yml",
    )


@pytest.mark.parametrize("bad", ["s3://only-bucket", "s3:///no-bucket-key", "s3://bucket/"])
def test_parse_s3_uri_malformed_is_loud(bad):
    with pytest.raises(ValueError, match="malformed S3 URI"):
        parse_s3_uri(bad)


# ── read_config_text dispatch ────────────────────────────────────────────────
def test_read_config_text_local_file(tmp_path):
    p = tmp_path / "cfg.yml"
    p.write_text("hello: world\n", encoding="utf-8")
    assert read_config_text(str(p)) == "hello: world\n"


def test_read_config_text_missing_local_is_loud(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_config_text(str(tmp_path / "nope.yml"))


def test_read_config_text_s3_uri_uses_the_store():
    fake = _FakeS3({"config/search_config.yml": "threshold: 70\n"})
    text = read_config_text("s3://the-bucket/config/search_config.yml", client=fake)
    assert text == "threshold: 70\n"
    assert fake.calls == [("the-bucket", "config/search_config.yml")]  # right bucket + key
