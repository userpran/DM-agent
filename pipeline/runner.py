"""
pipeline/runner.py

Orchestrates the full parse → profile → persist pipeline.

Public API:
  run_csv_pipeline(content: bytes, filename: str) -> dict
  run_ddl_pipeline(content: str,   filename: str) -> dict

Stages: PARSE → ADAPT → PROFILE → PERSIST
Each stage is independently timed, logged, and error-isolated.

Response shape (both pipelines):
  {
    "pipeline":          "csv" | "ddl",
    "filename":          str,
    "status":            "success" | "partial" | "error",
    "stages_completed": [...],
    "error":             null | {"stage": str, "message": str},
    "saved_profile_path": str | null,  # CSV only; DDL per-table
    "parse_output":      {...},
    "profiling_output":  {...} | [...]
  }
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ingestion_parsing.csv_parser import parse_csv
from ingestion_parsing.ddl_parser import parse_ddl
from pipeline.adapter import csv_to_profiler_input, ddl_to_profiler_inputs
from pipeline.logger import get_logger
from pipeline.persistence import save_profile
from profiling.profiler import profile_table

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _elapsed(start: float) -> str:
    """Return human-readable elapsed time string from a time.time() start."""
    ms = (time.time() - start) * 1000
    return f"{ms:.1f}ms"


def _error_response(
    pipeline: str,
    filename: str,
    stage: str,
    message: str,
    parse_output: Optional[dict] = None,
    stages_completed: Optional[List[str]] = None,
) -> dict:
    """Build a standardised error response dict."""
    return {
        "pipeline":           pipeline,
        "filename":           filename,
        "status":             "error",
        "stages_completed":   stages_completed or [],
        "error":              {"stage": stage, "message": message},
        "saved_profile_path": None,
        "parse_output":       parse_output,
        "profiling_output":   None,
    }


# ---------------------------------------------------------------------------
# CSV PIPELINE
# ---------------------------------------------------------------------------

def run_csv_pipeline(content: bytes, filename: str = "file.csv") -> dict:
    """
    Parse a CSV file and run column-level profiling. Saves result to disk.
    Returns a JSON-serialisable dict with parse_output and profiling_output.
    """
    pipeline_start = time.time()
    stages_done: List[str] = []

    logger.info(f"━━━ CSV PIPELINE START ━━━  file='{filename}'")

    # ── Stage 1: Parse ───────────────────────────────────────────────────────
    t = time.time()
    logger.info("  [1/3] PARSE  — running csv_parser")
    try:
        parsed = parse_csv(content)
    except Exception as exc:
        logger.error(f"  [1/3] PARSE  — unexpected exception: {exc}")
        return _error_response("csv", filename, "parse", str(exc))

    if "error" in parsed:
        logger.error(f"  [1/3] PARSE  — failed: {parsed['error']}")
        return _error_response("csv", filename, "parse", parsed["error"])

    logger.info(
        f"  [1/3] PARSE  — OK  "
        f"rows={parsed.get('row_count', '?')}  "
        f"cols={parsed.get('column_count', '?')}  "
        f"({_elapsed(t)})"
    )
    stages_done.append("parse")

    # ── Stage 2: Adapt ───────────────────────────────────────────────────────
    t = time.time()
    logger.info("  [2/3] ADAPT  — converting parser output → profiler input")
    try:
        # Strip extension from filename to use as a cleaner table name
        table_name = filename.rsplit(".", 1)[0] if "." in filename else filename
        profiler_input = csv_to_profiler_input(parsed, table_name=table_name)
    except (ValueError, KeyError) as exc:
        logger.error(f"  [2/3] ADAPT  — failed: {exc}")
        return _error_response("csv", filename, "adapt", str(exc), parsed, stages_done)

    logger.info(f"  [2/3] ADAPT  — OK  table='{profiler_input['table_name']}'  ({_elapsed(t)})")
    stages_done.append("adapt")

    # ── Stage 3: Profile ─────────────────────────────────────────────────────
    t = time.time()
    logger.info("  [3/3] PROFILE — running profiler")
    try:
        result = profile_table(profiler_input)
    except Exception as exc:
        logger.error(f"  [3/3] PROFILE — failed: {exc}")
        return _error_response("csv", filename, "profile", str(exc), parsed, stages_done)

    col_count = len(result.column_profiles)
    pk_count  = len(result.table_summary.pk_candidates)
    logger.info(
        f"  [3/3] PROFILE — OK  "
        f"columns={col_count}  pk_candidates={pk_count}  ({_elapsed(t)})"
    )
    stages_done.append("profile")

    # ── Stage 4: Persist ─────────────────────────────────────────────────────
    # Save failure is non-fatal — the pipeline still returns success
    saved_path = save_profile(
        result,
        table_name=profiler_input["table_name"],
        source_file=filename,
        pipeline="csv",
    )
    saved_profile_path = str(saved_path) if saved_path else None
    if saved_path:
        stages_done.append("persist")

    total_ms = (time.time() - pipeline_start) * 1000
    logger.info(f"━━━ CSV PIPELINE DONE  ━━━  total={total_ms:.1f}ms  stages={stages_done}")

    return {
        "pipeline":          "csv",
        "filename":          filename,
        "status":            "success",
        "stages_completed":  stages_done,
        "error":             None,
        "saved_profile_path": saved_profile_path,
        "parse_output":      parsed,
        "profiling_output":  result.model_dump(),
    }


# ---------------------------------------------------------------------------
# DDL PIPELINE
# ---------------------------------------------------------------------------

def run_ddl_pipeline(content: str, filename: str = "file.sql") -> dict:
    """
    Parse a SQL DDL file and run schema-only profiling for each table.
    Multiple CREATE TABLE statements each produce an independent profile.
    Status is "partial" when some tables succeed and some fail.
    """
    pipeline_start = time.time()
    stages_done: List[str] = []

    logger.info(f"━━━ DDL PIPELINE START ━━━  file='{filename}'")

    # ── Stage 1: Parse ───────────────────────────────────────────────────────
    t = time.time()
    logger.info("  [1/3] PARSE  — running ddl_parser")
    try:
        parsed = parse_ddl(content)
    except Exception as exc:
        logger.error(f"  [1/3] PARSE  — unexpected exception: {exc}")
        return _error_response("ddl", filename, "parse", str(exc))

    if "error" in parsed:
        logger.error(f"  [1/3] PARSE  — failed: {parsed['error']}")
        return _error_response("ddl", filename, "parse", parsed["error"])

    table_count = parsed.get("table_count", 0)
    logger.info(f"  [1/3] PARSE  — OK  tables={table_count}  ({_elapsed(t)})")
    stages_done.append("parse")

    # ── Stage 2: Adapt ───────────────────────────────────────────────────────
    t = time.time()
    logger.info("  [2/3] ADAPT  — converting parser output → profiler inputs")
    try:
        profiler_inputs = ddl_to_profiler_inputs(parsed)
    except Exception as exc:
        logger.error(f"  [2/3] ADAPT  — failed: {exc}")
        return _error_response("ddl", filename, "adapt", str(exc), parsed, stages_done)

    if not profiler_inputs:
        msg = "No valid tables to profile after adaptation"
        logger.warning(f"  [2/3] ADAPT  — {msg}")
        return _error_response("ddl", filename, "adapt", msg, parsed, stages_done)

    logger.info(f"  [2/3] ADAPT  — OK  {len(profiler_inputs)} table(s) prepared  ({_elapsed(t)})")
    stages_done.append("adapt")

    # ── Stage 3: Profile (one per table) ─────────────────────────────────────
    t = time.time()
    logger.info(f"  [3/3] PROFILE — profiling {len(profiler_inputs)} table(s)")

    profiling_outputs: List[Dict[str, Any]] = []
    success_count = 0
    error_count   = 0

    for pi in profiler_inputs:
        tbl_name = pi["table_name"]
        try:
            result = profile_table(pi)
            col_count = len(result.column_profiles)
            logger.info(f"        ✓ '{tbl_name}'  columns={col_count}")

            # Persist immediately — failure is non-fatal per table
            saved_path = save_profile(
                result,
                table_name=tbl_name,
                source_file=filename,
                pipeline="ddl",
            )

            profiling_outputs.append({
                "table_name":        tbl_name,
                "status":            "success",
                "error":             None,
                "saved_profile_path": str(saved_path) if saved_path else None,
                **result.model_dump(),    # unpacks column_profiles + table_summary
            })
            success_count += 1
        except Exception as exc:
            logger.error(f"        ✗ '{tbl_name}'  error: {exc}")
            profiling_outputs.append({
                "table_name":        tbl_name,
                "status":            "error",
                "error":             str(exc),
                "saved_profile_path": None,
            })
            error_count += 1

    logger.info(
        f"  [3/3] PROFILE — done  "
        f"success={success_count}  errors={error_count}  ({_elapsed(t)})"
    )
    stages_done.append("profile")

    # Determine overall pipeline status
    if error_count == 0:
        status = "success"
    elif success_count > 0:
        status = "partial"   # Some tables profiled, some failed
    else:
        status = "error"

    total_ms = (time.time() - pipeline_start) * 1000
    logger.info(f"━━━ DDL PIPELINE DONE  ━━━  status={status}  total={total_ms:.1f}ms")

    return {
        "pipeline":         "ddl",
        "filename":         filename,
        "status":           status,
        "stages_completed": stages_done,
        "error":            None,
        "parse_output":     parsed,
        "profiling_output": profiling_outputs,
    }
