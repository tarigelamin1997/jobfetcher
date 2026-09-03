<!-- Delete any section that genuinely does not apply, but do not delete the
     "Docs that only a human can check" question — answering it "no" in writing
     is the point. See ERR-016 in docs/ledgers/errors.md. -->

## What + why

<!-- The change, and the bottleneck or defect it addresses. Link the ADR / INV
     dossier / backlog item it came from. -->

## Docs that only a human can check

CI already fails on broken links, `plan §NN` citations, guarded counts that drift
from their ground-truth command, and stale "not yet deployed" claims
(`scripts/check_docs.py`). **It cannot tell whether this unit changed what the
system *is*.** That judgement is yours — answer it even when the answer is no,
because an unanswered question is what produced [ERR-016](../docs/ledgers/errors.md).

> **Did this unit add or change a surface, a resource, or a behaviour that a
> first-time reader must know about?**

- [ ] **No** — internal only. _(Say why in one line.)_
- [ ] **Yes** → then also updated, as applicable:
  - [ ] `README.md` / `CLAUDE.md` — the two entry points; what the system *is*
  - [ ] `docs/02-architecture.md` / `docs/diagrams.md` — the as-built picture. **A new internet-facing surface is never "just a detail"** — that omission is exactly what ERR-016 recorded.
  - [ ] `docs/ledgers/interface-contracts.md` — a changed port/contract shape
  - [ ] `docs/ledgers/phase-index.md` — live unit state
  - [ ] `CHANGELOG.md` — the `[Unreleased]` entry (owns the release narrative)
  - [ ] An ADR, if a real decision was made — with the rejected alternatives named

## Errors, deviations, surprises

- [ ] An [`errors.md`](../docs/ledgers/errors.md) entry for anything hit during this unit — including at deploy. *"A stage cannot close with an open error."*
- [ ] Nothing to record.

## Validation

<!-- Behavioral, and carrying a negative case — a presence/liveness check is no
     gate. If this PR adds or changes a gate, PROVE IT FAILS: link the run where
     it went red, not just the one where it passed (the standard ERR-015 and
     ERR-016 were closed to). -->

- [ ] `python scripts/check_docs.py` clean
- [ ] Tests + `ruff check` green in CI
