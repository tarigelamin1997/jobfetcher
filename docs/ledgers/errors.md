# Ledger · Error & Incident Log

> Every error, incident, deviation, or chaos-discovered gap is logged here — **constructed live, not reconstructed later.** Read this log *before* re-attempting any fix (a solution that reintroduces a known-failed approach is a documentation failure, not an engineering one). Paste error messages **verbatim** — the exact tokens are the searchable value. A stage cannot close with an open error.

## Entry template (the Five Questions)
```
### ERR-NNN — <short description>     [Open | Resolved]
- Stage / component · Layer · Type (data | connectivity | config | dependency | logic)
- Discovered: <date>  ·  Resolved: <date>  ·  Source: <implementation | chaos-discovered>
1. What happened?   <symptom as observed; verbatim error string>
2. Why did it happen? <root cause — the exact line/config/assumption that was wrong>
3. How did it happen? <chain of events>
4. How did we fix it? <exact files / commits / commands>
5. How do we prevent recurrence? <a concrete guard: a test / DQ check / CI gate / schema rule>
   + Detection: <what check would have caught this earlier — mandatory>
- Blast radius: <what downstream was affected>
- Prevention implemented? <Yes/No + commit>   (an incident without an implemented prevention is OPEN)
```

## Log
### ERR-001 — Anthropic Bedrock invocation blocked (base model ID + zero daily token quota)   [Mitigated — worked around via ADR-0017]
- Stage: pre-build · Bedrock readiness · Layer: Infra · Type: config / dependency
- Discovered: 2026-06-16 · Source: investigation (account `198592435375`, us-east-1, profile `samareltayeb`)

1. **What happened?** "Can't use Anthropic models." Two distinct failures, observed via `bedrock-runtime converse`:
   - base model id → `ValidationException: Invocation of model ID anthropic.claude-haiku-4-5-20251001-v1:0 with on-demand throughput isn't supported. Retry your request with the ID or ARN of an inference profile that contains this model.`
   - `us.` inference-profile id → `ThrottlingException: Too many tokens per day, please wait before trying again.`
2. **Why?** (a) Claude 4.x models in us-east-1 are **inference-profile-only** — base ids aren't invokable on-demand. (b) The account's per-day Bedrock token quota is **0 for every model AND `Adjustable = False`** (AWS default is *billions*) → on-demand inference is blocked entirely. **NOT** root/IAM (root reaches Bedrock fine), and **NOT** billing (Tarig confirmed a valid payment method + unused new-account credits).
3. **How?** (a) The call used the base model id. (b) The account is **brand-new**: AWS gates new accounts with a **non-adjustable 0 daily-token quota** on Bedrock until the account matures — independent of billing. (Free credits are *spend*, not a *rate* quota — they can't buy past a 0 cap.)
4. **How fixed?** (a) ✅ use the **`us.anthropic.*` inference-profile id** (e.g. `us.anthropic.claude-sonnet-4-6`). (b) ⏳ the daily quota is **non-adjustable**, so a Service-Quotas increase request doesn't apply — it lifts via **account maturity** (commonly days → ~2 weeks; re-test) and/or an **AWS Support case** asking to raise the new-account Bedrock daily-token quota. **Open until quota > 0.**
5. **Prevention + Detection:** code must *always* use inference-profile ids (boundary/contract check); a **v0 readiness gate** runs a 1-token `converse` against the chosen `us.` profile and **fails loudly** on `ValidationException` (wrong id) or `ThrottlingException`/quota-0 (billing) *before* the pipeline runs.
- **Blast radius:** the entire scoring + CV-tailoring pipeline (no LLM ⇒ no product) until resolved.
- **Prevention implemented?** No — pre-build; tracked here + in [04-v0-build-plan](../04-v0-build-plan.md) prerequisites.
- **Update (Kimi K2 / model-agnostic):** `moonshot.kimi-k2-thinking` and `moonshotai.kimi-k2.5` are **ACTIVE + `ON_DEMAND`** (no inference-profile gotcha), **but** their daily-token quota also reads **`0.0` / non-adjustable** → the new-account wall is **account-wide, not Anthropic-specific.** The model is now **config-driven** ([ADR-0012](../adr/0012-model-agnostic-llm.md)), so whichever model unblocks first is a one-line swap.
- **✅ RESOLVED open item — Kimi API `converse` test run 2026-06-17** (as `jobfetcher-dev`, us-east-1, model `moonshot.kimi-k2-thinking`, `maxTokens:5`): → **`ThrottlingException: Too many tokens per day, please wait before trying again.`** **Conclusion: switching models does NOT bypass the wall** — the 0/non-adjustable daily-token quota throttles Kimi via the **API** exactly like Anthropic. Where Kimi appeared to "work fine" was the **Bedrock console playground** (separate limits), **not** the `bedrock-runtime` API. So the only real unblock is lifting the account-wide daily-token quota (account maturity / **AWS Support case**) — model choice is irrelevant until then. ERR-001 stays **Open** (the quota, not the diagnosis, is the blocker).
- **Model decision (2026-06-21):** **Kimi K2 Thinking (`moonshot.kimi-k2-thinking`) is the chosen model** ([ADR-0012](../adr/0012-model-agnostic-llm.md)) — no longer waiting on Anthropic. This picks the *model*; it does **not** lift this quota — Kimi stays gated here until the daily-token cap lifts. ERR-001 remains the one blocker on the scoring path.
- **Re-test 2026-06-23** (Tarig believed the quota had cleared — a manual *console* check looked fine): 1-token `bedrock-runtime converse` → `moonshot.kimi-k2-thinking` (jobfetcher-dev, us-east-1) → **still `ThrottlingException: Too many tokens per day`**. The account-wide quota has **not** lifted ~1 week on; the console-vs-API gap holds — the **console playground has separate limits and is not proof for the pipeline** (which calls the `bedrock-runtime` API). ERR-001 stays **Open**; re-test periodically.
- **AWS Support case filed 2026-06-23 — case ID `178220019100382`** (live-chat). Asks AWS to raise the new-account Bedrock on-demand per-day token quotas above 0 — notably **`L-E239925C`** "Model invocation max tokens per day for **Kimi K2 Thinking**" (currently `0.0` / non-adjustable; sibling `L-3587C5E5` = Kimi K2.5) — for account `198592435375`, us-east-1. Billing + model access confirmed; root *and* admin-IAM both throttle. **Re-test trigger:** when AWS confirms the bump, re-run the 1-token `converse` against `moonshot.kimi-k2-thinking` — a completion ⇒ Bedrock is usable again (re-point config; see the mitigation below).
- **✅ MITIGATED 2026-06-24 — routed around via DeepSeek ([ADR-0017](../adr/0017-llm-transport-openai-compatible-deepseek.md)).** We stopped waiting on the Bedrock quota and moved the LLM **transport** to the **OpenAI-compatible API** (v0 provider = **DeepSeek API**, which has no new-account gate), behind the model-agnostic `LlmClient` port ([ADR-0012](../adr/0012-model-agnostic-llm.md)). The Bedrock quota is **still 0** — this is *not Resolved* — but it **no longer blocks** us: Bedrock is now one parked, config-swappable backend. AWS case `178220019100382` stays open as **optional** (flip config back to Bedrock if it ever lifts). The whole pipeline (silver dissection → gold → score) is **live-runnable** once the DeepSeek key lands in Secrets Manager (`jobfetcher/deepseek`). **✅ Verified 2026-06-24** — key stored (rotated) + $2 balance; `scripts/deepseek_smoke.py` returned **HTTP 200** from `deepseek-v4-flash` (prompt=11 / completion=5 tokens). The LLM path is **LIVE**; config model id = `deepseek-v4-flash`. *(One detour en route: DeepSeek's "free signup tokens" did not apply — the API returned `402 Insufficient Balance` until a small top-up; the key + integration were valid throughout.)*

| ID | Severity | Stage | Symptom | Status |
|---|---|---|---|---|
| ERR-001 | Critical | pre-build (Bedrock) | base-id ValidationException + new-account daily token quota = 0 (non-adjustable) | **Mitigated** — routed around via DeepSeek / OpenAI-compatible ([ADR-0017]); quota still 0 but no longer blocking; AWS case `178220019100382` open (optional) |
