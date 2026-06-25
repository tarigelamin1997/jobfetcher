"""The silver `Dissector` (ADR-0016): one JD -> a grounded, structured `DissectedPosting`.

Accuracy levers built in now (the "basics in, measure, then tighten" decision, plan §30):
  - metadata-split: deterministic fields come from `PostingMetadata`; the LLM only does the
    free-text part (skills + levels, sector, normalized title).
  - grounding: every skill must quote a JD phrase (`evidence`); `grounding_check` drops any
    skill whose evidence is not actually in the JD — a cheap, deterministic guard against the
    hallucinated-stack failure that thin JDs invite.
  - tight level definitions + a worked example + temperature 0 (in the prompt).

Deferred (a fast-follow once the full sweep yields enough JDs to hand-label): measured
precision/recall, a second-pass verifier, a canonicalization dictionary, stronger-model
escalation per field.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .models import DissectedPosting, LlmExtraction, PostingMetadata, Skill
from .ports import LlmClient

DISSECTION_SYSTEM_PROMPT = """\
You extract structured data from a job description. Return ONLY a single JSON object — no \
prose, no explanation, no markdown fences.

Extract exactly these fields:
- "skills": a list of objects, each {"name": <short skill / technology / competency>, \
"level": "must" | "nice" | "implied", "evidence": <the exact phrase from the job \
description that supports this skill>}.
- "sector": the company's industry / domain if stated or clearly implied, else null \
(e.g. "fintech", "e-commerce", "consulting", "healthcare").
- "normalized_title": the core role title with marketing fluff and seniority stripped \
(e.g. "Senior Data Engineer - Build Scalable Pipelines" -> "Data Engineer").

Rules (critical — accuracy depends on these):
1. Extract ONLY skills explicitly present or directly implied BY THE TEXT. Do NOT add \
skills you merely expect for this kind of role. If the description is generic and names no \
concrete tools, return few or no skills — that is correct, not a failure.
2. "evidence" MUST be an exact substring of the job description. If you cannot point to a \
phrase, do not include the skill.
3. level: "must" = explicitly required / essential / a stated minimum; "nice" = preferred / \
a plus / a bonus / advantageous; "implied" = not stated outright but directly entailed by a \
stated responsibility (use this sparingly).
4. Output valid JSON and nothing else.

Example:
Job description: "Required: 3+ years with Python and SQL. Experience with Airflow is a plus. \
You will build ETL pipelines on AWS."
{"skills":[{"name":"Python","level":"must","evidence":"Required: 3+ years with Python and SQL"},\
{"name":"SQL","level":"must","evidence":"Required: 3+ years with Python and SQL"},\
{"name":"Airflow","level":"nice","evidence":"Experience with Airflow is a plus"},\
{"name":"AWS","level":"must","evidence":"build ETL pipelines on AWS"},\
{"name":"ETL","level":"implied","evidence":"You will build ETL pipelines"}],\
"sector":null,"normalized_title":"Data Engineer"}"""


class DissectionError(Exception):
    """The LLM output could not be parsed/validated into a `DissectedPosting`."""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def grounding_check(jd_text: str, skills: list[Skill]) -> tuple[list[Skill], list[Skill]]:
    """Split skills into (grounded, ungrounded). A skill is grounded iff its `evidence`
    appears in the JD (whitespace- and case-insensitively). This is the anti-hallucination
    guard: a skill the model invented will quote evidence that isn't in the text."""
    jd = _normalize(jd_text)
    grounded: list[Skill] = []
    ungrounded: list[Skill] = []
    for s in skills:
        (grounded if _normalize(s.evidence) in jd else ungrounded).append(s)
    return grounded, ungrounded


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of the model's reply (tolerates code fences and stray
    surrounding prose, e.g. a thinking model that narrates before the JSON)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise DissectionError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(t[start : end + 1])


def _user_prompt(jd_text: str, metadata: PostingMetadata) -> str:
    return f"Job title: {metadata.raw_title}\n\nJob description:\n{jd_text}"


class Dissector:
    """Dissects one JD into a `DissectedPosting`. Pure: JD text + metadata in, contract out —
    no storage, no fetching (those are later build steps)."""

    def __init__(self, llm: LlmClient, *, model_id: str = "") -> None:
        self.llm = llm
        self.model_id = model_id  # provenance label; falls back to the client's config model

    def dissect(self, jd_text: str, metadata: PostingMetadata) -> DissectedPosting:
        if not jd_text or not jd_text.strip():
            raise DissectionError("empty job description")

        extraction = self._extract(jd_text, metadata)
        grounded, ungrounded = grounding_check(jd_text, extraction.skills)

        model = self.model_id or getattr(getattr(self.llm, "config", None), "model", "") or "unknown"
        return DissectedPosting(
            raw_title=metadata.raw_title,
            language=metadata.language,
            location=metadata.location,
            city=metadata.city,
            country=metadata.country,
            employment_type=metadata.employment_type,
            seniority=metadata.seniority,
            normalized_title=extraction.normalized_title,
            sector=extraction.sector,
            skills=grounded,
            model=model,
            dropped_skill_count=len(ungrounded),
        )

    def _extract(self, jd_text: str, metadata: PostingMetadata) -> LlmExtraction:
        user = _user_prompt(jd_text, metadata)
        last_err: Exception | None = None
        for attempt in range(2):  # one parse/validate retry
            system = DISSECTION_SYSTEM_PROMPT
            if attempt == 1:
                system += (
                    "\n\nYour previous reply was not valid JSON for the schema above. "
                    "Return ONLY the JSON object."
                )
            raw = self.llm.complete(system=system, user=user)
            try:
                return LlmExtraction.model_validate(_extract_json(raw))
            except (DissectionError, json.JSONDecodeError, ValidationError) as e:
                last_err = e
        raise DissectionError(f"no valid extraction after one retry: {last_err}")
