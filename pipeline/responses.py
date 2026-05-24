"""
Standard API / pipeline error envelopes.
"""

from __future__ import annotations

from typing import Any, List, Optional


def api_error(
    message: str,
    *,
    stage: str = "input",
    stages_completed: Optional[List[str]] = None,
    extra: Optional[dict] = None,
) -> dict:
    """JSON error response shared by all HTTP endpoints."""
    body: dict[str, Any] = {
        "status": "error",
        "stages_completed": stages_completed or [],
        "error": {"stage": stage, "message": message},
    }
    if extra:
        body.update(extra)
    return body
