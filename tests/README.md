# Tests — the v0 pyramid + validation-gate map

> The test suite is the **negative-case engine for the [v0 validation gate](../docs/04-v0-build-plan.md#v0-validation-gate-behavioral--negative--a-presence-check-is-no-gate)**: every gate is *behavioral* and carries a *negative* case (a presence/liveness check is no gate). This file maps the gates (VG1–VG8) to the tests that enforce them, lists the pyramid layers, and shows how to run each.

**Current state:** **272 unit + 31 integration (+5 live-key skips)** tests · `ruff` clean · **~95% coverage** (full suite, 94.76% measured 2026-07-07; the CI floor is 85% — the integration tests cover the `Repository`/handler DB paths).

## The pyramid + how to run

| Layer | What | Needs | Command |
|---|---|---|---|
| **Unit** | pure logic — normalization, fingerprint, score parsing, threshold routing, email rendering, the `SearchSpec`/`Profile` contracts, the LLM-client payload, the handler helpers. LLM/DB/AWS all faked. | nothing | `python -m pytest -m "not integration" -q` |
| **Coverage** | the unit measurement (CI-ready; [pyproject](../pyproject.toml) `[tool.coverage]`) | nothing | `python -m pytest -m "not integration" --cov=src/jobfetcher --cov-report=term -q` |
| **Integration** | the orchestrators + handler against **real local Postgres** + **moto** (S3, SES); LLM faked. ([ADR-0018](../docs/adr/0018-persistence-sqlalchemy-data-api-repository.md) — LocalStack can't mock the Aurora Data API, so the DB is real.) | local Postgres + moto | `docker compose up -d` → `JOBFETCHER_DB_URL=postgresql+psycopg2://jobfetcher:jobfetcher@127.0.0.1:5433/jobfetcher python -m pytest -m integration -q` → `docker compose stop` |
| **Live** | real **DeepSeek** end-to-end (dissect/score, determinism). **Skips automatically without a key.** | `$DEEPSEEK_API_KEY` (or Secrets Manager `jobfetcher/deepseek`) | runs within the integration command above when a key resolves |
| **Live smoke** | one real end-to-end run against **deployed** infra | deployed stack (Step 10) | manual Lambda invoke (Step 10) |

> `docker compose up -d` is only needed for the **integration** layer. Default development (code, unit tests, coverage, docs) needs no Docker.

## Validation gates → tests (positive + negative)

| Gate | Positive | Negative | Enforced |
|---|---|---|---|
| **VG1 — Ingestion** | `test_integration_ingest.py::test_fetch_to_bronze_lands_s3_and_rows` | `test_ingest.py::test_fetch_to_bronze_skips_jobs_without_id` · `::test_land_silver_skips_on_dissection_error` · `test_jsearch_source.py::test_fetch_malformed_data_shapes_dont_crash` · `test_search_spec.py::test_malformed_iso2_country_is_loud` *(config-contract layer)* | ✅ |
| **VG2 — Scoring is behavioral** | `test_scorer.py::test_vg2_high_score_is_strong_fit_with_explanation` · live `test_live_scorer.py::test_live_score_real_jd` | `test_scorer.py::test_vg2_low_score_is_misaligned` (misaligned JD → below floor) | ✅ |
| **VG3 — Determinism (best-effort)** | live `test_live_scorer.py::test_live_score_is_deterministic` (asserts temp 0 configured; logs the delta — *not gated*, [Step 5 VG3 decision](../docs/04-v0-build-plan.md)) | `test_llm_openai.py::test_temperature_from_config_is_in_request_payload` — **CI-enforceable offline**: the client must send the *configured* temperature in its request body (catches a temp-≠-0 regression without a key) | ✅ |
| **VG4 — Idempotency** | `test_integration_handler.py::test_handler_end_to_end_then_idempotent` (two runs, same date → identical rows, ≤1 email) | `::test_handler_crash_mid_run_then_resume_sends_once` · `::test_handler_send_failure_not_double_marked_then_retry_sends_once` | ✅ |
| **VG5 — Notification** | `test_notifier.py::test_render_digest_matches_carry_core_fields` · `::test_notify_sends_digest_with_surfaced_jobs_and_below_count` | `::test_render_digest_zero_matches_is_valid_no_matches_email` · `::test_notify_zero_matches_still_sends`; injection negatives `::test_render_digest_rejects_javascript_scheme_apply_url` · `::test_render_digest_attribute_breakout_apply_url_is_escaped` | ✅ |
| **VG6 — Teardown** | `terraform destroy` → ~$0 | (N/A — destroy is the negative of apply) | 🏗️ infra gate (validated at C-3; re-checked at Step 10) — *not a pytest* |
| **VG7 — Secrets hygiene** | gitleaks scan passes clean on the tree | a planted realistic fake key (an OpenAI- or AWS-style token) is **detected + blocked** (verified at Step 9) | ✅ enforced — pre-commit `gitleaks` hook ([`.pre-commit-config.yaml`](../.pre-commit-config.yaml)) + the CI `secret-scan` job ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) |
| **VG8 — Threshold is config** | `test_scorer.py::test_vg8_threshold_60_splits_in_between` | `::test_vg8_threshold_0_surfaces_all` · `::test_vg8_threshold_above_all_surfaces_none`; the `SearchSpec`/`Profile` contract tests guard the config layer | ✅ |

## Post-v0 unit gates → tests (same standard: behavioral + negative)

Capability units after the v0 gate map add their own positive + negative pairs here as they land.

| Unit | Positive | Negative | Enforced |
|---|---|---|---|
| **score_event lineage (migration 0004 — dual-write + backfill; [ADR-0025](../docs/adr/0025-score-event-lineage.md), unreleased)** | `test_integration_score_event.py::test_three_scorings_three_events_score_holds_current_plus_previous` (three scorings → three appended events; `score` stays the 1:1 current view) · `test_integration_migration_0004.py::test_upgrade_0004_backfills_existing_scores_into_the_event_log` (the `'pre-0004'` baseline backfill rescues existing scores; hollow rows skipped) | `test_integration_score_event.py::test_failed_event_insert_rolls_back_the_score_upsert` · `::test_failed_score_upsert_writes_no_event` (the dual-write is ONE transaction — either failure rolls back both, current view and history never diverge) | ✅ |
| **reassess age bound (`get_scored_for_reassess(max_age_days)`; [ADR-0025](../docs/adr/0025-score-event-lineage.md), unreleased)** | `test_integration_score_event.py::test_age_filter_bites_on_live_shaped_data_and_keeps_null_safety` (ages by `COALESCE(posting, bronze).fetched_at` on live-shaped data — the bound actually bites; unknown-age rows INCLUDED, never dropped forever) | `test_integration_score_event.py::test_age_filter_zero_and_none_are_unbounded` (`0`/`None` emit the pre-0004 unbounded query — no behavior drift) | ✅ |

## Unit-pyramid items (build-plan Step 8)

| Item | Tests |
|---|---|
| **normalization** (`core/clean`) | `test_clean_fingerprint.py::test_clean_*` (5 — HTML/entities, unicode+whitespace, double-encoded, None/empty) |
| **fingerprint** (`core/fingerprint`) | `test_clean_fingerprint.py::test_fingerprint_*` (4) + `test_ingest.py::test_fingerprint_is_independent_of_llm_normalized_title` (stability across model versions) |
| **score-output parsing** (`core/scorer`) | `test_scorer.py::test_score_result_*` (range / missing / extra-keys) + `::test_scorer_retries_then_succeeds` · `::test_scorer_bad_json_after_retry_raises` · `::test_scorer_missing_field_raises` |
| **threshold routing** (`derive_fit_category`) | `test_scorer.py::test_derive_fit_category_bands` (parametrized) + `::test_derive_fit_category_stretch_band` + the VG8 trio |
| **email rendering** (`core/notifier.render_digest`) | `test_notifier.py::test_render_digest_*` (10 — core fields, singular wording, zero-matches, HTML-escape, scheme/attribute injection, None/empty) |

## Conventions

- **Markers:** integration tests carry `@pytest.mark.integration`; everything else is unit. Live tests `pytest.importorskip`/skip without a DeepSeek key.
- **Fakes:** `tests/helpers.py` (`FakeLlm`, sample-payload helpers); moto for S3/SES; a real local Postgres for the DB (no LocalStack for the Data API).
- **No retries to paper over flakiness** (build-plan Step 8 FAILURE-MODE) — a flaky integration test gets its root cause fixed.
