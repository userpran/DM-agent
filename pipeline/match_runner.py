"""
pipeline/match_runner.py

Parse → profile → schema-match across multiple uploaded files.
"""

from __future__ import annotations

import time
from typing import List, Tuple

from pydantic import ValidationError

from pipeline.logger import get_logger
from pipeline.responses import api_error
from pipeline.runner import run_csv_pipeline, run_ddl_pipeline
from pipeline.source_extractor import merge_pipeline_sources
from schema_matching.matcher import match_schemas

logger = get_logger(__name__)


def _analyze_one(content: bytes | str, filename: str) -> dict:
    lower = filename.lower()
    if lower.endswith(".csv"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        return run_csv_pipeline(content, filename=filename)
    if lower.endswith(".sql") or lower.endswith(".ddl"):
        text = content if isinstance(content, str) else content.decode("utf-8")
        return run_ddl_pipeline(text, filename=filename)
    return {
        "pipeline": "unknown",
        "filename": filename,
        "status": "error",
        "stages_completed": [],
        "error": {"stage": "input", "message": "Unsupported file type"},
        "parse_output": None,
        "profiling_output": None,
    }


def _count_profiled_tables(result: dict) -> int:
    """Return how many tables produced profiling output."""
    if result.get("status") not in ("success", "partial"):
        return 0
    out = result.get("profiling_output")
    if out is None:
        return 0
    if result.get("pipeline") == "csv":
        return 1
    if result.get("pipeline") == "ddl":
        return sum(1 for t in out if t.get("status") == "success")
    return 0


def run_match_pipeline(
    files: List[Tuple[bytes | str, str]],
) -> dict:
    """
    Run parse + profile on each file, then schema matching across all tables.

    Parameters
    ----------
    files : List of (content, filename) tuples. At least two files required.

    Returns
    -------
    JSON-serialisable dict with per-file analyze results and matching_output.
    """
    if len(files) < 2:
        return api_error(
            "At least two files are required",
            extra={"analyze_results": [], "matching_output": None},
        )

    start = time.time()
    analyze_results: List[dict] = []
    errors: List[str] = []

    for content, filename in files:
        logger.info(f"MATCH pipeline — analyzing '{filename}'")
        result = _analyze_one(content, filename)
        analyze_results.append(result)

        if result.get("status") == "error":
            err = result.get("error") or {}
            errors.append(f"{filename}: {err.get('message', 'unknown error')}")
        elif _count_profiled_tables(result) == 0:
            errors.append(f"{filename}: no tables were successfully profiled")

    if errors:
        return api_error(
            "; ".join(errors),
            stage="analyze",
            stages_completed=["analyze"],
            extra={
                "analyze_results": analyze_results,
                "matching_output": None,
                "elapsed_ms": round((time.time() - start) * 1000, 1),
            },
        )

    try:
        sources = merge_pipeline_sources(analyze_results)
    except ValueError as exc:
        return api_error(
            str(exc),
            stage="extract",
            stages_completed=["analyze", "extract"],
            extra={
                "analyze_results": analyze_results,
                "matching_output": None,
                "elapsed_ms": round((time.time() - start) * 1000, 1),
            },
        )

    if len(sources) < 2:
        return api_error(
            (
                "Need at least two profiled tables across uploads for matching "
                f"(found {len(sources)})"
            ),
            stage="extract",
            stages_completed=["analyze", "extract"],
            extra={
                "analyze_results": analyze_results,
                "sources_extracted": sources,
                "matching_output": None,
                "elapsed_ms": round((time.time() - start) * 1000, 1),
            },
        )

    logger.info(f"MATCH pipeline — matching {len(sources)} source(s)")
    try:
        matching = match_schemas(sources)
    except (ValueError, ValidationError) as exc:
        logger.error(f"MATCH pipeline — matcher failed: {exc}")
        return api_error(
            str(exc),
            stage="match",
            stages_completed=["analyze", "extract", "match"],
            extra={
                "analyze_results": analyze_results,
                "sources_extracted": sources,
                "matching_output": None,
                "elapsed_ms": round((time.time() - start) * 1000, 1),
            },
        )

    elapsed = round((time.time() - start) * 1000, 1)
    logger.info(f"MATCH pipeline — done in {elapsed}ms")

    return {
        "status": "success",
        "stages_completed": ["analyze", "extract", "match"],
        "error": None,
        "analyze_results": analyze_results,
        "sources_extracted": sources,
        "matching_output": matching.model_dump(),
        "elapsed_ms": elapsed,
    }
