"""v0 pipeline Lambda — STUB package.

The real fetch -> dissect -> filter -> score -> notify handler lands at a later
build step (build-plan Steps 4-7). This stub exists only so the Terraform infra is
complete and applyable now; it makes no AWS calls and reads no secrets.
"""


def handler(event, context):  # noqa: ARG001 - signature fixed by Lambda
    return {
        "statusCode": 200,
        "body": "jobfetcher v0 handler stub - real handler lands at a later build step",
    }
