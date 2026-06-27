"""Unit tests for the deterministic silver helpers: `clean` + `fingerprint`. With negatives."""
from jobfetcher.core.clean import clean
from jobfetcher.core.fingerprint import fingerprint


# --------------------------------------------------------------------------- clean
def test_clean_strips_html_tags_and_entities():
    assert clean("<p>Build <b>ETL</b> &amp; pipelines</p>") == "Build ETL & pipelines"


def test_clean_collapses_whitespace_and_normalizes_unicode():
    # NFKC folds the non-breaking space + the fullwidth chars; whitespace collapses to one space.
    raw = "Data Engineer\n\n  \tＳＱＬ"
    assert clean(raw) == "Data Engineer SQL"


def test_clean_handles_br_and_nested_entities():
    assert clean("line1<br/>line2 &amp;amp; more") == "line1 line2 & more"


def test_clean_strips_double_encoded_tags():
    # C6: a double-encoded tag (`&amp;lt;b&amp;gt;`) only becomes a real tag after a SECOND
    # unescape; the bounded unescape-until-stable loop removes it fully.
    assert clean("Build &amp;lt;b&amp;gt;ETL&amp;lt;/b&amp;gt; pipelines") == "Build ETL pipelines"


def test_clean_empty_and_none_return_empty():
    # negative: None / empty / whitespace-only in → "" out, never a crash.
    assert clean(None) == ""
    assert clean("") == ""
    assert clean("   \n\t ") == ""


# --------------------------------------------------------------------------- fingerprint
def test_fingerprint_is_stable_and_short():
    fp = fingerprint("Data Engineer", "Acme", "Riyadh")
    assert fp == fingerprint("Data Engineer", "Acme", "Riyadh")
    assert len(fp) == 16 and all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_normalizes_case_and_whitespace():
    # same job, different casing/spacing → same key (cross-board dedup).
    assert fingerprint("data  engineer", "ACME", " Riyadh ") == fingerprint(
        "Data Engineer", "Acme", "Riyadh"
    )


def test_fingerprint_distinguishes_different_jobs():
    # negative: a different company → a different fingerprint (no false collision).
    assert fingerprint("Data Engineer", "Acme", "Riyadh") != fingerprint(
        "Data Engineer", "Globex", "Riyadh"
    )


def test_fingerprint_tolerates_none_fields():
    # negative: missing company/location don't crash; None and "" normalize the same.
    assert fingerprint("Data Engineer", None, None) == fingerprint("Data Engineer", "", "")
