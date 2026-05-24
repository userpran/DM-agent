"""
profiling/profiler.py

Core profiling engine.

Public API:  profile_table(input_data: dict) -> ProfilingResult

Two paths:
  CSV (rows present) — full statistical profiling via pandas DataFrame
  DDL (rows=[])      — schema-only: type inference from SQL keywords, no stats

Input dict format:
  {
    "table_name": str,
    "columns":    [{"name", "raw_dtype"|"type", ...}],  # from csv/ddl parser
    "rows":       [{col: val}, ...]                      # omit for DDL
  }
"""

from __future__ import annotations

from typing import Any, List, Optional

import pandas as pd

from profiling.models import (
    ColumnProfile,
    MixedTypeDetail,
    ProfilerInput,
    ProfilingResult,
    TableSummary,
    UniquenessDetail,
)
from profiling.utils import (
    compute_categorical_stats,
    compute_date_stats,
    compute_numeric_stats,
    detect_mixed_types,
    infer_type,
    is_empty_series,
    safe_list,
    score_uniqueness,
)

# ---------------------------------------------------------------------------
# THRESHOLDS
# ---------------------------------------------------------------------------

# Columns with null_pct above this value are listed in high_null_columns
HIGH_NULL_THRESHOLD: float = 20.0

# Maximum sample values to include per column
SAMPLE_SIZE: int = 5

# Semantic types that receive NumericStats and top-level min/max/avg
_NUMERIC_TYPES  = {"integer", "float", "decimal"}

# Semantic types that receive CategoricalStats
_CATEGORY_TYPES = {"text", "boolean", "categorical"}

# Semantic types that receive DateStats and top-level min/max
_DATE_TYPES     = {"date", "datetime", "time", "duration"}


# ---------------------------------------------------------------------------
# PRIVATE: build one ColumnProfile with full row data  (CSV path)
# ---------------------------------------------------------------------------

def _profile_with_data(series: pd.Series, col_meta) -> ColumnProfile:
    """
    Build a complete ColumnProfile for a column that has real row data.

    All edge cases are handled explicitly:
      - Empty / all-null series       → is_empty=True, stats zeroed
      - Mixed-type object columns     → mixed_type_detail populated
      - Numeric columns with dirt     → detect_numeric strips symbols, tolerates 10 %
      - Date columns, string format   → detect_datetime pre-screens with regex
      - Single-value series           → unique_count=1, possible_primary_key=False
    """
    raw_type   = col_meta.raw_type or "unknown"
    total      = len(series)
    null_count = int(series.isna().sum())
    null_pct   = round(null_count / total * 100, 2) if total > 0 else 0.0

    # ── Empty guard ─────────────────────────────────────────────────────────
    empty = is_empty_series(series)

    # ── Uniqueness ───────────────────────────────────────────────────────────
    u_report        = score_uniqueness(series)
    unique_count    = u_report["unique_count"]
    unique_pct      = u_report["unique_pct"]
    uniqueness_ratio = u_report["uniqueness_ratio"]
    is_pk           = u_report["is_pk_candidate"]

    uniqueness_detail = UniquenessDetail(
        unique_count      = unique_count,
        unique_pct        = unique_pct,
        uniqueness_ratio  = uniqueness_ratio,
        has_duplicates    = u_report["has_duplicates"],
        duplicate_count   = u_report["duplicate_count"],
        most_frequent_val = u_report["most_frequent_val"],
        most_frequent_cnt = u_report["most_frequent_cnt"],
    )

    # ── Mixed-type detection ─────────────────────────────────────────────────
    mt_report = detect_mixed_types(series)
    mixed_detail = MixedTypeDetail(
        is_mixed        = mt_report["is_mixed"],
        type_counts     = mt_report["type_counts"],
        dominant_type   = mt_report["dominant_type"],
        malformed_count = mt_report["malformed_count"],
    )

    # ── Type inference ───────────────────────────────────────────────────────
    inferred = infer_type(series, raw_type)

    # ── Type-specific stats + top-level min / max / avg ──────────────────────
    numeric_stats    = None
    date_stats       = None
    categorical_stats = None

    col_min: Optional[Any] = None
    col_max: Optional[Any] = None
    col_avg: Optional[float] = None

    non_null = series.dropna()

    if not empty:
        if inferred in _NUMERIC_TYPES:
            numeric_stats = compute_numeric_stats(non_null)
            col_min = numeric_stats.min
            col_max = numeric_stats.max
            col_avg = numeric_stats.mean

        elif inferred in _DATE_TYPES:
            date_stats = compute_date_stats(series)
            col_min = date_stats.min_date
            col_max = date_stats.max_date
            # avg is not meaningful for dates

        elif inferred in _CATEGORY_TYPES:
            categorical_stats = compute_categorical_stats(non_null)

    # ── Sample values ────────────────────────────────────────────────────────
    sample = safe_list(non_null.head(SAMPLE_SIZE).tolist()) if not empty else []

    return ColumnProfile(
        column_name          = col_meta.name,
        raw_type             = raw_type,
        inferred_data_type   = inferred,
        total_count          = total,
        null_count           = null_count,
        null_percentage      = null_pct,
        unique_count         = unique_count,
        unique_percentage    = unique_pct,
        possible_primary_key = is_pk,
        min                  = col_min,
        max                  = col_max,
        avg                  = col_avg,
        sample_values        = sample,
        is_empty             = empty,
        mixed_type_detail    = mixed_detail,
        uniqueness_detail    = uniqueness_detail,
        numeric_stats        = numeric_stats,
        date_stats           = date_stats,
        categorical_stats    = categorical_stats,
    )


# ---------------------------------------------------------------------------
# PRIVATE: build one ColumnProfile from schema only  (DDL path)
# ---------------------------------------------------------------------------

def _profile_schema_only(col_meta) -> ColumnProfile:
    """
    Build a minimal ColumnProfile when only schema metadata is available.

    No row data → all statistical fields are None or 0.
    Semantic type is inferred from the SQL type keyword via type_mapper.
    possible_primary_key is always False (cannot confirm without data).
    """
    from schema_inference.type_mapper import map_sql_type

    raw_type = col_meta.raw_type or "unknown"
    inferred = map_sql_type(raw_type)   # e.g. "VARCHAR" → "text"

    return ColumnProfile(
        column_name          = col_meta.name,
        raw_type             = raw_type,
        inferred_data_type   = inferred,
        total_count          = 0,
        null_count           = 0,
        null_percentage      = 0.0,
        unique_count         = 0,
        unique_percentage    = 0.0,
        possible_primary_key = False,
        sample_values        = safe_list(col_meta.sample_values or []),
        is_empty             = False,
    )


# ---------------------------------------------------------------------------
# PRIVATE: build the TableSummary from all column profiles
# ---------------------------------------------------------------------------

def _build_table_summary(
    table_name:  str,
    profiles:    List[ColumnProfile],
    row_count:   int,
    schema_only: bool,
) -> TableSummary:
    """
    Aggregate per-column profiles into a single table-level summary.

    Useful lists for LLM consumption:
      pk_candidates      — columns safe to use as a primary key
      high_null_columns  — data quality flag
      constant_columns   — single-value columns (no modelling value)
      empty_columns      — 100 % null columns
      mixed_type_columns — heterogeneous columns needing normalisation
    """
    total_cells = row_count * len(profiles)
    total_nulls = sum(p.null_count for p in profiles)

    completeness = (
        round((total_cells - total_nulls) / total_cells * 100, 2)
        if total_cells > 0
        else 100.0   # Schema-only: no data to contradict
    )

    return TableSummary(
        table_name               = table_name,
        row_count                = row_count,
        column_count             = len(profiles),
        total_null_cells         = total_nulls,
        overall_completeness_pct = completeness,

        # PK / quality classification
        pk_candidates       = [p.column_name for p in profiles if p.possible_primary_key],
        high_null_columns   = [p.column_name for p in profiles if p.null_percentage > HIGH_NULL_THRESHOLD],
        constant_columns    = [p.column_name for p in profiles if p.unique_count == 1],
        empty_columns       = [p.column_name for p in profiles if p.is_empty],
        mixed_type_columns  = [
            p.column_name for p in profiles
            if p.mixed_type_detail and p.mixed_type_detail.is_mixed
        ],

        # Type classification
        numeric_columns     = [p.column_name for p in profiles if p.inferred_data_type in _NUMERIC_TYPES],
        text_columns        = [p.column_name for p in profiles if p.inferred_data_type == "text"],
        date_columns        = [p.column_name for p in profiles if p.inferred_data_type in _DATE_TYPES],
        boolean_columns     = [p.column_name for p in profiles if p.inferred_data_type == "boolean"],

        schema_only         = schema_only,
    )


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def profile_table(input_data: dict) -> ProfilingResult:
    """
    Main entry point for the profiling layer.

    Parameters
    ----------
    input_data : dict
        Must contain:
          "table_name" : str
          "columns"    : list[dict]  — column metadata (csv_parser OR ddl_parser format)
          "rows"       : list[dict]  — row data (optional; omit/pass [] for DDL-only)

    Returns
    -------
    ProfilingResult
        Call .model_dump()      for a JSON-safe dict.
        Call .model_dump_json() for a JSON string.

    Raises
    ------
    pydantic.ValidationError  — when input_data fails schema validation.
    ValueError                — when table_name is missing.
    """
    # Validate and normalise (handles csv key "raw_dtype" vs ddl key "type")
    inp = ProfilerInput(**input_data)

    schema_only = len(inp.rows) == 0
    profiles: List[ColumnProfile] = []

    if schema_only:
        # ── DDL path ─────────────────────────────────────────────────────────
        for col_meta in inp.columns:
            profiles.append(_profile_schema_only(col_meta))
        row_count = 0

    else:
        # ── CSV path ─────────────────────────────────────────────────────────
        df = pd.DataFrame(inp.rows)
        row_count = len(df)

        for col_meta in inp.columns:
            col_name = col_meta.name

            if col_name not in df.columns:
                # Column declared in header but absent in row data → all-null series
                series = pd.Series(
                    [None] * row_count, name=col_name, dtype=object
                )
            else:
                series = df[col_name]

            profiles.append(_profile_with_data(series, col_meta))

    summary = _build_table_summary(
        table_name  = inp.table_name,
        profiles    = profiles,
        row_count   = row_count,
        schema_only = schema_only,
    )

    return ProfilingResult(
        table_name      = inp.table_name,
        column_profiles = profiles,
        table_summary   = summary,
    )
