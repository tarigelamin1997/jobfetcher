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
*No errors yet — building has not started. The first entries will come from the v0 build (Step-by-step in [04-v0-build-plan](../04-v0-build-plan.md)) and its validation gate.*

| ID | Severity | Stage | Symptom | Status |
|---|---|---|---|---|
| — | — | — | (none) | — |
