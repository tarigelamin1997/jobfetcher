#!/usr/bin/env python
"""Doc propagation — generate what is mechanical, check what is not.

WHAT: two things, deliberately kept apart.

  GENERATED BLOCKS  `<!--gen:name-->…<!--/gen-->` regions rewritten from a single
                    source of truth. `--write` updates them; `--check` fails if
                    running `--write` would change anything.
  CONSISTENCY CHECKS  cross-file agreement that involves human prose. These are
                    NEVER overwritten — they are reported for a person to fix.

WHY: ERR-016. Facts had no owning file, so the same content was copy-pasted and
     the copies went stale silently. `check_docs.py` closed that for *derived
     numbers*. This closes it for *derived content* — and, just as importantly,
     draws the line where generation must stop.

THE LINE, AND WHY IT MATTERS MORE THAN THE CODE:
     Generating the ADR index was the obvious next step and it is WRONG. Measured
     2026-09-04: 28 of 38 index rows carry a curated one-line summary richer than
     the ADR's own H1 ("…; Adzuna deferred", "…; resolves D-v0-1"). Regenerating
     from titles would have destroyed 28 hand-written summaries and called it
     tidying. So the index is CHECKED for completeness and status agreement, and
     its prose is left alone.

     Same for the errors ledger: an entry's ID and status are mechanical, its
     Symptom column is a human summary. Generate the first, check the second,
     never flatten the third.

     Over-generation is not a neutral mistake. A doc nobody may hand-edit is a
     doc nobody will improve.

SO-WHAT: the propagation is automatic (pre-commit `--write`) and the guarantee is
     armed (CI `--check`). The hook is convenience; a hook is bypassable with
     `--no-verify`, which is precisely ERR-015's failure mode, so CI is the layer
     that actually holds.

READ-ONLY outside the doc tree: reads CHANGELOG, migrations/, docs/. Never writes
to src/, tests/, migrations/ or terraform/.

Usage:
    python scripts/sync_docs.py --check    # CI: exit 1 if anything is stale
    python scripts/sync_docs.py --write    # local/pre-commit: bring blocks current
    python scripts/sync_docs.py --list     # show what the generators produce
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the box-drawing and
# typographic characters this output uses. Without this the script CRASHES on
# Tarig's machine — and it runs in a pre-commit hook there, so that is fatal.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

BLOCK_RE = re.compile(r"(<!--gen:([a-z_]+)-->)(.*?)(<!--/gen-->)", re.S)


def md_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.md") if ".git" not in p.parts)


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


# ── generators: mechanical content only ──────────────────────────────────────


def _latest_release() -> tuple[str, str]:
    """(version, date) from the newest `## [vX.Y.Z] — YYYY-MM-DD` in the CHANGELOG."""
    for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## \[(v[\d.]+)\]\s*[—-]\s*(\d{4}-\d{2}-\d{2})", line)
        if m:
            return m.group(1), m.group(2)
    raise RuntimeError("no released version found in CHANGELOG.md")


def gen_current_release() -> str:
    v, d = _latest_release()
    return f"`{v}` ({d})"


def gen_migrations() -> str:
    names = sorted(p.stem for p in (ROOT / "migrations" / "versions").glob("0*.py"))
    return ", ".join(f"`{n}`" for n in names)


GENERATORS = {
    "current_release": ("the newest ## [vX.Y.Z] heading in CHANGELOG.md", gen_current_release),
    "migrations": ("migrations/versions/0*.py", gen_migrations),
}


def sync_blocks(write: bool) -> list[str]:
    problems = []
    for f in md_files():
        text = f.read_text(encoding="utf-8")
        out, changed = text, False

        def repl(m: re.Match) -> str:
            nonlocal changed
            open_tag, name, body, close_tag = m.groups()
            if name not in GENERATORS:
                problems.append(f"{rel(f)} — unknown gen block {name!r}; add it to GENERATORS")
                return m.group(0)
            fresh = GENERATORS[name][1]()
            if body != fresh:
                changed = True
                if not write:
                    problems.append(
                        f"{rel(f)} — gen:{name} is stale\n"
                        f"      has:  {body.strip()[:90]}\n"
                        f"      want: {fresh.strip()[:90]}"
                    )
            return f"{open_tag}{fresh}{close_tag}"

        out = BLOCK_RE.sub(repl, text)
        if write and changed:
            f.write_text(out, encoding="utf-8")
            print(f"  updated {rel(f)}")
    return problems


# ── consistency checks: prose involved, never overwritten ────────────────────


def check_adr_index() -> list[str]:
    """The ADR index must be complete and its Status column must match each ADR.

    The *Decision* column is deliberately NOT checked: it is a curated summary,
    intentionally richer than the ADR's H1 (28 of 38 differ, by design).
    """
    idx_path = ROOT / "docs" / "adr" / "README.md"
    idx = idx_path.read_text(encoding="utf-8")
    rows = {
        m.group(1): m.group(3).strip()
        for m in re.finditer(r"^\| \[(\d{4})\]\([^)]+\) \| (.+?) \| (.+?) \|\s*$", idx, re.M)
    }
    problems = []
    files = sorted((ROOT / "docs" / "adr").glob("0*.md"))
    for f in files:
        num = f.name[:4]
        if num not in rows:
            problems.append(f"docs/adr/README.md — ADR-{num} exists but has no index row")
            continue
        txt = f.read_text(encoding="utf-8")
        m = re.search(r"^\*\*Status:\*\*\s*(.+)$", txt, re.M) or re.search(
            r"^## Status\s*\n(.+)$", txt, re.M
        )
        real = (m.group(1) if m else "").strip()
        head = re.split(r"[·(]", real)[0].strip().rstrip(".")
        if head and not rows[num].lower().startswith(head.lower()[:8]):
            problems.append(
                f"docs/adr/README.md — ADR-{num} status disagrees\n"
                f"      index: {rows[num][:70]}\n"
                f"      ADR:   {real[:70]}"
            )
    for num in sorted(set(rows) - {f.name[:4] for f in files}):
        problems.append(f"docs/adr/README.md — index row ADR-{num} has no file")
    return problems


def check_error_ledger() -> list[str]:
    """Every ERR entry needs a summary-table row, agreeing on status; IDs contiguous.

    The Symptom column is a human summary and is never generated or compared —
    only its presence is required.
    """
    path = ROOT / "docs" / "ledgers" / "errors.md"
    text = path.read_text(encoding="utf-8")
    entries = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^### ERR-(\d{3})\s*[—-]\s*.*?\[(.+?)\]\s*$", text, re.M)
    }
    rows = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^\| ERR-(\d{3}) \|[^|]*\|[^|]*\|.*?\| (.+?) \|\s*$", text, re.M)
    }
    problems = []
    for num in sorted(entries):
        if num not in rows:
            problems.append(f"docs/ledgers/errors.md — ERR-{num} has an entry but no summary-table row")
    for num in sorted(set(rows) - set(entries)):
        problems.append(f"docs/ledgers/errors.md — summary row ERR-{num} has no entry above it")
    for num in sorted(set(entries) & set(rows)):
        head = entries[num].split("—")[0].split("-")[0].strip().lower()
        if head and head not in rows[num].lower():
            problems.append(
                f"docs/ledgers/errors.md — ERR-{num} status disagrees\n"
                f"      entry: [{entries[num][:50]}]\n"
                f"      table: {rows[num][:70]}"
            )
    if entries:
        nums = sorted(int(n) for n in entries)
        gaps = [n for n in range(1, max(nums) + 1) if n not in nums]
        if gaps:
            problems.append(
                f"docs/ledgers/errors.md — ERR ids not contiguous, missing: "
                f"{', '.join(f'ERR-{g:03d}' for g in gaps)}"
            )
    return problems


CHECKS = [
    ("ADR index complete + statuses agree", check_adr_index),
    ("error ledger entries match summary table", check_error_ledger),
]


def main() -> int:
    if "--list" in sys.argv:
        print("Generators produce:\n")
        for name, (src, fn) in GENERATORS.items():
            print(f"  gen:{name:<18} = {fn()}\n  {'':<21}   (from {src})")
        return 0

    write = "--write" in sys.argv
    if not write and "--check" not in sys.argv:
        print(__doc__.split("Usage:")[1])
        return 2

    failures = 0
    if write:
        print("Syncing generated blocks…")
    problems = sync_blocks(write)
    if problems:
        failures += len(problems)
        print(f"\nFAIL — generated blocks are stale ({len(problems)}):")
        for p in problems:
            print(f"  • {p}")
        print("\n  Fix: python scripts/sync_docs.py --write")
    elif not write:
        print("ok   — generated blocks current")

    for label, fn in CHECKS:
        found = fn()
        if found:
            failures += len(found)
            print(f"\nFAIL — {label}  ({len(found)}):")
            for p in found:
                print(f"  • {p}")
        else:
            print(f"ok   — {label}")

    if failures and not write:
        print(
            f"\n{failures} problem(s). Generated blocks are fixed with --write; the "
            "consistency checks are prose and need a human — that split is deliberate "
            "(see the module docstring and ERR-016)."
        )
        return 1
    if write:
        print("Done. Run --check to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
