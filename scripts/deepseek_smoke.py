"""DeepSeek API smoke test — proves the Bedrock→DeepSeek unblock (ADR-0017).

Reads the DeepSeek API key from AWS Secrets Manager (`jobfetcher/deepseek`; or the
`DEEPSEEK_API_KEY` env var as a fallback), makes ONE cheap chat-completion call to
the OpenAI-compatible endpoint, and reports PASS/FAIL.

It never prints the key. A PASS means the LLM path is live again — the Bedrock
new-account quota wall (ERR-001) is worked around, and the model id that worked is
the one to put in config.

    python scripts/deepseek_smoke.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

SECRET_NAME = "jobfetcher/deepseek"
REGION = "us-east-1"
ENDPOINT = "https://api.deepseek.com/chat/completions"
# Try the canonical v4 id first (what we'll put in config), then the stable legacy
# alias (retires 2026-07-24) so the smoke test still proves connectivity either way.
MODEL_CANDIDATES = ["deepseek-v4-flash", "deepseek-chat"]


def _load_key() -> str:
    """Key from $DEEPSEEK_API_KEY, else Secrets Manager. Accepts a raw key or a
    JSON blob {"api_key": "..."}. Returns the key; never logs it."""
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    import boto3  # lazy import so the env-var path needs no AWS SDK

    raw = (
        boto3.client("secretsmanager", region_name=REGION)
        .get_secret_value(SecretId=SECRET_NAME)
        .get("SecretString")
        or ""
    ).strip()
    try:
        data = json.loads(raw)
        return str(data.get("api_key") or data.get("apiKey") or "").strip() or raw
    except (json.JSONDecodeError, AttributeError):
        return raw  # not JSON → the whole secret string is the key


def _call(model: str, key: str) -> tuple[int, dict]:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "reply with the single word: OK"}],
            "max_tokens": 5,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read())


def main() -> int:
    try:
        key = _load_key()
    except Exception as e:  # noqa: BLE001
        print(
            f"FAIL: could not load the DeepSeek key (Secrets Manager `{SECRET_NAME}` / "
            f"$DEEPSEEK_API_KEY): {type(e).__name__}: {e}"
        )
        return 2
    if not key:
        print(f"FAIL: secret `{SECRET_NAME}` is empty — store the DeepSeek key first.")
        return 2

    last_err = None
    for model in MODEL_CANDIDATES:
        try:
            status, data = _call(model, key)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last_err = f"HTTP {e.code} on `{model}`: {detail}"
            if e.code == 401:
                print("FAIL: 401 Unauthorized — the stored key is wrong or revoked.")
                return 1
            continue  # 400/404 → try the next model id
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue

        reply = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        usage = data.get("usage", {})
        print("PASS - DeepSeek reachable via the OpenAI-compatible API.")
        print(f"  model  : {model}")
        print(f"  http   : {status}")
        print(f"  reply  : {reply!r}")
        print(
            f"  tokens : prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}"
        )
        print(
            f"\n  => ERR-001 worked around: the LLM path is LIVE (ADR-0017). "
            f"Use model id `{model}` in config."
        )
        return 0

    print(f"FAIL: no candidate model id worked. Tried {MODEL_CANDIDATES}. Last error: {last_err}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
