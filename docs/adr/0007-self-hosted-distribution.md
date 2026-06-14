# ADR-0007 — Self-hosted / open-source distribution (not SaaS)

## Status
Accepted

## Context
Should JobFetcher be a tool each user self-deploys, or a centralized service Tarig hosts for others? This decides liability, scope, and how much "works for strangers" effort is justified. The project's real goals are a job-search tool + a portfolio — not a startup.

## Decision
**Self-hosted / open-source.** Each user runs their own copy (clone → configure → `terraform apply`). The IaC + reproducibility *is* the portfolio value. **Multi-user** stays a documented future pivot (the `user_id` seam exists in the schema), built only as a deliberate later migration if ever.

## Alternatives Considered
- **Centralized SaaS.** Rejected: hosting it for others means owning their cost (Bedrock per user), auth, billing, support, multi-tenancy — and, critically, **other people's CVs/PII** (real legal/privacy liability) plus job-data ToS exposure. That's a company, not a portfolio piece; it explodes scope and distracts from landing a job.
- **Self-host now, SaaS later.** Effectively the chosen path: SaaS is a possible future pivot, not a v1 commitment.

## Consequences
- **Easier:** zero ongoing hosting liability; the repo/IaC is the deliverable; the "production-grade for one user" framing stays honest.
- **Harder:** real distribution to non-technical users is out of reach (they'd need an AWS account + setup) — accepted, since that's not a goal.
- **Impact:** keeps PII out of the repo ([decisions-locked](../ledgers/decisions-locked.md) — sanitized sample only; real data gitignored → private S3). Reinforces reproducible Terraform + clean `destroy` as first-class.
