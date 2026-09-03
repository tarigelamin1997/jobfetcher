#!/usr/bin/env python
"""Doc audit — the gate that keeps the repo honest about itself.

WHAT: four checks over the markdown in this repo, run by CI on every PR.
      (1) every internal link resolves
      (2) no unresolvable `plan §NN` citations
      (3) every *guarded* prose count agrees with the command that produces it
      (4) no stale "not yet deployed" claim on a shipped decision

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
    """
    needles = ("not yet deployed", "not yet live-validated", "not deployed or live")
    bad = []
    for f in md_files():
        if rel(f).startswith("docs/session-log/"):
            continue  # verbatim historical record — never edited to look tidier
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            for n in needles:
                if n in low:
                    bad.append(f"{rel(f)}:{i} — {n!r}; if it has shipped, say so")
    return bad


CHECKS = [
    ("internal links resolve", check_links),
    ("plan §NN citations are links", check_plan_refs),
    ("guarded counts match ground truth", check_facts),
    ("no stale deployment claims", check_stale_deploy),
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
