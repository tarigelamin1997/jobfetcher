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
2. **Why?** (a) Claude 4.x models in us-east-1 are **inference-profile-only** — base ids aren't invokable on-demand. (b) The account's per-day Bedrock token quota is **0 for every model AND `Adjustable = False`** (AWS default is *billions*) → on-demand inference is blocked entirely. **NOT** root/IAM (root reaches Bedrock fine), and **NOT** billing (Tarig confirmed a valid payment method + unused new-account credits).
3. **How?** (a) The call used the base model id. (b) The account is **brand-new**: AWS gates new accounts with a **non-adjustable 0 daily-token quota** on Bedrock until the account matures — independent of billing. (Free credits are *spend*, not a *rate* quota — they can't buy past a 0 cap.)
4. **How fixed?** (a) ✅ use the **`us.anthropic.*` inference-profile id** (e.g. `us.anthropic.claude-sonnet-4-6`). (b) ⏳ the daily quota is **non-adjustable**, so a Service-Quotas increase request doesn't apply — it lifts via **account maturity** (commonly days → ~2 weeks; re-test) and/or an **AWS Support case** asking to raise the new-account Bedrock daily-token quota. **Open until quota > 0.**
5. **Prevention + Detection:** code must *always* use inference-profile ids (boundary/contract check); a **v0 readiness gate** runs a 1-token `converse` against the chosen `us.` profile and **fails loudly** on `ValidationException` (wrong id) or `ThrottlingException`/quota-0 (billing) *before* the pipeline runs.
- **Blast radius:** the entire scoring + CV-tailoring pipeline (no LLM ⇒ no product) until resolved.
- **Prevention implemented?** No — pre-build; tracked here + in [04-v0-build-plan](../04-v0-build-plan.md) prerequisites.

| ID | Severity | Stage | Symptom | Status |
|---|---|---|---|---|
| ERR-001 | Critical | pre-build (Bedrock) | base-id ValidationException + new-account daily token quota = 0 (non-adjustable) | Open (awaiting account maturity / AWS Support) |
