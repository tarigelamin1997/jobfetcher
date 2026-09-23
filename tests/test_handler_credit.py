"""Handler-level gate for INV-005: an empty LLM account is announced in the digest it already
sends, and a best-effort balance read forecasts it — without ever failing the run.

**Why these run the REAL client.** The dossier's failure is a chain: DeepSeek answers 402 →
`score_gold` counts `billing_blocked` → the handler must turn it into a banner → `notify` must
render it. Stubbing any link proves nothing about the chain. So only the network is faked: a
`urlopen` stand-in plays DeepSeek (`/chat/completions` and `/user/balance`), and the REAL
`OpenAICompatLlmClient`, `Scorer`, `score_gold`, `notify` and `render_digest` run end to end.
Nothing here reaches the network, a database or AWS.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import pytest

from jobfetcher.adapters import llm_openai
from jobfetcher.core.ingest import score_gold
from jobfetcher.core.profile import Profile
from tests.test_db_resume import _PROFILE_YML
from tests.test_db_resume import _wire_handler as _wire_every_mode
from tests.test_handler_cadence import _CountingSource, _wire
from tests.test_scorer import _dissected, _score_json

SENTINEL_KEY = "sk-SENTINEL-abcd1234"  # gitleaks:allow -- fake key; VG-g proves it never leaks
RUN_DAY = "2026-09-23"   # a non-fetch day (09-22 was the sweep)
_402_BODY = (
    '{"error":{"message":"Insufficient Balance","type":"unknown_error","param":null,'
    '"code":"invalid_request_error"}}'
)
# DeepSeek's real 401 shape: it echoes the key's last four characters (INV-005 Evidence 6).
_401_BODY = '{"error":{"message":"Authentication Fails, Your api key: ****1234 is invalid"}}'


@pytest.fixture
def pkg_logger_restored():
    logger = logging.getLogger("jobfetcher")
    before = logger.level
    yield
    logger.setLevel(before)


class _Resp:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _balance_body(usd: str, available: bool = True) -> bytes:
    return json.dumps({
        "is_available": available,
        "balance_infos": [{"currency": "USD", "total_balance": usd, "granted_balance": "0.00",
                           "topped_up_balance": usd}],
    }).encode()


class _FakeDeepSeek:
    """`urlopen`, playing DeepSeek. `broke` → every completion is HTTP 402; `balance` is the
    `/user/balance` behaviour: a USD string, or a callable that raises/returns per request."""

    def __init__(self, *, broke: bool = False, balance="9.71", available: bool = True) -> None:
        self.broke = broke
        self.balance = balance
        self.available = available
        self.balance_calls: list[float] = []   # the timeout of each balance read

    def __call__(self, req, timeout=0):
        path = urlsplit(req.full_url).path
        if path.endswith("/user/balance"):
            self.balance_calls.append(timeout)
            if callable(self.balance):
                return self.balance(req)
            return _Resp(_balance_body(self.balance, self.available))
        if path.endswith("/chat/completions"):
            if self.broke:
                raise urllib.error.HTTPError(
                    req.full_url, 402, "Payment Required", None, io.BytesIO(_402_BODY.encode())
                )
            reply = {"choices": [{"message": {"content": _score_json(80)},
                                  "finish_reason": "stop"}]}
            return _Resp(json.dumps(reply).encode())
        raise AssertionError(f"unexpected request to {path}")


def _http(code: int, body: str):
    def _raise(req):
        raise urllib.error.HTTPError(req.full_url, code, "err", None, io.BytesIO(body.encode()))

    return _raise


def _raising(exc: BaseException):
    def _raise(req):  # noqa: ARG001
        raise exc

    return _raise


class _Repo:
    """Just enough state for real scoring + a real notify across several days: pending gold
    candidates (left pending while 402s block them), and the send-once `run_log`."""

    engine = object()

    def __init__(self, candidates=()) -> None:
        self.candidates = list(candidates)
        self.scored: set[str] = set()
        self.sent_days: list[date] = []
        self.last_sent: datetime | None = None

    def upsert_profile(self, **kw):  # noqa: ARG002
        pass

    def get_profile(self, user_id):  # noqa: ARG002
        return {"profile": Profile.from_yaml_text(_PROFILE_YML).model_dump(),
                "threshold": 60, "hard_floor": 50, "near_miss_band": 10}

    def get_gold_candidates(self):
        return [c for c in self.candidates if c[0] not in self.scored]

    def save_score(self, **kw):
        return kw["cluster_id"]

    def mark_scored(self, posting_id):
        self.scored.add(posting_id)

    def was_digest_sent(self, *, user_id, run_date):  # noqa: ARG002
        return run_date in self.sent_days

    def mark_digest_sent(self, *, user_id, run_date, run_id):  # noqa: ARG002
        self.sent_days.append(run_date)
        self.last_sent = datetime(run_date.year, run_date.month, run_date.day, 6,
                                  tzinfo=timezone.utc)

    def get_last_digest_sent_at(self, *, user_id):  # noqa: ARG002
        return self.last_sent

    def get_scored_shortlist(self, *, threshold, since=None, max_age_days=None):  # noqa: ARG002
        return [], 0


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, subject, html_body, text_body, recipients):
        self.sent.append({"subject": subject, "html": html_body, "text": text_body,
                          "recipients": recipients})
        return "msg-1"


def _candidates(n: int):
    return [(f"p{i}", f"c{i}", _dissected(f"Data Engineer {i}")) for i in range(n)]


def _wire_credit(monkeypatch, tmp_path, deepseek, *, repo=None, ingest_counts=None,
                 score_counts=None):
    """`_wire`, with the REAL LLM client (network faked), real scoring and a real notify.
    `ingest_counts` / `score_counts` stub a stage when a test needs a count it cannot provoke."""
    import jobfetcher.handlers.capture as capture

    pipe = _wire(monkeypatch, tmp_path, _CountingSource())
    repo = repo if repo is not None else _Repo()
    outbox = _Outbox()
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", llm_openai.OpenAICompatLlmClient)
    monkeypatch.setattr(llm_openai.urllib.request, "urlopen", deepseek)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL_KEY)
    monkeypatch.setattr(pipe, "PostgresRepository", lambda url: repo)  # noqa: ARG005
    monkeypatch.setattr(pipe, "SesNotifier", lambda: outbox)
    monkeypatch.setattr(pipe, "S3ReportStore", lambda: None)
    monkeypatch.setattr(capture, "build_capture_link", lambda env: None)  # noqa: ARG005
    monkeypatch.setattr(pipe, "score_gold", score_gold)   # `_wire` stubbed it; score for real
    if ingest_counts is not None:
        monkeypatch.setattr(pipe, "ingest", lambda *a, **kw: dict(ingest_counts))  # noqa: ARG005
    if score_counts is not None:
        monkeypatch.setattr(pipe, "score_gold", lambda **kw: dict(score_counts))  # noqa: ARG005
    return pipe, repo, outbox


def _run(pipe, day: str = RUN_DAY, run_id: str = "credit01"):
    return pipe.handler({"run_date": day, "run_id": run_id}, None)


_OUT_OF_CREDIT = "⚠ The LLM account is out of credit"


def _credit_banner_in(mail: dict) -> bool:
    return "LLM account is out of credit" in mail["html"] and any(
        line.startswith(_OUT_OF_CREDIT) for line in mail["text"].splitlines()
    )


# ------------------------------------------------ VG-a: a blocked run sends, and leads with it
def test_a_blocked_run_sends_the_digest_and_it_leads_with_the_credit_banner(
    monkeypatch, tmp_path, pkg_logger_restored, caplog
):
    deepseek = _FakeDeepSeek(broke=True, balance="0.00")
    pipe, repo, outbox = _wire_credit(monkeypatch, tmp_path, deepseek, repo=_Repo(_candidates(3)))
    with caplog.at_level("WARNING"):
        out = _run(pipe)
    assert out["statusCode"] == 200 and out["partial"] is False
    assert out["score"]["billing_blocked"] == 3
    assert len(outbox.sent) == 1                                   # SENT, not skipped
    mail = outbox.sent[0]
    assert mail["subject"].startswith(
        "⚠ The LLM account is out of credit: 3 postings could not be processed (HTTP 402) | "
    )
    assert mail["subject"].count("⚠") == 1
    assert mail["text"].splitlines()[0].startswith(_OUT_OF_CREDIT)   # the FIRST line
    assert "Top up the DeepSeek account" in mail["html"]
    assert repo.sent_days == [date.fromisoformat(RUN_DAY)]         # mark_digest_sent ran
    assert out["llm_balance_usd"] == 0.0 and out["llm_balance_error"] is None
    assert "LLM CREDIT ALERT" in caplog.text


def test_a_clean_run_sends_an_ordinary_digest(monkeypatch, tmp_path, pkg_logger_restored):
    # VG-a negative: scoring works and the account is funded — the email must look ordinary.
    deepseek = _FakeDeepSeek(balance="9.71")
    pipe, repo, outbox = _wire_credit(monkeypatch, tmp_path, deepseek, repo=_Repo(_candidates(3)))
    out = _run(pipe)
    assert out["statusCode"] == 200
    assert out["score"]["scored"] == 3 and out["score"]["billing_blocked"] == 0
    mail = outbox.sent[0]
    assert "⚠" not in mail["subject"] and "⚠" not in mail["text"]
    assert "credit" not in mail["text"] and "#fce8e6" not in mail["html"]
    assert out["llm_balance_usd"] == 9.71 and out["llm_balance_error"] is None


# ------------------------------------------------ VG-b: dissection-only blocks count too
def test_a_dissection_only_block_banners(monkeypatch, tmp_path, pkg_logger_restored):
    ingest = {"fetched": 5, "silvered": 0, "skipped": 0, "deferred": 0, "billing_blocked": 5}
    pipe, _, outbox = _wire_credit(
        monkeypatch, tmp_path, _FakeDeepSeek(balance="9.71"), ingest_counts=ingest
    )
    assert _run(pipe)["statusCode"] == 200
    assert outbox.sent[0]["subject"].startswith(
        "⚠ The LLM account is out of credit: 5 postings could not be processed"
    )


def test_ordinary_dissection_failures_do_not_banner(monkeypatch, tmp_path, pkg_logger_restored):
    # VG-b negative: `skipped` is a per-item failure, not an empty account.
    ingest = {"fetched": 5, "silvered": 0, "skipped": 5, "deferred": 0, "billing_blocked": 0}
    pipe, _, outbox = _wire_credit(
        monkeypatch, tmp_path, _FakeDeepSeek(balance="9.71"), ingest_counts=ingest
    )
    assert _run(pipe)["statusCode"] == 200
    assert "⚠" not in outbox.sent[0]["subject"]


# ------------------------------------------------ VG-c: the balance read can never fail the run
_FAILED_READS = {
    "raises": (_raising(RuntimeError("socket exploded")), "error:RuntimeError"),
    "timeout": (_raising(urllib.error.URLError(TimeoutError("timed out"))), "timeout"),
    "non-json": (lambda req: _Resp(b"<html>502</html>"), "bad_json"),  # noqa: ARG005
    "no-usd": (lambda req: _Resp(json.dumps(  # noqa: ARG005
        {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "7"}]}
    ).encode()), "no_usd"),
    "http-401": (_http(401, _401_BODY), "http_401"),
    "http-404": (_http(404, "Not Found"), "http_404"),
}


@pytest.mark.parametrize("case", list(_FAILED_READS))
def test_a_failed_balance_read_is_recorded_and_never_a_false_alarm(
    monkeypatch, tmp_path, pkg_logger_restored, case
):
    behaviour, error = _FAILED_READS[case]
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, _FakeDeepSeek(balance=behaviour))
    out = _run(pipe)
    assert out["statusCode"] == 200
    assert out["llm_balance_usd"] is None and out["llm_balance_error"] == error  # null says why
    assert len(outbox.sent) == 1
    assert "⚠" not in outbox.sent[0]["subject"]              # no banner from a read error


@pytest.mark.parametrize("case", list(_FAILED_READS))
def test_a_blocked_run_still_banners_when_the_balance_read_fails(
    monkeypatch, tmp_path, pkg_logger_restored, case
):
    # VG-c: rule (a) never depends on rung 2.
    behaviour, _ = _FAILED_READS[case]
    deepseek = _FakeDeepSeek(broke=True, balance=behaviour)
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, deepseek, repo=_Repo(_candidates(2)))
    out = _run(pipe)
    assert out["statusCode"] == 200 and out["llm_balance_usd"] is None
    assert _credit_banner_in(outbox.sent[0])


def test_a_client_without_a_balance_read_records_why(monkeypatch, tmp_path, pkg_logger_restored):
    # VG-c: the port has no balance; a client without the capability reports `unsupported`.
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, _FakeDeepSeek(),
                                   score_counts={"billing_blocked": 0, "deferred": 0})
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", lambda cfg=None, **kw: object())  # noqa: ARG005
    out = _run(pipe)
    assert out["statusCode"] == 200
    assert out["llm_balance_usd"] is None and out["llm_balance_error"] == "unsupported"
    assert len(outbox.sent) == 1 and "⚠" not in outbox.sent[0]["subject"]


# ------------------------------------------------ VG-d: the threshold, through the handler
@pytest.mark.parametrize(
    ("usd", "available", "expect"),
    [
        ("1.99", True, "⚠ LLM credit low: $1.99 left (≈128 postings) | "),
        ("0.00", True, "⚠ The LLM account is out of credit ($0.00 left) | "),
        ("-0.30", True, "⚠ The LLM account is out of credit (-$0.30 left) | "),
        ("4.00", False, "⚠ The LLM account is out of credit (DeepSeek reports it unavailable)"),
        ("2.00", True, None),
        ("9.71", True, None),
    ],
)
def test_the_balance_decides_the_banner(
    monkeypatch, tmp_path, pkg_logger_restored, usd, available, expect
):
    deepseek = _FakeDeepSeek(balance=usd, available=available)
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, deepseek)
    assert _run(pipe)["statusCode"] == 200
    subject = outbox.sent[0]["subject"]
    if expect is None:
        assert "⚠" not in subject                 # $2.00 is AT the threshold, not below it
    else:
        assert subject.startswith(expect)


# ------------------------------------------------ VG-f: it persists while blocked, clears after
def test_the_banner_persists_every_day_until_a_top_up_then_clears(
    monkeypatch, tmp_path, pkg_logger_restored
):
    repo = _Repo(_candidates(2))
    deepseek = _FakeDeepSeek(broke=True, balance="0.00")
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, deepseek, repo=repo)

    out1 = _run(pipe, "2026-09-23", "day1")                   # blocked work → rule (a)
    held = list(repo.candidates)
    repo.candidates = []                                      # day 2: nothing left to retry...
    out2 = _run(pipe, "2026-09-24", "day2")
    repo.candidates = held
    out3 = _run(pipe, "2026-09-25", "day3")                   # a fetch day, still blocked
    assert (out1["score"]["billing_blocked"], out2["score"]["billing_blocked"],
            out3["score"]["billing_blocked"]) == (2, 0, 2)
    assert [_credit_banner_in(m) for m in outbox.sent] == [True, True, True]
    assert "($0.00 left)" in outbox.sent[1]["subject"]        # ...so rule (b) carried it

    deepseek.broke, deepseek.balance = False, "10.00"         # the top-up
    out4 = _run(pipe, "2026-09-26", "day4")
    assert out4["score"]["scored"] == 2 and out4["score"]["billing_blocked"] == 0
    assert "⚠" not in outbox.sent[3]["subject"]               # cleared

    deepseek.balance = "1.50"                                 # a small top-up is still low
    _run(pipe, "2026-09-27", "day5")
    assert outbox.sent[4]["subject"].startswith("⚠ LLM credit low: $1.50 left")
    assert len(outbox.sent) == 5


# ------------------------------------------------ VG-g: the key never leaks
@pytest.mark.parametrize(
    "balance",
    [_http(401, _401_BODY), _raising(urllib.error.URLError(TimeoutError("timed out")))],
    ids=["401-echoes-the-key", "timeout"],
)
def test_the_api_key_never_reaches_a_log_the_summary_or_the_email(
    monkeypatch, tmp_path, pkg_logger_restored, caplog, balance
):
    deepseek = _FakeDeepSeek(broke=True, balance=balance)
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, deepseek, repo=_Repo(_candidates(2)))
    with caplog.at_level("DEBUG"):
        out = _run(pipe)
    assert out["statusCode"] == 200 and deepseek.balance_calls   # the read really happened
    mail = outbox.sent[0]
    assert _credit_banner_in(mail)                                 # something WAS rendered
    surfaces = {
        "logs": caplog.text,
        "summary": json.dumps(out, default=str),
        "subject": mail["subject"], "html": mail["html"], "text": mail["text"],
    }
    for where, blob in surfaces.items():
        assert "SENTINEL" not in blob, where
        assert "1234" not in blob, where


# ------------------------------------------------ VG-h: partial + send-once are untouched
def test_a_partial_blocked_run_still_skips_notify_and_the_next_run_sends_once(
    monkeypatch, tmp_path, pkg_logger_restored
):
    deepseek = _FakeDeepSeek(balance="0.00")
    pipe, repo, outbox = _wire_credit(monkeypatch, tmp_path, deepseek,
                                      score_counts={"deferred": 2, "billing_blocked": 4})
    out = _run(pipe, run_id="partial")
    assert out["partial"] is True and outbox.sent == []            # unchanged: no send
    assert out["llm_balance_usd"] == 0.0                           # ...but the reading is kept

    monkeypatch.setattr(pipe, "score_gold",
                        lambda **kw: {"deferred": 0, "billing_blocked": 4})  # noqa: ARG005
    out = _run(pipe, run_id="resume")
    assert out["partial"] is False and len(outbox.sent) == 1
    assert _credit_banner_in(outbox.sent[0])

    out = _run(pipe, run_id="again")                               # same run_date
    assert out["notify"]["sent"] == 0 and len(outbox.sent) == 1   # never twice
    assert out["llm_balance_usd"] == 0.0                           # recorded on a skip too


# ------------------------------------------------ VG-i: once per daily run, never in smoke/reassess
class _BalanceSpy:
    reads: list[str] = []

    def __init__(self, cfg=None, **kw) -> None:  # noqa: ARG002
        self.model = cfg.model if cfg is not None else None

    def read_balance(self):
        _BalanceSpy.reads.append(self.model)
        return llm_openai.LlmBalance(9.71, True, None)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"mode": "smoke"}, []),
        ({"mode": "reassess"}, []),
        ({}, ["deepseek-v4-pro"]),                  # exactly once, on the SCORING account
    ],
    ids=["smoke", "reassess", "daily"],
)
def test_the_balance_is_read_once_per_daily_run_and_never_in_smoke_or_reassess(
    monkeypatch, tmp_path, pkg_logger_restored, event, expected
):
    pipe = _wire_every_mode(monkeypatch, tmp_path, [])
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", _BalanceSpy)
    _BalanceSpy.reads = []
    out = pipe.handler({"run_date": RUN_DAY, **event}, None)
    assert out["statusCode"] == 200
    assert _BalanceSpy.reads == expected
    if event.get("mode") == "smoke":
        assert "llm_balance_usd" not in out                      # smoke's shape is unchanged


# ------------------------------------------------ the handler's own safety net (Examiner S1, S2, N3)
class _RaisingBalance:
    def __init__(self, cfg=None, **kw) -> None:  # noqa: ARG002
        pass

    def read_balance(self):
        raise RuntimeError("an adapter that broke its never-raises contract")


class _JunkBalance(_RaisingBalance):
    def read_balance(self):
        return {"usd": 1.0}     # not an LlmBalance


@pytest.mark.parametrize(
    ("client", "error"),
    [(_RaisingBalance, "error:RuntimeError"), (_JunkBalance, "error:TypeError")],
    ids=["raises", "not-a-reading"],
)
def test_a_misbehaving_balance_read_never_fails_the_run(
    monkeypatch, tmp_path, pkg_logger_restored, client, error
):
    # The adapter promises never to raise; the handler must not depend on that promise.
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, _FakeDeepSeek(),
                                   score_counts={"billing_blocked": 0, "deferred": 0})
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", client)
    out = _run(pipe)
    assert out["statusCode"] == 200
    assert out["llm_balance_usd"] is None and out["llm_balance_error"] == error
    assert len(outbox.sent) == 1 and "⚠" not in outbox.sent[0]["subject"]


def test_the_balance_is_read_after_scoring(monkeypatch, tmp_path, pkg_logger_restored):
    # B-10's settlement lag: a read BEFORE scoring misses today's spend — a full cycle late.
    events: list[str] = []

    class _Spy(_RaisingBalance):
        def read_balance(self):
            events.append("balance")
            return llm_openai.LlmBalance(9.71, True, None)

    pipe, _, _ = _wire_credit(monkeypatch, tmp_path, _FakeDeepSeek())
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", _Spy)
    monkeypatch.setattr(pipe, "score_gold", lambda **kw: events.append("score") or  # noqa: ARG005
                        {"billing_blocked": 0, "deferred": 0})
    assert _run(pipe)["statusCode"] == 200
    assert events == ["score", "balance"]


def test_a_failing_credit_rule_still_sends_the_digest(
    monkeypatch, tmp_path, pkg_logger_restored, caplog
):
    def _boom(*a, **kw):  # noqa: ARG001
        raise ValueError("credit rule blew up")

    deepseek = _FakeDeepSeek(broke=True, balance="0.00")
    pipe, _, outbox = _wire_credit(monkeypatch, tmp_path, deepseek, repo=_Repo(_candidates(1)))
    monkeypatch.setattr(pipe, "credit_problem", _boom)
    with caplog.at_level("WARNING"):
        out = _run(pipe)
    assert out["statusCode"] == 200 and len(outbox.sent) == 1
    assert not _credit_banner_in(outbox.sent[0])
    assert "could not determine LLM credit" in caplog.text
    assert "PIPELINE_ALARM" not in caplog.text
