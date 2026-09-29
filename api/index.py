from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from runtime import gate, normalize, preflight, prepare

app = FastAPI(title="ai-os-runtime HTTP API", version="1")


def _authorize(authorization: str | None) -> None:
    token = os.getenv("AIOS_SERVICE_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="AIOS_SERVICE_TOKEN is not configured")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "ai-os-runtime"}


@app.post("/api/runtime/preflight")
def http_preflight(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return preflight(
            payload["boot"],
            payload["capsule"],
            payload["fresh"],
            worker_id=payload["worker_id"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runtime/prepare")
def http_prepare(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return prepare(
            payload["boot"],
            payload["capsule"],
            payload["preflight"],
            driver=payload.get("driver", "manual"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runtime/normalize")
def http_normalize(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return normalize(payload["invocation"], payload["result"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runtime/gate")
def http_gate(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return gate(payload["boot"], payload["fresh"], payload["outcome"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
