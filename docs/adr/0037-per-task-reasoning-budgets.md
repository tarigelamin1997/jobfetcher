# ADR-0037 — Per-task reasoning budgets: extraction thinks nothing, scoring thinks a little

**Status:** Accepted · **built 2026-09-01**, branch `feat/gold-rejection-lineage` · resolves [ERR-011](../ledgers/errors.md) · decided by Tarig 2026-09-01 · non-crucial by blast radius (no schema, no infra) but it **changes what the scorer does**, so it is recorded as a decision rather than a tuning tweak

## Context

DeepSeek's v4 models are **reasoners**. `max_tokens` budgets reasoning *and* content together, and reasoning runs first. Exhaust it and the API returns a well-formed **HTTP 200** whose `content` is empty.

The pipeline used one `LlmConfig` shape for both stages — `max_tokens=4096`, no reasoning control — and `adapters/llm_openai.py` read the response as `content or ""`, discarding `finish_reason`. So a truncated completion arrived two layers up as `no JSON object in model output: ''`: a message that blames the model's formatting, followed by a retry at the same budget that cannot possibly help.

Measured live against the real scorer prompt at the production budget:

```
max_tokens=4096  →  finish_reason=length,  reasoning_tokens=4096,  content_chars=0
```

The model spent the entire budget thinking and emitted nothing. This is the failure in the 2026-08-22 production log, and it was independent of both [ERR-010](../ledgers/errors.md) (the 1 MB read) and the exhausted account: **fixing those two would have produced a clean run that still delivered zero postings.**

## Decision

**Split the budget by task, from measurement, and read the truncation signal.**

`LlmConfig` gains `reasoning: bool = True` and `reasoning_effort: Literal["low","medium","high"] | None`. Exactly one of the two API keys is ever sent (`thinking: {"type":"disabled"}` *or* `reasoning_effort`), and the default config sends **neither** — a plain OpenAI-compatible host sees the body it always did, so [ADR-0012](0012-model-agnostic-llm.md) portability holds.

**Extraction — reasoning OFF, `max_tokens=2048`** (`_dissect_llm_config()`). Dissection fills a fixed JSON schema from text that already contains every answer; there is nothing to reason about. Measured: **425 tokens** with reasoning off against ~2,950 with it on — a ~7× saving on the highest-volume call in the pipeline. 2048 is ~5× the observed need.

**Scoring — reasoning ON at `effort: "high"`, `max_tokens=6144`** (`_score_llm_config()`). Scoring is where judgment is the product: postings describe the same role in very different words and split responsibilities differently, so reconciling a JD against the profile is real work and high effort is bought deliberately. Keeping reasoning on also keeps [ADR-0031](0031-boundary-self-consistency-honest-graduations.md)'s calibration honest — the measured 15.95-point spread that justifies a resample margin of 16 describes a *reasoning* model.

**The fix is naming the effort, not lowering it.** Omitting `reasoning_effort` does not select a default — it leaves reasoning **uncapped**, which is what burned all 4,096 tokens and returned an empty completion. Measured over four live scores at *explicit* high effort: **1,334–2,348 tokens** total (1,032–2,036 reasoning). `max_tokens=6144` is ~2.6× that ceiling and costs nothing extra, because with an explicit effort usage does **not** expand to fill the budget (2,348 tokens at 8,192; 2,227 at 12,288; 1,334 at 16,384) — only the uncapped setting did that.

**And read `finish_reason`.** `complete()` raises on `"length"` with a message naming `max_tokens`, the reasoning share, and the fix. It stays an `LlmError`, so per-item isolation is unchanged: one truncated posting still never kills a run.

## Alternatives Considered

- **Reasoning off for scoring too.** Cheapest by a wide margin (~450 tokens/call) and it removes truncation risk everywhere. Rejected: scoring is the one place in the pipeline where judgment is the product, and — decisively — every number in ADR-0031 was measured against a reasoning model. Turning it off would make the resample margin, the drift buckets, and the "avg spread 15.95" baseline describe a model no longer in use, with nothing announcing the invalidation. If we ever want this, it is a deliberate re-calibration, not a config tweak.
- **Scoring at `low` effort** (the first cut of this ADR, before measurement). Rejected once the numbers were in: `low` was chosen believing high effort was what blew the budget, but explicit `high` costs only 1,334–2,348 tokens — the uncapped *absence* of the parameter was the fault, not its level. Buying less reasoning than needed on the one judgment call in the pipeline is a bad trade for ~1,000 tokens.
- **Leave reasoning at default effort and just raise `max_tokens` to 8192.** The obvious fix, and wrong. Reasoning **expands to fill the budget offered** — measured at `effort=low`: 886 reasoning tokens at `max_tokens=4096` but **1,412** at 6,144. A bigger budget buys more thinking and more cost, not more headroom, so this trades a hard failure for an unbounded bill and only moves the cliff.
- **One shared `LlmConfig` with a single generous budget.** Rejected: extraction and scoring have genuinely different shapes (425 vs 1,224 tokens; schema-filling vs judgment). Sharing is what let one stage's assumptions silently set the other's in the first place.
- **Retry at a larger budget on truncation.** Tempting as an auto-heal. Rejected: it hides a config error behind cost, and the failure is deterministic for a given prompt size — the second attempt would truncate too, or succeed only by spending unboundedly. Fail loudly with the number to change.
- **A `SearchSpec` knob so the operator tunes it.** Rejected ([ADR-0031](0031-boundary-self-consistency-honest-graduations.md) set this precedent): these are technical anti-failure defaults, not user preferences, and a new required field is a coupled config-and-code deploy.

## Consequences

- **The pipeline can actually produce output again.** Verified live: `test_live_scorer` and `test_integration_deepseek`, both failing before this change, pass against the real API.
- **~7× cheaper extraction**, on the call made once per fetched posting — by far the highest-volume LLM call in the run. Scoring goes from a 4,096-token failure to ~2,000 tokens of success at full reasoning effort.
- **The v0.11.0 cost picture is now visible, and it is worse than it looked.** A boundary posting is scored 3× ([ADR-0031](0031-boundary-self-consistency-honest-graduations.md)), so it costs ~6,000 tokens rather than the ~1,500 anyone would have estimated before knowing about reasoning tokens. That is very likely the real mechanism behind backlog **B-4** (full-backlog reassess runs out of time), and it reframes that entry from "resampling is slow" to "resampling is 3× a much larger number than we thought."
- **A truncated response is now loud.** It names the budget, the reasoning share, and the fix, instead of arriving as `''` three layers away from its cause.
- **Live tests now exercise the production config.** They previously built their own `LlmConfig`, so they proved *a* configuration worked while production's was broken. Same shape as the `ALEMBIC_HEAD` gap (backlog **B-7**): a test that constructs its own copy of production config tests the copy.
- **`scripts/deepseek_smoke.py` was a liveness check, and is now a behavioral one.** It printed PASS through the entire 38-day outage because it asserted HTTP 200 and never checked the reply was non-empty — precisely what `CLAUDE.md` forbids ("a presence/liveness check is *no gate*"). It now FAILs on an empty reply and reports `finish_reason` and the reasoning-token count. Its model list also still named `deepseek-chat`, an alias that retired 2026-07-24; replaced with the two ids confirmed live against `GET /models`.
- **Provider coupling, stated honestly.** `thinking: {"type": "disabled"}` and `reasoning_effort` are not universal OpenAI-compatible fields. They are sent only when explicitly configured, so the default path stays portable — but a provider swap now has one more thing to check.
