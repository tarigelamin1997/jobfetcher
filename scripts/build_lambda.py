#!/usr/bin/env python
"""Build the real v0 pipeline Lambda deployment package (build-plan Step 10).

WHAT: stages a Linux/x86-64 Python-3.11 Lambda package under `terraform/build/lambda/` —
      runtime deps (Linux wheels) + the `jobfetcher` source + the two config YAMLs. Terraform's
      archive provider zips this dir (`terraform/lambda.tf`).
WHY:  the deployed runtime talks to Aurora via the **Data API** (ADR-0018), so the heavy
      `psycopg2` driver is NOT needed; `boto3`/`botocore` ship in the Lambda runtime already;
      `alembic` is migrations-only (run from local at deploy, never in the function). Vendoring
      only what the Data-API path imports keeps the package small + the cold start cheap.
SO-WHAT: runnable on Windows WITHOUT Docker — we ask pip for `manylinux2014_x86_64` wheels so the
      compiled dep (`pydantic_core`) is the Linux `.so`, never the local Windows `.pyd`. A guard
      asserts exactly that, because a leaked Windows binary is the #1 cross-OS packaging mistake.

Run:  python scripts/build_lambda.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- paths
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PKG = REPO_ROOT / "src" / "jobfetcher"
CONFIG_DIR = REPO_ROOT / "config"
STAGING = REPO_ROOT / "terraform" / "build" / "lambda"

# Runtime deps to vendor (Linux wheels). Floors mirror pyproject `[project.dependencies]`.
# EXCLUDED on purpose: psycopg2* (Data-API path, ADR-0018), alembic (migrations-only, run from
# local at deploy). Transitive pure-Python deps (pydantic-core, typing-extensions, annotated-types,
# greenlet, …) resolve automatically as py3-none-any/manylinux.
#
# NOTE: `sqlalchemy-aurora-data-api` -> `aurora-data-api` HARD-DEPENDS on boto3/botocore, so pip
# pulls them in transitively. boto3/botocore (and their s3transfer/jmespath deps) ship in the
# Lambda runtime already, so we PRUNE them post-install (see PRUNE_AFTER_INSTALL) — vendoring them
# would add ~40 MB for nothing and is the difference between a <50 MB direct upload and not.
RUNTIME_DEPS = [
    "pydantic>=2",
    "pyyaml>=6",
    "SQLAlchemy>=2",
    "sqlalchemy-aurora-data-api>=0.5",
]

# Packages the Lambda Python runtime already provides — prune them from the staged dir after
# install (they arrive only as transitive deps of aurora-data-api). Removing them keeps the zip
# under the 50 MB direct-upload limit and avoids shadowing the runtime's own (newer) boto3.
PRUNE_AFTER_INSTALL = (
    "boto3", "botocore", "s3transfer", "jmespath", "dateutil", "six", "urllib3"
)

# Lambda target (ADR: Python 3.11, Linux x86-64).
PIP_PLATFORM = "manylinux2014_x86_64"
PYTHON_VERSION = "3.11"

# Things that must NOT end up in the package (runtime-provided or unused at runtime).
FORBIDDEN_TOP_LEVEL = ("boto3", "botocore", "psycopg2", "alembic")

# Required proof artifacts. Config YAMLs are NOT bundled (ADR-0022): they live in S3 and are
# read at runtime, so a settings change needs no rebuild. `scripts/push_config.py` uploads them.
REQUIRED_PRESENT = (
    "jobfetcher/handlers/pipeline.py",
)


def _run(cmd: list[str]) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def clean_staging() -> None:
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True, exist_ok=True)
    print(f"[1/6] clean staging dir: {STAGING}")


def install_deps() -> None:
    print("[2/6] install runtime deps (Linux manylinux2014_x86_64 wheels)")
    _run(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(STAGING),
            "--platform", PIP_PLATFORM,
            "--implementation", "cp",
            "--python-version", PYTHON_VERSION,
            "--only-binary=:all:",
            *RUNTIME_DEPS,
        ]
    )


def _ignore_pycache(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n == "__pycache__" or n.endswith(".pyc")}


def copy_source() -> None:
    dest = STAGING / "jobfetcher"
    shutil.copytree(SRC_PKG, dest, ignore=_ignore_pycache)
    print(f"[3/6] copy source: {SRC_PKG} -> {dest}")


def prune_runtime_provided() -> None:
    """Remove packages the Lambda runtime already provides (boto3 & its deps), pulled in only as
    transitive deps of aurora-data-api. Removes the import dir/module, the `.dist-info`, and any
    top-level `.py` shim (e.g. `six.py`)."""
    removed: list[str] = []
    for name in PRUNE_AFTER_INSTALL:
        for path in STAGING.glob(f"{name}"):  # package dir or module dir
            if path.is_dir():
                shutil.rmtree(path)
                removed.append(path.name)
        for path in STAGING.glob(f"{name}.py"):  # single-module shim (six.py)
            path.unlink()
            removed.append(path.name)
        for path in STAGING.glob(f"{name}-*.dist-info"):
            shutil.rmtree(path)
            removed.append(path.name)
        for path in STAGING.glob(f"{name}-*.data"):  # scripts/bin payloads, if any
            shutil.rmtree(path)
            removed.append(path.name)
    # `python_dateutil-*.dist-info` is the orphaned metadata for the pruned `dateutil` module.
    for path in STAGING.glob("python_dateutil-*.dist-info"):
        shutil.rmtree(path)
        removed.append(path.name)
    # bin/ holds console scripts for the pruned deps (e.g. jp.py); include/ holds C headers
    # (greenlet) that are build-time only; the top-level __pycache__ holds .pyc for the pruned
    # single-module shims (six/typing_extensions) — none belong in the runtime package.
    for junk in ("bin", "include", "__pycache__"):
        d = STAGING / junk
        if d.exists():
            shutil.rmtree(d)
            removed.append(f"{junk}/")
    print(f"[5/6] prune runtime-provided deps: {sorted(set(removed))}")


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt_mb(nbytes: int) -> str:
    return f"{nbytes / (1024 * 1024):.1f} MB"


def guards_and_report() -> None:
    print("[6/6] guards + report")

    # --- size + top-level listing -----------------------------------------
    total = _dir_size(STAGING)
    top = sorted(p.name for p in STAGING.iterdir())
    print(f"\n  staged size: {_fmt_mb(total)} ({total:,} bytes)")
    print(f"  top-level entries ({len(top)}):")
    for name in top:
        print(f"    - {name}")

    # --- forbidden runtime-provided / unused deps must be absent ----------
    leaked = [name for name in FORBIDDEN_TOP_LEVEL if (STAGING / name).exists()]
    if leaked:
        sys.exit(f"\nFAIL: forbidden package(s) leaked into the package: {leaked}")
    print(f"\n  OK: none of {FORBIDDEN_TOP_LEVEL} present")

    # --- required files present -------------------------------------------
    missing = [rel for rel in REQUIRED_PRESENT if not (STAGING / rel).exists()]
    if missing:
        sys.exit(f"FAIL: required file(s) missing from the package: {missing}")
    print(f"  OK: required files present: {list(REQUIRED_PRESENT)}")

    # --- pydantic_core must be a Linux manylinux .so, NOT a Windows .pyd ---
    pc_dir = STAGING / "pydantic_core"
    if not pc_dir.exists():
        sys.exit("FAIL: pydantic_core was not installed (no compiled core).")
    pyd = list(pc_dir.glob("*.pyd"))
    if pyd:
        sys.exit(
            f"FAIL: a Windows binary leaked into pydantic_core ({[p.name for p in pyd]}). "
            "The local platform's wheel was used instead of the Linux one — re-run; pip must "
            "fetch manylinux2014_x86_64 wheels."
        )
    so = list(pc_dir.glob("*.so"))
    linux_so = [p for p in so if "x86_64-linux" in p.name or "manylinux" in p.name]
    if not so:
        sys.exit("FAIL: no compiled pydantic_core binary (.so) found — wheel resolution failed.")
    if not linux_so:
        sys.exit(
            f"FAIL: pydantic_core .so does not look like a Linux build: {[p.name for p in so]}"
        )
    print(f"  OK: pydantic_core Linux wheel (manylinux proof): {linux_so[0].name}")

    # --- sanity: nothing in jobfetcher hard-imports psycopg2/alembic ------
    offenders: list[str] = []
    for py in (STAGING / "jobfetcher").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            if (
                s.startswith("import psycopg2")
                or s.startswith("from psycopg2")
                or s.startswith("import alembic")
                or s.startswith("from alembic")
            ):
                offenders.append(f"{py.relative_to(STAGING)}: {s}")
    if offenders:
        print("\n  WARNING: hard psycopg2/alembic import(s) found — these would break the deploy:")
        for o in offenders:
            print(f"    ! {o}")
    else:
        print("  OK: no hard psycopg2/alembic import in the jobfetcher import graph")

    print("\nBuild OK.")


def main() -> None:
    print(f"Building Lambda package -> {STAGING}\n")
    clean_staging()
    install_deps()
    copy_source()
    # config YAMLs are NOT bundled (ADR-0022) — they live in S3, read at runtime
    prune_runtime_provided()
    guards_and_report()


if __name__ == "__main__":
    main()
