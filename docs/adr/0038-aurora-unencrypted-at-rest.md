# ADR-0038 — Aurora runs unencrypted at rest, deliberately, while the data is experimental

**Status:** Accepted · decided by Tarig 2026-09-01 · **a labeled trade-off, not an oversight** · revisit when the data stops being replaceable

## Context

The Aurora cluster has never set `storage_encrypted`, so it defaults to `false` — confirmed on the live cluster (`StorageEncrypted: false`, `KmsKeyId: null`). This surfaced during a 2026-08-31 codebase review, not from any incident.

"Unencrypted RDS" is the single most common finding in any AWS security review, and this is a public portfolio repo. So the absence needs to be either fixed or **explicitly owned**. This ADR owns it.

**One factual correction worth recording, because it was the basis of the original objection.** Encryption at rest on Aurora is **free**. `storage_encrypted = true` with no `kms_key_id` uses the AWS-managed key (`aws/rds`), which carries **no monthly charge** and no measurable performance cost. A customer-managed key would be ~$1/month plus per-request charges — but we would not need one. So there is no recurring bill to avoid here. The real cost is entirely different, and it is the reason for this decision.

## Decision

**Leave the cluster unencrypted for now, and say so plainly everywhere the architecture is described.**

The cost that matters is not money, it is that **`storage_encrypted` cannot be changed in place.** Terraform destroys and recreates the cluster to apply it — and with `skip_final_snapshot = true` and `deletion_protection = false` (both deliberate, for the teardown-to-$0 cadence), that happens silently and unrecoverably. Adding the argument turns the *next* routine `terraform apply`, including an ordinary code deploy, into total data loss.

Against that: the database currently holds one ~1.5 KB profile row (no contact PII, by the `config/profile.sample.yml` convention), 3,232 job postings that were public when fetched, and 1,635 score events. Encryption at rest defends only against reading raw storage without authenticating — AWS's physical disks, or a copied/shared snapshot. It is transparent to anyone holding credentials, so it does **nothing** against the realistic risks: a leaked AWS access key, a leaked DB secret, or a bug in our own code. The Lambda reads plaintext either way.

So the honest position is: *we know this is the production default, we know why it exists, and at this data sensitivity we are choosing not to pay a cluster recreate for it yet.*

**The commented-out `storage_encrypted` line was removed from `terraform/aurora.tf`** and replaced with a prose note that contains no code. A commented-out setting one keystroke away from destroying the database is a hazard, not documentation — and a future session or agent "tidying up" by uncommenting it would have no way to know that. The procedure survives, deliberately, only in [the runbook](../runbooks/deploy.md#5--encrypting-aurora-at-rest-one-time-destroys-the-cluster), where it cannot be executed by accident.

## Alternatives Considered

- **Encrypt now via destroy-and-recreate.** Genuinely cheap *today*: export with `scripts/export.py`, add the argument, and let the existing end-of-day `terraform destroy` do the work. Rejected for now because the experimental data does not justify even that small ceremony, and because it puts a live data-loss trigger in the repo during a week when a deploy is pending. This is the option to take the moment the data starts mattering — and it stops being available then.
- **Encrypt via snapshot → encrypted copy → restore.** The correct procedure once data is real, and the one this ADR defers to. Rejected as premature: it is a multi-step human-present migration for a database whose entire contents are currently reproducible (bronze re-fetches, scores re-derive via reassess).
- **Keep the setting commented out in `aurora.tf` as a reminder.** This is what was originally built, and Tarig rejected it: a commented line is one edit away from a destroy, and nothing in the file's syntax communicates that. The risk of a well-meaning future edit outweighs the convenience of having it pre-written.
- **Say nothing and leave the default.** Rejected — that is the version that reads as an oversight to a reviewer, and it is the one thing the [design philosophy](../00-design-philosophy.md) forbids: an unexamined default masquerading as a decision.

## Consequences

- **A reviewer who spots the unencrypted cluster will find this ADR**, plus a matching note in `terraform/aurora.tf` and the README. The finding is real; the answer is that it was weighed. That is a better portfolio position than either a silent default or a security setting nobody understood.
- **The migration cost grows with the data.** Today it is a teardown you already perform. Later it is a snapshot-copy-restore with downtime. The trigger to revisit is not a date — it is the first time the database holds something that cannot be re-derived (real application outcomes, a CV, anything with contact PII).
- **The stated threat model is narrow on purpose.** If any of these change, this ADR is void: contact PII enters the profile, the outcome log accumulates real application history, the database is shared with anyone, or a snapshot is ever copied outside the account.
- **`terraform/aurora.tf` now carries an explicit instruction not to add the argument**, aimed at future sessions and agents as much as humans, because the failure mode is a plausible "helpful" edit rather than a typo.
- **Nothing about this defends the other gaps found in the same review** — the capture endpoint's replay window, no IaC security scanning, the SES wildcard. Those are recorded separately in the [backlog](../ledgers/backlog.md) and are not covered by this decision.
