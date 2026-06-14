# ADR-0005 — Deduplication: cluster-and-surface, never hide

## Status
Accepted

## Context
Pulling from multiple job APIs surfaces the same role 2–4× with slightly different titles/company-spellings. Tarig wanted near-perfect dedup ("99.99%"). But dedup is a classifier with two error types: a **missed duplicate** (trivial cost — one extra cheap score) and a **wrong merge** (the dangerous error — a *real job gets hidden*). A literal 99.99% guarantee on fuzzy job-text matching is not an honest claim. Also relevant: applying via different platforms can help (hiring teams sometimes favor one platform's pipeline), so collapsing to one listing can *cost* an opportunity.

## Decision
**Cluster-and-surface, never hide.** Group suspected-same postings into a **cluster** and surface the *whole group* with every platform's apply-link + a "suspected same as X/Y/Z" note; the **user decides** whether to apply once or several times. Uncertain clusters go to a dedicated **Suspected-Duplicates** surface to confirm/split (machine proposes, user disposes). Engineering: precision-first + fail-safe + **measured precision/recall**; signals = exact-id → fingerprint → (embeddings via pgvector + apply-URL + company-canonicalization + time-window); ambiguous → LLM adjudication → UNSURE never auto-merges. **Score + tailor a CV once per cluster**, but never hide an apply-link.

## Alternatives Considered
- **Pick one canonical + hide the duplicates (the original design).** Rejected: a wrong merge silently removes a real job — the one unacceptable failure. Also discards the "apply via the best platform" option.
- **Aggressive auto-merge for a clean shortlist.** Rejected: optimizes the wrong metric (tidiness) at the cost of recall of *opportunities*.
- **Promise ~99.99% accuracy.** Rejected: dishonest for fuzzy matching; we report *measured* precision/recall instead.

## Consequences
- **Easier:** never lose a job; "found on N platforms = hot signal" falls out of cluster size; entity resolution with real precision/recall numbers is a strong, honest DE showcase.
- **Harder:** more UI surface (a Suspected-Duplicates view) and a measurement/labeling loop to maintain.
- **Impact:** drives the cluster-centric data model ([02-architecture](../02-architecture.md)); scoring/CV attach to clusters, not postings. Arrives in migration M2 (with the second source that creates the duplicate problem).
