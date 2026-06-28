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
        _client().complete(system="s", user="u")


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


@pytest.mark.parametrize("temp", [0.0, 0.7])
def test_temperature_from_config_is_in_request_payload(monkeypatch, temp):
    """VG3 (CI-enforceable): the client must send the *configured* temperature in the
    request body — not a hardcoded one. Asserting both 0.0 and a non-zero value makes
    this non-vacuous: a client that hardcoded any single constant would fail one branch."""
    captured = _capture_payload(monkeypatch)
    client = OpenAICompatLlmClient(LlmConfig(temperature=temp), api_key="test-key")
    client.complete(system="s", user="u")
    assert captured["body"]["temperature"] == temp
