"""OpenAICompatLlmClient tests (network mocked): content parse + error mapping + key resolution."""
import io
import json
import urllib.error

import pytest

from jobfetcher.adapters import llm_openai
from jobfetcher.adapters.llm_openai import OpenAICompatLlmClient
from jobfetcher.config import LlmConfig
from jobfetcher.core.ports import (
    LlmAuthError,
    LlmBillingError,
    LlmError,
    LlmModelNotFoundError,
)


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


def test_402_maps_to_billing_error(monkeypatch):
    # ERR-010: the provider account ran out of credit and every dissection returned this.
    # It must be its own type — no retry can fix it, and the pipeline counts it separately
    # instead of emitting one indistinguishable warning per posting.
    body = '{"error":{"message":"Insufficient Balance","type":"unknown_error"}}'
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _raise_http(402, body))
    with pytest.raises(LlmBillingError, match="402"):
        _client().complete(system="s", user="u")


def test_402_is_not_retried(monkeypatch):
    # negative: a billing failure must fail on the FIRST attempt. Retrying it burns the
    # deadline budget on calls that cannot succeed.
    calls = []
    raiser = _raise_http(402, "Insufficient Balance")

    def _counting(req, timeout=0):
        calls.append(1)
        return raiser(req, timeout=timeout)

    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _counting)
    with pytest.raises(LlmBillingError):
        _client().complete(system="s", user="u")
    assert len(calls) == 1


def test_402_wins_over_the_model_not_found_heuristic(monkeypatch):
    # negative: the 404 branch matches on BODY text ("model" + "not"), so a 402 whose message
    # happens to mention a model must still classify as billing, not model-not-found.
    body = "Insufficient Balance: the model is not available on a zero-credit account"
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _raise_http(402, body))
    with pytest.raises(LlmBillingError):
        _client().complete(system="s", user="u")


def test_truncated_response_raises_instead_of_returning_empty(monkeypatch):
    """ERR-010 follow-up: the failure that looked like bad JSON for a month.

    DeepSeek v4 models are reasoners — `max_tokens` budgets reasoning AND content together,
    reasoning first. Exhaust it and the API returns HTTP 200, `finish_reason: "length"`, and
    an EMPTY content. The old `content or ""` turned that into `''`, which surfaced two
    layers up as `no JSON object in model output: ''` — blaming the model's formatting and
    triggering a re-prompt that cannot possibly help.
    """
    payload = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 512,
                  "completion_tokens_details": {"reasoning_tokens": 512}},
    }
    monkeypatch.setattr(
        llm_openai.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(payload)
    )
    with pytest.raises(LlmError, match="truncated at max_tokens"):
        _client().complete(system="s", user="u")


def test_truncated_error_names_the_budget_and_the_reasoning_share(monkeypatch):
    # the message has to carry the fix, not just the fact — an operator reading one warning
    # line should know to raise max_tokens without re-deriving it from the API docs
    payload = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 512,
                  "completion_tokens_details": {"reasoning_tokens": 400}},
    }
    monkeypatch.setattr(
        llm_openai.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(payload)
    )
    with pytest.raises(LlmError) as exc:
        _client().complete(system="s", user="u")
    msg = str(exc.value)
    assert "max_tokens=4096" in msg
    assert "reasoning=400" in msg
    assert "cannot help" in msg


def test_complete_returns_content_on_a_normal_stop(monkeypatch):
    # negative twin: finish_reason='stop' is the happy path and must NOT raise
    payload = {
        "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 12},
    }
    monkeypatch.setattr(
        llm_openai.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(payload)
    )
    assert _client().complete(system="s", user="u") == '{"ok": true}'


def test_missing_finish_reason_is_not_treated_as_truncation(monkeypatch):
    # negative: a provider that omits finish_reason entirely must still work — the check is
    # for the explicit "length" signal, never the absence of one
    payload = {"choices": [{"message": {"content": "hello"}}]}
    monkeypatch.setattr(
        llm_openai.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(payload)
    )
    assert _client().complete(system="s", user="u") == "hello"


# ------------------------------------------------- reasoning control (ERR-010 follow-up)
def _capture_body(monkeypatch, config):
    """Send one completion and return the JSON body that went over the wire."""
    seen = {}

    def _urlopen(req, timeout=0):
        seen["body"] = json.loads(req.data)
        return _FakeResp({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", _urlopen)
    OpenAICompatLlmClient(config, api_key="k").complete(system="s", user="u")
    return seen["body"]


def test_reasoning_off_sends_thinking_disabled(monkeypatch):
    # extraction: nothing to reason about, and reasoning costs ~7x the tokens
    body = _capture_body(monkeypatch, LlmConfig(reasoning=False, max_tokens=2048))
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body
    assert body["max_tokens"] == 2048


def test_reasoning_effort_is_sent_when_reasoning_is_on(monkeypatch):
    body = _capture_body(monkeypatch, LlmConfig(reasoning_effort="low"))
    assert body["reasoning_effort"] == "low"
    assert "thinking" not in body


def test_the_two_reasoning_keys_are_mutually_exclusive(monkeypatch):
    # negative: disabling thinking makes an effort level meaningless, and sending both invites
    # a 400 from any provider that validates the combination
    body = _capture_body(monkeypatch, LlmConfig(reasoning=False, reasoning_effort="high"))
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


def test_default_config_sends_neither_key(monkeypatch):
    # negative: portability (ADR-0012). A plain OpenAI-compatible host that knows nothing about
    # reasoning must see exactly the body it always did.
    body = _capture_body(monkeypatch, LlmConfig())
    assert "thinking" not in body
    assert "reasoning_effort" not in body
