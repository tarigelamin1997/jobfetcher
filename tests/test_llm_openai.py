"""OpenAICompatLlmClient tests (network mocked): content parse + error mapping + key resolution."""
import io
import json
import urllib.error

import pytest

from jobfetcher.adapters import llm_openai
from jobfetcher.adapters.llm_openai import OpenAICompatLlmClient
from jobfetcher.config import LlmConfig
from jobfetcher.core.ports import LlmAuthError, LlmError, LlmModelNotFoundError


class _FakeResp:
    def __init__(self, payload: dict):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client():
    return OpenAICompatLlmClient(LlmConfig(), api_key="test-key")


def _no_retry_client():
    return OpenAICompatLlmClient(LlmConfig(max_retries=0), api_key="test-key")


def _raise_http(code: int, body: str):
    def _u(req, timeout=0):
        raise urllib.error.HTTPError(
            "http://x/chat/completions", code, "err", None, io.BytesIO(body.encode())
        )

    return _u


def test_complete_returns_content(monkeypatch):
    payload = {"choices": [{"message": {"content": "hello"}}]}
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(payload))
    assert _client().complete(system="s", user="u") == "hello"


def test_401_maps_to_auth_error(monkeypatch):
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _raise_http(401, '{"error":"bad key"}'))
    with pytest.raises(LlmAuthError):
        _client().complete(system="s", user="u")


def test_model_not_found(monkeypatch):
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _raise_http(400, "Model not found: x"))
    with pytest.raises(LlmModelNotFoundError):
        _client().complete(system="s", user="u")


def test_500_maps_to_llm_error(monkeypatch):
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _raise_http(500, "server boom"))
    with pytest.raises(LlmError):
        _no_retry_client().complete(system="s", user="u")


def test_no_key_raises_auth(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm_openai, "_resolve_api_key", lambda config: "")
    with pytest.raises(LlmAuthError):
        OpenAICompatLlmClient(LlmConfig()).complete(system="s", user="u")


def test_env_key_is_used(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "envkey")
    assert llm_openai._resolve_api_key(LlmConfig()) == "envkey"


def _capture_payload(monkeypatch) -> dict:
    """Intercept the POST and return the parsed JSON request body the client sent."""
    captured: dict = {}

    def _fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data)
        return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _fake_urlopen)
    return captured


# --------------------------------------------------------------------- retry policy (ERR-006)
class _FlakyThenOk:
    """urlopen fake: raise the given HTTP errors in order, then return a good completion."""

    def __init__(self, *codes: int):
        self.codes = list(codes)
        self.calls = 0

    def __call__(self, req, timeout=0):
        self.calls += 1
        if self.codes:
            code = self.codes.pop(0)
            raise urllib.error.HTTPError(
                "http://x/chat/completions", code, "err", None, io.BytesIO(b"busy")
            )
        return _FakeResp({"choices": [{"message": {"content": "recovered"}}]})


def _no_sleep(monkeypatch) -> list[float]:
    """Neutralize the backoff sleep; return the list of requested delays."""
    delays: list[float] = []
    monkeypatch.setattr(llm_openai.time, "sleep", delays.append)
    return delays


def test_transient_503_is_retried_to_success(monkeypatch):
    """ERR-006 positive: two 503s then a 200 → the call succeeds, with backoff in between."""
    delays = _no_sleep(monkeypatch)
    fake = _FlakyThenOk(503, 503)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", fake)
    assert _client().complete(system="s", user="u") == "recovered"
    assert fake.calls == 3
    assert len(delays) == 2  # one backoff before each retry


def test_retries_exhausted_raises_llm_error(monkeypatch):
    """ERR-006 negative: a persistent 503 fails after exactly max_retries retries."""
    _no_sleep(monkeypatch)
    fake = _FlakyThenOk(503, 503, 503, 503, 503)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", fake)
    with pytest.raises(LlmError, match="after 3 retries"):
        _client().complete(system="s", user="u")
    assert fake.calls == 4  # 1 attempt + max_retries(3)


def test_auth_error_is_never_retried(monkeypatch):
    """A 401 is a config problem — retrying it is waste. Exactly one attempt."""
    delays = _no_sleep(monkeypatch)
    fake = _FlakyThenOk(401, 401)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", fake)
    with pytest.raises(LlmAuthError):
        _client().complete(system="s", user="u")
    assert fake.calls == 1
    assert delays == []


def test_non_retryable_4xx_fails_fast(monkeypatch):
    """A 400 (bad request) is not transient — no retry."""
    _no_sleep(monkeypatch)
    fake = _FlakyThenOk(422)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", fake)
    with pytest.raises(LlmError):
        _client().complete(system="s", user="u")
    assert fake.calls == 1


def test_connection_error_is_retried(monkeypatch):
    """URLError (network blip / timeout) counts as transient."""
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def _flaky(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("connection reset")
        return _FakeResp({"choices": [{"message": {"content": "back"}}]})

    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _flaky)
    assert _client().complete(system="s", user="u") == "back"
    assert calls["n"] == 2


def test_backoff_delays_grow_exponentially(monkeypatch):
    """Full jitter: each delay is uniform(0, base * 2^(attempt-1)) — bounds must grow."""
    monkeypatch.setattr(llm_openai.random, "uniform", lambda a, b: b)  # take the upper bound
    delays = _no_sleep(monkeypatch)
    fake = _FlakyThenOk(503, 503, 503, 503)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", fake)
    with pytest.raises(LlmError):
        _client().complete(system="s", user="u")
    assert delays == [1.0, 2.0, 4.0]  # backoff_base_s=1.0 doubling per retry


def test_max_retries_zero_disables_retrying(monkeypatch):
    _no_sleep(monkeypatch)
    fake = _FlakyThenOk(503, 503)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", fake)
    with pytest.raises(LlmError):
        _no_retry_client().complete(system="s", user="u")
    assert fake.calls == 1


@pytest.mark.parametrize("temp", [0.0, 0.7])
def test_temperature_from_config_is_in_request_payload(monkeypatch, temp):
    """VG3 (CI-enforceable): the client must send the *configured* temperature in the
    request body — not a hardcoded one. Asserting both 0.0 and a non-zero value makes
    this non-vacuous: a client that hardcoded any single constant would fail one branch."""
    captured = _capture_payload(monkeypatch)
    client = OpenAICompatLlmClient(LlmConfig(temperature=temp), api_key="test-key")
    client.complete(system="s", user="u")
    assert captured["body"]["temperature"] == temp
