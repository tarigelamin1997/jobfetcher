# ADR-0006 — CV rendering without LibreOffice-in-Lambda

## Status
Accepted

## Context
The original design generated a DOCX with python-docx, then converted to PDF via a **LibreOffice Lambda Layer** (~250 MB). This was the design's **#1 reliability risk** (heavy layer, fragile headless conversion). Reliability is one of the two north stars, and CV tailoring is a core value. We need ATS-clean DOCX (editable) + PDF (submission-ready), deterministically, without the fragility.

## Decision
Render from one **structured content model** to **DOCX via python-docx** + **PDF via a pure-Python/HTML path** (e.g. WeasyPrint, or headless Chromium) — **no LibreOffice-in-Lambda**. Bedrock decides *what to say*; the renderer decides *how it looks* (layout 100% deterministic). Keep the *tarig-cv* template as the refined base. **One master CV.** Every CV is a **draft** pending a human-review gate before "submission-ready"; strict no-fabrication honesty rules.

## Alternatives Considered
- **python-docx + LibreOffice-layer PDF (original).** Rejected: the main failure mode; heavy, fragile, slow cold starts.
- **Direct structured-JSON → PDF (reimplement layout).** Rejected: reimplementing a layout engine is unnecessary complexity.
- **DOCX-only (skip PDF).** Rejected: ATS uploads expect PDF; PDF is the submission format.
- **A managed DOCX→PDF service.** Possible, but adds an external dependency; the pure-Python/HTML path keeps it self-contained and reliable.

## Consequences
- **Easier:** far more reliable rendering; trivial restyling (HTML/CSS); no 250 MB layer.
- **Harder:** keeping DOCX and HTML/PDF visually consistent requires one shared content model (which we want anyway).
- **Impact:** arrives in migration **M1** (CV tailoring); the draft→approved review gate doubles as the **scoring-calibration capture** surface.
