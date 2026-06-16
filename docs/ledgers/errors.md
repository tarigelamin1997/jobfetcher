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
### ERR-001 — Anthropic Bedrock invocation blocked (base model ID + zero daily token quota)   [Open]
- Stage: pre-build · Bedrock readiness · Layer: Infra · Type: config / dependency
- Discovered: 2026-06-16 · Source: investigation (account `198592435375`, us-east-1, profile `samareltayeb`)

1. **What happened?** "Can't use Anthropic models." Two distinct failures, observed via `bedrock-runtime converse`:
   - base model id → `ValidationException: Invocation of model ID anthropic.claude-haiku-4-5-20251001-v1:0 with on-demand throughput isn't supported. Retry your request with the ID or ARN of an inference profile that contains this model.`
   - `us.` inference-profile id → `ThrottlingException: Too many tokens per day, please wait before trying again.`
2. **Why?** (a) Claude 4.x models in us-east-1 are **inference-profile-only** — base ids aren't invokable on-demand. (b) The account's **applied** Bedrock per-day token quota is **0 for every model** (AWS default is *billions*) → on-demand inference is blocked entirely. **NOT** a root/IAM problem (root reaches Bedrock fine — lists models + profiles).
3. **How?** (a) The call used the base model id. (b) The account has Service-Quota overrides of `0.0` tokens/day across all models — the signature of an account whose **billing / standing isn't configured for paid Bedrock usage**.
4. **How fixed?** (a) ✅ use the **`us.anthropic.*` cross-region inference-profile id** (e.g. `us.anthropic.claude-sonnet-4-6`). (b) ⏳ *Tarig:* enable billing / verify account standing, then raise **"Model invocation max tokens per day"** above 0 in Service Quotas. **Open until quota > 0.**
5. **Prevention + Detection:** code must *always* use inference-profile ids (boundary/contract check); a **v0 readiness gate** runs a 1-token `converse` against the chosen `us.` profile and **fails loudly** on `ValidationException` (wrong id) or `ThrottlingException`/quota-0 (billing) *before* the pipeline runs.
- **Blast radius:** the entire scoring + CV-tailoring pipeline (no LLM ⇒ no product) until resolved.
- **Prevention implemented?** No — pre-build; tracked here + in [04-v0-build-plan](../04-v0-build-plan.md) prerequisites.

| ID | Severity | Stage | Symptom | Status |
|---|---|---|---|---|
| ERR-001 | Critical | pre-build (Bedrock) | base-id ValidationException + account daily token quota = 0 | Open (needs billing/quota) |
