from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

RECEIPT_SCHEMA = "ai-os-tool-receipt:v1"
RECEIPT_STATUSES = {"success", "error", "partial_success"}

_RECEIPT_KEYS = {
    "schema",
    "authoritative",
    "acceptance",
    "run_id",
    "step",
    "action_id",
    "tool",
    "status",
    "args_sha256",
    "output_sha256",
    "output_bytes",
    "created_at",
}


def _canonical_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def make_tool_receipt(
    *,
    run_id: str,
    step: int,
    tool: str,
    status: str,
    args: Any,
    output: Any,
    action_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id is required")
    if not isinstance(step, int) or isinstance(step, bool) or step < 1:
        raise ValueError("step must be a positive integer")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("tool is required")
    if status not in RECEIPT_STATUSES:
        raise ValueError(f"unsupported receipt status: {status!r}")
    if action_id is not None and (
        not isinstance(action_id, str) or not action_id.strip()
    ):
        raise ValueError("action_id must be null or a non-empty string")

    output_bytes = _canonical_bytes(output)
    timestamp = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "authoritative": False,
        "acceptance": False,
        "run_id": run_id,
        "step": step,
        "action_id": action_id,
        "tool": tool,
        "status": status,
        "args_sha256": _digest(args),
        "output_sha256": "sha256:" + hashlib.sha256(output_bytes).hexdigest(),
        "output_bytes": len(output_bytes),
        "created_at": timestamp,
    }
    validate_tool_receipt(receipt)
    return receipt


def _valid_sha256(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value[7:]
    return len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)


def validate_tool_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    if set(receipt) != _RECEIPT_KEYS:
        raise ValueError("receipt keys do not match schema")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("unsupported receipt schema")
    if receipt.get("authoritative") is not False:
        raise ValueError("receipt must be non-authoritative")
    if receipt.get("acceptance") is not False:
        raise ValueError("receipt is execution evidence, not acceptance")
    if not isinstance(receipt.get("run_id"), str) or not receipt["run_id"].strip():
        raise ValueError("run_id is required")
    if (
        not isinstance(receipt.get("step"), int)
        or isinstance(receipt["step"], bool)
        or receipt["step"] < 1
    ):
        raise ValueError("step must be a positive integer")
    action_id = receipt.get("action_id")
    if action_id is not None and (
        not isinstance(action_id, str) or not action_id.strip()
    ):
        raise ValueError("action_id must be null or a non-empty string")
    if not isinstance(receipt.get("tool"), str) or not receipt["tool"].strip():
        raise ValueError("tool is required")
    if receipt.get("status") not in RECEIPT_STATUSES:
        raise ValueError("unsupported receipt status")
    if not _valid_sha256(receipt.get("args_sha256")) or not _valid_sha256(
        receipt.get("output_sha256")
    ):
        raise ValueError("receipt hashes must be full sha256 digests")
    if (
        not isinstance(receipt.get("output_bytes"), int)
        or isinstance(receipt["output_bytes"], bool)
        or receipt["output_bytes"] < 0
    ):
        raise ValueError("output_bytes must be a non-negative integer")

    created_at = receipt.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("created_at is required")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be ISO-8601") from exc


def receipt_fingerprint(receipt: dict[str, Any]) -> str:
    validate_tool_receipt(receipt)
    canonical = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
