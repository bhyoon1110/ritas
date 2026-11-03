"""Public delivery progress without exposing the private mail envelope."""

from __future__ import annotations

import json
from typing import Any


def public_delivery_state(transfer: dict[str, Any] | None) -> dict[str, Any]:
    row = transfer or {}
    raw = row.get("delivery_state_json")
    try:
        state = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        state = None
    if not isinstance(state, dict):
        state = {}
    # Do not return the raw JSON: future versions may add private diagnostics.
    def identifier(key: str) -> str | int | None:
        value = state.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        return value if len(str(value)) <= 255 else None

    def message(key: str, fallback: str | None = None) -> str | None:
        value = state.get(key, row.get(fallback) if fallback else None)
        if not isinstance(value, str):
            return None
        return "".join(c for c in value[:2000] if c >= " " or c in "\n\t") or None

    def count(key: str) -> int | None:
        value = state.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2_147_483_647 else None

    return {
        "documentStatus": row.get("document_status"),
        "mailStatus": row.get("mail_status"),
        "documentCompletedAt": row.get("document_completed_at"),
        "deliveryPhase": identifier("phase"),
        "axDocNum": identifier("axDocNum"),
        "regenSeq": identifier("regenSeq"),
        "fileNo": identifier("fileNo"),
        "mailRequestId": identifier("requestId"),
        "alarmResultNo": identifier("alarmResultNo"),
        "documentPollCount": count("documentPollCount"),
        "mailPollCount": count("mailPollCount"),
        "mailTryCount": count("mailTryCount") if count("mailTryCount") is not None else count("tryCount"),
        "needsReview": state.get("needsReview") is True,
        "reviewReason": message("reviewReason"),
        "mailRetryUncertain": state.get("mailRetryUncertain") is True,
        "nextPollSeconds": count("nextPollSeconds") or count("pollDelaySeconds"),
        "documentErrorCode": identifier("documentErrorCode"),
        "documentErrorMessage": message("documentErrorMessage"),
        "mailErrorCode": identifier("mailErrorCode"),
        "mailErrorMessage": message("mailErrorMessage"),
        "lastErrorCode": message("lastErrorCode", "last_error_code"),
        "lastErrorMessage": message("lastErrorMessage", "last_error_message"),
        "acceptedAt": identifier("acceptedAt"),
        "sentAt": identifier("sentAt"),
    }
