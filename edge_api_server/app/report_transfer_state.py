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
    }
