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
# The canonical v4 ids, in the order the pipeline uses them. Confirmed live 2026-09-01
# against GET /models: deepseek-v4-flash, deepseek-v4-pro, deepseek-v4-flash-vision-exp.
MODEL_CANDIDATES = ["deepseek-v4-flash", "deepseek-v4-pro"]


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
            # Reasoning OFF, same as the extraction path (ADR-0037). Without it these v4
            # models spend the whole budget thinking and return an empty reply — a 5-token
            # budget cannot survive a reasoner, so the check would fail on its own terms
            # rather than on anything about the provider.
            "thinking": {"type": "disabled"},
            "max_tokens": 16,
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

        choice = (data.get("choices") or [{}])[0]
        reply = (choice.get("message", {}).get("content") or "").strip()
        usage = data.get("usage", {})

        # BEHAVIORAL gate, not a liveness one (ERR-011). This script printed PASS through the
        # entire 38-day outage because it only ever checked for HTTP 200 — while the v4
        # reasoners were returning a well-formed 200 with an EMPTY reply, having spent the
        # whole token budget thinking. "The endpoint answered" is not "the LLM path works";
        # CLAUDE.md is explicit that a presence check is no gate.
        if not reply:
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            print(
                f"FAIL: `{model}` returned HTTP {status} with an EMPTY reply "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"completion_tokens={usage.get('completion_tokens')}"
                + (f", of which reasoning={reasoning}" if reasoning is not None else "")
                + ").\n"
                "  The endpoint is reachable but produced nothing usable. If finish_reason is\n"
                "  'length', the reasoning budget consumed max_tokens — see ERR-011 / ADR-0037."
            )
            return 1

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
