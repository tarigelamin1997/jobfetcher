# ADR-0030 — Reachable full job list from the digest (presigned S3 report link)

**Status:** Accepted · **shipped v0.10.0** (2026-07-10) · the first bottleneck solved end-to-end by the autonomous agentic squad (Investigator → Surgeon → Examiner → auto-merge → live deploy)

## Context
The truthful digest ([ADR-0027](0027-digest-truthfulness.md)) deliberately compacts the long tail: the still-open overflow beyond the top-5 and the entire below-threshold set collapse into two lines of **non-clickable text** ("…and N more — see your export" · "+N below your threshold"), where "your export" is the **local** [`scripts/export.py`](../../scripts/export.py) ([ADR-0024](0024-query-via-export.md)). Measured live (2026-07-10): **286 scored, 61 above / 225 below** threshold 60 — so **~281 of 286 scored jobs had no clickable path** from the email. Tarig logged this from real use (backlog **B-1**); the P2 Investigator verified it and ranked it the top squad-actionable bottleneck (B-2 deliverability outranks it on impact but is blocked on a sender domain the squad can't provision).

## Decision
Each daily run renders a **single self-contained HTML page** of ALL scored jobs (surfaced + still-open + below-threshold; sortable/filterable via inline vanilla JS, no framework/CDN/external asset), writes it to S3 **`reports/{run_date}/jobs-{run_id}.html`**, presigns it, and embeds that **https** URL in **both** dead-text sinks (the still-open overflow line AND the below-threshold footer). New pure `core/report.py` (render) + `adapters/s3_reports.py` (`S3ReportStore`, lazy-boto3 mirroring `s3_raw.py`) + `Repository.get_all_scored`. Built inside a **non-fatal guard** in `notify` — a report build/upload/presign failure degrades the digest to today's plain text and **never blocks the send** (the send itself stays loud). **No migration, no Terraform/IAM change** (S3 `PutObject` already granted bucket-wide), **no new dependency**.

## Alternatives Considered
- **Rung 1 — presigned CSV only.** Reachable, but filtering means opening Excel/Sheets (clunky on mobile morning-triage). Rejected: the dominant cost (`get_all_scored` + S3 write + presign + digest plumbing) is *identical* to the HTML page; the only delta is a pure render function reusing the notifier's zero-dependency template — so the mobile-friendly HTML page wins at negligible extra complexity.
- **Rung 3 — hosted dashboard + auth** (the [ADR-0024](0024-query-via-export.md) end-state), optionally with inline override/graduation actions. Bigger build (hosting + auth); **deferred**.
- **Keep the local-script-only status quo.** Exactly the friction Tarig logged; rejected.

## Consequences
- **Easier:** the full long tail — including the 225 below-threshold "unqualified" jobs — is one click from the email; realizes the human-as-final-judge loop over the whole scored set.
- **Constraint (documented, not hidden):** a presigned URL signed with the **Lambda role's temporary creds** is capped at the session-token TTL (hours, not days). Accepted for a *daily* email (same-day reachability; tomorrow's digest regenerates the link). A durable multi-day link is the rung-3 hosted surface — deferred.
- **Non-fatal by design:** any report failure (incl. a Data-API dialect quirk in `get_all_scored`) degrades to plain text — never a new way to fail the daily run.
- **Live-validated (2026-07-10):** deployed (terraform: 1 change in-place, no migration), post-deploy smoke `200 @ 0006_subscores`, and `get_all_scored` confirmed over the **Aurora Data API** — 286 rows, the ~242 KB report page rendered + written to S3 + presigned. The full email-with-link path validates on the next unattended 06:00 cron.
