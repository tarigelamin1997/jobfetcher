#!/usr/bin/env python
"""Doc audit — the gate that keeps the repo honest about itself.

WHAT: five checks over the markdown in this repo, run by CI on every PR.
      (1) every internal link resolves
      (2) no unresolvable `plan §NN` citations
      (3) every *guarded* prose count agrees with the command that produces it
      (4) no stale "not yet deployed" claim on a shipped decision
      (5) mermaid edge labels are quoted (an unquoted one renders as a red
          error box on GitHub — it hit the README's own architecture diagram)

WHY:  the project's first pillar is "the repo is the memory: any session resumes
      from these files alone." That was measurably false — see ERR-016. The debt
      had been cleared twice in two days and regenerated both times, because
      nothing FAILED when a doc went stale. Facts were copy-pasted across up to
      12 files with no owner, and derived counts were typed as prose literals,
      correct only at the instant of writing.

SO-WHAT: check (3) is the one that matters. It converts "remember to re-count the
      tests after you add some" from a recurring manual chore into a build
      failure. The project's second pillar is "a standard not wired into a
      command is a suggestion" — and ERR-015 sharpened it: WIRING IS NECESSARY
      AND NOT SUFFICIENT, IT HAS TO BE ARMED. This runs in the `lint-and-test`
      job, which is a required status check on a branch protected with
      `enforce_admins: true`, so a stale count blocks the merge for everyone
      including the repo owner.

READ-ONLY. This script never writes to the tree. It reads `terraform/` and
`src/` to count things and must never modify them.

Usage:
    python scripts/check_docs.py           # exit 0 = clean, 1 = failures
    python scripts/check_docs.py --list    # print current ground-truth values

Marking a number as guarded — wrap it in an HTML comment naming its fact:

    Tests: <!--fact:tests-->549<!--/fact--> collected

HTML comments do not render, so the prose reads normally on GitHub. Any number
NOT wrapped is unguarded and this script ignores it; wrap the ones that matter.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252 and mangle this output (and would crash on
# characters outside it). Force utf-8 so local runs read the same as CI.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# Files whose broken links are deliberate. `_TEMPLATE.md` ships placeholder
# targets (`../../adr/00NN-...`) that cannot resolve until a real case is filled
# in — "fixing" them would turn the template into a broken example.
LINK_ALLOWLIST = {"docs/investigations/_TEMPLATE.md"}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
FACT_RE = re.compile(r"<!--fact:([a-z_]+)-->\s*([0-9][0-9,]*)\s*<!--/fact-->")
PLAN_RE = re.compile(r"plan §\d")


def md_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.md") if ".git" not in p.parts)


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


# ── ground truth ─────────────────────────────────────────────────────────────
# Each fact is (description, callable). The callable runs the real command; its
# result is what the prose must agree with. Add a fact here, then wrap the
# number in the prose. Keep these cheap — they run on every PR.


def _pytest_count(marker: str | None = None) -> int:
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    if marker:
        cmd += ["-m", marker]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout
    # "549 tests collected in 1.8s"  or  "482/549 tests collected (67 deselected)"
    m = re.search(r"(?:^|\n)(\d+)(?:/\d+)? tests? collected", out)
    if not m:
        raise RuntimeError(f"could not parse pytest collection output:\n{out[-500:]}")
    return int(m.group(1))


def _tf_resources() -> int:
    return sum(
        len([ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("resource ")])
        for f in sorted((ROOT / "terraform").glob("*.tf"))
    )


def _releases() -> int:
    # Counted from the CHANGELOG, not `git tag` — CI checks out shallow without
    # tags, and per the ownership table the CHANGELOG owns release narrative.
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return len([ln for ln in text.splitlines() if ln.startswith("## [v")])


def _adrs() -> int:
    return len(list((ROOT / "docs" / "adr").glob("0*.md")))


def _errors() -> int:
    # The log opens with a blank `### ERR-NNN — <short description>` template row
    # that is not an incident; subtract it.
    text = (ROOT / "docs" / "ledgers" / "errors.md").read_text(encoding="utf-8")
    headers = [ln for ln in text.splitlines() if ln.startswith("### ERR-")]
    return len([h for h in headers if not h.startswith("### ERR-NNN")])


FACTS: dict[str, tuple[str, object]] = {
    "tests": ("pytest --collect-only -q", lambda: _pytest_count()),
    "tests_unit": ('pytest -m "not integration" --collect-only -q', lambda: _pytest_count("not integration")),
    "tests_integration": ('pytest -m integration --collect-only -q', lambda: _pytest_count("integration")),
    "tf_resources": ("grep -c '^resource ' terraform/*.tf", _tf_resources),
    "releases": ("grep -c '^## \\[v' CHANGELOG.md", _releases),
    "adrs": ("ls docs/adr/0*.md | wc -l", _adrs),
    "errors": ("grep -c '^### ERR-' docs/ledgers/errors.md, minus the template row", _errors),
}


# ── checks ───────────────────────────────────────────────────────────────────


def check_links() -> list[str]:
    """Every relative link target must exist on disk."""
    bad = []
    for f in md_files():
        if rel(f) in LINK_ALLOWLIST:
            continue
        for m in LINK_RE.finditer(f.read_text(encoding="utf-8", errors="replace")):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (f.parent / target).resolve().exists():
                bad.append(f"{rel(f)} → {target}  (target does not exist)")
    return bad


def check_plan_refs() -> list[str]:
    """`plan §NN` must not appear at all — it never resolved.

    28 of these cited a planning document (Category D / ERR-016). §12–§29 are in
    the repo as docs/session-log/working-document.md and are now linked as
    `working-document §NN`; §30+ were never committed and are now re-pointed to
    their in-repo successor, or annotated as unrecoverable. Either way the string
    `plan §NN` is gone, so any reappearance is a regression — an unresolvable
    pointer being reintroduced.
    """
    bad = []
    for f in md_files():
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if PLAN_RE.search(line):
                bad.append(
                    f"{rel(f)}:{i} — 'plan §NN' citation; that document is not in the repo. "
                    "Cite 'working-document §NN' (§12–§29) or its in-repo successor."
                )
    return bad


def check_facts() -> list[str]:
    """Guarded prose counts must equal what the ground-truth command returns."""
    found: dict[str, list[tuple[str, int]]] = {}
    unknown = []
    for f in md_files():
        for m in FACT_RE.finditer(f.read_text(encoding="utf-8", errors="replace")):
            name, raw = m.group(1), int(m.group(2).replace(",", ""))
            if name not in FACTS:
                unknown.append(f"{rel(f)} — unknown fact {name!r}; add it to FACTS in scripts/check_docs.py")
                continue
            found.setdefault(name, []).append((rel(f), raw))

    bad = list(unknown)
    for name, sites in sorted(found.items()):
        cmd, fn = FACTS[name]
        try:
            actual = fn()
        except Exception as exc:  # a broken provider must fail loudly, not pass
            bad.append(f"fact {name!r}: ground-truth command failed — {exc}")
            continue
        for where, claimed in sites:
            if claimed != actual:
                bad.append(
                    f"{where} — claims {name}={claimed}, actual is {actual}\n"
                    f"      ground truth: {cmd}"
                )
    return bad


def check_stale_deploy() -> list[str]:
    """No 'not yet deployed' claim may survive its deployment.

    ADR-0036 carried this in three places for a day after it went live (Category
    A). The phrasing is narrow on purpose: it flags the claim, and a human
    decides whether the thing has since shipped.

    Quoted occurrences are skipped: the phrase inside quotes or backticks is
    *naming* it (ERR-016 and /review-step both do) rather than *claiming* it.
    The real offenders were unquoted and bold.
    """
    needles = ("not yet deployed", "not yet live-validated", "not deployed or live")
    quoted = re.compile(r'"[^"]*"|`[^`]*`')
    bad = []
    for f in md_files():
        if rel(f).startswith("docs/session-log/"):
            continue  # verbatim historical record — never edited to look tidier
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = quoted.sub("", line.lower())
            for n in needles:
                if n in low:
                    bad.append(f"{rel(f)}:{i} — {n!r}; if it has shipped, say so")
    return bad


def check_mermaid_edge_labels() -> list[str]:
    """Mermaid edge labels containing markup or slashes MUST be quoted.

    WHY THIS EXISTS: two diagrams — including the README's front-page architecture — rendered
    as a red "Unable to render rich display" box on GitHub for an unknown length of time, and
    nobody noticed. Both were the same defect: an UNQUOTED edge label in the
    `A -. text .-> B` form containing `<br/>` and `/`, which mermaid's lexer rejects.

    This is a targeted heuristic, NOT a mermaid parser — it catches the shape that actually
    broke, twice, with no new dependency. A full parse needs a node toolchain in CI (mermaid +
    jsdom); that is a bigger call, and the validator used to find these lives in the PR notes.
    Quoting an edge label is always safe, so the rule is simply: quote it.
    """
    edge = re.compile(
        r"""(?:-\.|--|==)\s+          # a link opener followed by a bare label
            (?!["|>])                 # already quoted / already a pipe-form label -> fine
            ([^"|
]*?)               # the label text
            \s+(?:\.->|-->|==>)""",   # the closing arrow
        re.X,
    )
    # Narrowed to angle brackets ONLY, and narrowed on evidence: the two real breakages both
    # carried `<br/>`, while `run summary → runs/` and `errors / dead-man` were flagged by a
    # wider rule and then confirmed FINE by an actual mermaid parse. A gate with false
    # positives trains people to ignore it — which is the failure this repo keeps re-learning.
    risky = re.compile(r"[<>]")
    bad = []
    for f in md_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for block in re.finditer(r"```mermaid\n(.*?)```", text, re.S):
            start = text[: block.start()].count("\n") + 1
            for i, line in enumerate(block.group(1).split("\n"), 1):
                for m in edge.finditer(line):
                    label = m.group(1).strip()
                    if label and risky.search(label):
                        bad.append(
                            f"{rel(f)}:{start + i} — unquoted mermaid edge label "
                            f"{label!r} contains markup (< or >); wrap it in double quotes "
                            "or GitHub renders the whole diagram as an error box"
                        )
    return bad


CHECKS = [
    ("internal links resolve", check_links),
    ("plan §NN citations are links", check_plan_refs),
    ("guarded counts match ground truth", check_facts),
    ("no stale deployment claims", check_stale_deploy),
    ("mermaid edge labels are quoted", check_mermaid_edge_labels),
]


def main() -> int:
    if "--list" in sys.argv:
        print("Ground truth right now:\n")
        for name, (cmd, fn) in FACTS.items():
            print(f"  {name:<20} = {fn():<6}  ({cmd})")
        return 0

    failures = 0
    for label, fn in CHECKS:
        problems = fn()
        if problems:
            failures += len(problems)
            print(f"\nFAIL — {label}  ({len(problems)}):")
            for p in problems:
                print(f"  • {p}")
        else:
            print(f"ok   — {label}")

    if failures:
        print(
            f"\n{failures} documentation problem(s). See ERR-016 in "
            "docs/ledgers/errors.md for why this is a build failure and not a warning."
        )
        return 1
    print("\nAll documentation checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
