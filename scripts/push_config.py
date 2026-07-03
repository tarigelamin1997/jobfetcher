#!/usr/bin/env python3
"""push_config.py — upload the local config YAMLs to S3 so the deployed pipeline picks them
up on its next run. THE everyday "apply my settings" command (ADR-0022): edit your YAML, run
this, done — no Lambda rebuild, no `terraform apply`.

    python scripts/push_config.py                 # bucket from $JOBFETCHER_DATA_BUCKET / terraform
    python scripts/push_config.py --bucket NAME   # explicit bucket

It VALIDATES both files (SearchSpec / Profile) before uploading — a broken config fails loudly
here and never reaches S3, so a bad edit can't poison a live run. Uploads:
    config/search_config.local.yml -> s3://<bucket>/config/search_config.yml
    config/profile.local.yml       -> s3://<bucket>/config/profile.yml
(the keys the Lambda's $SEARCH_CONFIG_PATH / $PROFILE_PATH point at).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Repo root + the src/ package (so this runs without an editable install).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobfetcher.core.profile import Profile  # noqa: E402
from jobfetcher.core.search_spec import SearchSpec  # noqa: E402

# local file -> S3 key (the keys the Lambda env points at)
_UPLOADS = [
    (ROOT / "config" / "search_config.local.yml", "config/search_config.yml", SearchSpec),
    (ROOT / "config" / "profile.local.yml", "config/profile.yml", Profile),
]


def _resolve_bucket(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("JOBFETCHER_DATA_BUCKET")
    if env and env.strip():
        return env.strip()
    # last resort: read it from terraform output (the deployed bucket name)
    try:
        out = subprocess.run(
            ["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-raw", "data_bucket_name"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        if out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — terraform not installed / no state — fall through to the error
        pass
    sys.exit(
        "no S3 bucket — pass --bucket, set $JOBFETCHER_DATA_BUCKET, or run from a deployed "
        "checkout (terraform output data_bucket_name)."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Push local config YAMLs to S3 (no redeploy).")
    ap.add_argument("--bucket", help="S3 data bucket (else $JOBFETCHER_DATA_BUCKET / terraform).")
    args = ap.parse_args()

    # 1) validate every file BEFORE touching S3 — a broken edit must never reach a live run.
    for path, _key, model in _UPLOADS:
        if not path.exists():
            sys.exit(f"missing {path} — seed it from {path.name.replace('.local.', '.sample.')}")
        try:
            model.from_yaml_text(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — surface the validation error as a clean exit
            sys.exit(f"INVALID {path.name}: {exc}")
    print("[1/2] validated: search_config.local.yml + profile.local.yml")

    # 2) upload.
    bucket = _resolve_bucket(args.bucket)
    import boto3

    s3 = boto3.client("s3")
    for path, key, _model in _UPLOADS:
        s3.put_object(
            Bucket=bucket, Key=key,
            Body=path.read_text(encoding="utf-8").encode("utf-8"),
            ContentType="application/x-yaml",
        )
        print(f"[2/2] uploaded {path.name} -> s3://{bucket}/{key}")
    print("done — the next pipeline run will use these settings (no redeploy needed).")


if __name__ == "__main__":
    main()
