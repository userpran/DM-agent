"""
pipeline/adapter.py

Bridges raw parser output dicts → profiler input dicts.

Why this file exists
--------------------
csv_parser and ddl_parser produce output shaped for their own purposes.
The profiler expects a specific input contract (ProfilerInput).
Rather than coupling the profiler to each parser's key names, this adapter
absorbs all the translation so neither the parser nor the profiler needs
to know about the other's internal format.

Key translations
----------------
  csv_parser output key  →  profiler input key
  ─────────────────────────────────────────────
  "columns"              →  "columns"       (passed through — ProfilerColumnMeta
                                             handles raw_dtype internally)
  "sample_rows"          →  "rows"          (renamed)
  <filename arg>         →  "table_name"    (injected by caller)

  ddl_parser output key  →  profiler input key
  ─────────────────────────────────────────────
  tbl["table_name"]      →  "table_name"
  tbl["columns"]         →  "columns"       (passed through — ProfilerColumnMeta
                                             handles "type" key internally)
  (no rows in DDL)       →  "rows": []      (empty → schema-only profiling path)
"""

from __future__ import annotations

from typing import List

from pipeline.logger import get_logger

logger = get_logger(__name__)


def csv_to_profiler_input(parsed: dict, table_name: str = "uploaded_csv") -> dict:
    """
    Convert csv_parser output into a single profiler input dict.

    CSV files are treated as a single virtual table whose name defaults to
    "uploaded_csv" but can be overridden (e.g. use the original filename).

    Parameters
    ----------
    parsed     : The dict returned by parse_csv().
    table_name : Name to assign the virtual table. Defaults to "uploaded_csv".

    Returns
    -------
    dict matching ProfilerInput schema:
        { "table_name": str, "columns": [...], "rows": [...] }

    Raises
    ------
    ValueError — if `parsed` does not contain a "columns" key.

    Notes
    -----
    csv_parser "columns" entries use key "raw_dtype" (not "raw_type").
    ProfilerColumnMeta's model_validator resolves this automatically.

    csv_parser provides full ``rows`` for profiling and ``sample_rows`` (5) for API preview.
    """
    if "columns" not in parsed:
        raise ValueError(
            "csv_to_profiler_input: parsed dict missing 'columns' key. "
            "Is this valid csv_parser output?"
        )

    columns  = parsed.get("columns", [])
    # Prefer full rows for profiling; fall back to sample_rows for legacy parse output
    rows     = parsed.get("rows") or parsed.get("sample_rows", [])

    logger.debug(
        f"csv_to_profiler_input: table='{table_name}', "
        f"{len(columns)} columns, {len(rows)} sample rows"
    )

    return {
        "table_name": table_name,
        "columns":    columns,
        "rows":       rows,
    }


def ddl_to_profiler_inputs(parsed: dict) -> List[dict]:
    """
    Convert ddl_parser output into a list of profiler input dicts — one per table.

    DDL files can define multiple CREATE TABLE statements.
    Each table becomes a separate profiler input so columns are profiled
    in their correct table context.

    Parameters
    ----------
    parsed : The dict returned by parse_ddl().

    Returns
    -------
    List of dicts, each matching ProfilerInput schema:
        [{ "table_name": str, "columns": [...], "rows": [] }, ...]

    Empty list is returned when no valid tables are found.

    Notes
    -----
    DDL columns use key "type" (not "raw_dtype").
    ProfilerColumnMeta's model_validator resolves this automatically.

    rows is always [] for DDL — triggers schema-only profiling path in profiler.py.
    """
    tables = parsed.get("tables", [])

    if not tables:
        logger.warning("ddl_to_profiler_inputs: no tables found in parsed DDL output")
        return []

    inputs = []
    for tbl in tables:
        table_name = tbl.get("table_name", "unknown_table")
        columns    = tbl.get("columns",    [])

        logger.debug(
            f"ddl_to_profiler_input: table='{table_name}', {len(columns)} columns"
        )

        inputs.append({
            "table_name": table_name,
            "columns":    columns,
            "rows":       [],    # DDL has no data → schema-only profiling
        })

    return inputs
