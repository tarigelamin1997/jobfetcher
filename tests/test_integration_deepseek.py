"""Live DeepSeek dissection — the C-1 validation gate on a REAL probe JD.

Run with:  pytest -m integration
Needs the DeepSeek key (Secrets Manager `jobfetcher/deepseek` or $DEEPSEEK_API_KEY) + balance.
Skipped automatically if the gitignored probe fixture isn't present.
"""
from __future__ import annotations

import pytest

from jobfetcher.adapters.llm_openai import OpenAICompatLlmClient
from jobfetcher.core.dissector import Dissector
from tests.helpers import load_probe

pytestmark = pytest.mark.integration

# Concrete tools that do NOT appear in the thin `sample_sa_en` JD — must not be invented (VG-b).
_ABSENT_TOOLS = {"python", "sql", "spark", "airflow", "kafka", "snowflake", "aws", "java", "scala", "hadoop"}


def test_dissect_real_thin_jd_live(capsys):
    jd_text, meta = load_probe("sample_sa_en.json")
    out = Dissector(OpenAICompatLlmClient()).dissect(jd_text, meta)

    # VG-a: a valid contract, metadata carried through, provenance recorded.
    assert out.language == "en"
    assert out.normalized_title.strip()
    assert out.model
    # VG-b: this JD names zero concrete tools -> none of the kept skills may be a tool absent from it.
    invented = {s.name.lower() for s in out.skills} & _ABSENT_TOOLS
    assert not invented, f"hallucinated tools not present in the JD: {invented}"
    # every kept skill is grounded by construction (grounding_check ran); show what was dropped.
    with capsys.disabled():
        print(
            f"\n[live dissect] title='{out.normalized_title}' sector={out.sector} "
            f"lang={out.language} seniority={out.seniority} "
            f"skills={[(s.name, s.level.value) for s in out.skills]} "
            f"dropped={out.dropped_skill_count} model={out.model}"
        )


def test_dissect_detailed_jd_live(capsys):
    """A detailed JD yields a rich, fully-grounded contract (the opposite of the thin case).

    Note: all 3 current probe fixtures happen to be English, so the true non-English path
    (VG-c in the spec) is NOT exercised here — it's deferred until the full JSearch sweep
    yields a non-English JD. The `language` field is still carried from metadata.
    """
    jd_text, meta = load_probe("sample_sa.json")
    out = Dissector(OpenAICompatLlmClient()).dissect(jd_text, meta)
    assert out.normalized_title.strip() and out.model
    assert out.skills, "a detailed JD should yield at least some grounded skills"
    assert all(s.evidence.strip() for s in out.skills)  # kept skills carry evidence
    with capsys.disabled():
        print(
            f"\n[live dissect/detailed] title='{out.normalized_title}' "
            f"skills={[(s.name, s.level.value) for s in out.skills]} dropped={out.dropped_skill_count}"
        )
