"""`SesNotifier` — the `Notifier` port over AWS SES (`send_email`). Sends the daily digest
from a verified sender identity (`$SES_SENDER`) with both an HTML body and a plaintext
fallback. Behind the `Notifier` port so the core depends on the port, not boto3 (ADR-0015);
tests inject a fake SES client (or moto).

The sender comes from `$SES_SENDER` (no default — a clear error if unset; never a hardcoded
identity). No secrets here — boto3 uses the ambient IAM identity (the Lambda execution role).
"""
from __future__ import annotations

import os
from typing import Any

from ..core.ports import NotifierError

_SENDER_ENV = "SES_SENDER"
_CHARSET = "UTF-8"


class SesNotifier:
    """`Notifier` over SES (boto3 `send_email`). One client, reused.

    `sender` is resolved from the constructor arg or `$SES_SENDER`; an unset sender is a
    misconfiguration that fails LOUDLY at construction (a digest with no `Source` would be
    rejected by SES anyway — better a clear error here)."""

    def __init__(
        self,
        *,
        sender: str | None = None,
        region: str | None = None,
        client: Any = None,
    ) -> None:
        self._sender = (sender or os.environ.get(_SENDER_ENV) or "").strip()
        if not self._sender:
            raise NotifierError(
                f"no SES sender configured — set ${_SENDER_ENV} or pass sender="
            )
        if client is not None:
            self._client = client
        else:
            import boto3  # lazy: tests inject a moto/mock client and need no real import here

            self._client = boto3.client("ses", region_name=region)

    def send(
        self,
        *,
        subject: str,
        html_body: str,
        text_body: str,
        recipients: list[str],
    ) -> str:
        """Send the digest via SES `send_email` (both `Html` + `Text`, Charset UTF-8). Returns
        the SES `MessageId`. Any SES/boto3 failure (e.g. `MessageRejected` when an identity is
        unverified) is wrapped into `NotifierError` so the core never sees a raw boto3 error."""
        if not recipients:
            raise NotifierError("send requires at least one recipient")
        try:
            resp = self._client.send_email(
                Source=self._sender,
                Destination={"ToAddresses": list(recipients)},
                Message={
                    "Subject": {"Data": subject, "Charset": _CHARSET},
                    "Body": {
                        "Html": {"Data": html_body, "Charset": _CHARSET},
                        "Text": {"Data": text_body, "Charset": _CHARSET},
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001 — wrap ANY transport/SES error into the port's
            raise NotifierError(f"SES send_email failed: {exc}") from exc
        return resp.get("MessageId", "")
