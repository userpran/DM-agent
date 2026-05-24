"""
profiling/models.py

Pydantic models for the profiling layer.

INPUT models  — what the profiler accepts (source-agnostic):
  ProfilerColumnMeta  normalises csv_parser key "raw_dtype" and
                      ddl_parser key "type" into a single "raw_type".
  ProfilerInput       table_name + columns + optional rows.

OUTPUT models — what the profiler returns:
  NumericStats        min/max/mean/median/std/percentiles/zero/negative counts
  DateStats           min_date / max_date / date_range_days
  ValueFrequency      one (value, count, pct) entry
  CategoricalStats    top-N frequency distribution
  UniquenessDetail    duplicate count, most-frequent value
  MixedTypeDetail     type-bucket breakdown for object columns
  ColumnProfile       full per-column report
  TableSummary        rolled-up table insights
  ProfilingResult     column_profiles + table_summary
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# INPUT
# ---------------------------------------------------------------------------

class ProfilerColumnMeta(BaseModel):
    """
    Normalises the different dtype key names from each parser into raw_type.
    csv_parser uses "raw_dtype"; ddl_parser uses "type".
    """
    name: str

    raw_dtype: Optional[str] = Field(default=None, exclude=True)  # csv_parser
    type:      Optional[str] = Field(default=None, exclude=True)  # ddl_parser
    raw_type:  Optional[str] = None                               # canonical

    raw_definition: Optional[str]      = None  # DDL full column definition
    sample_values:  Optional[List[Any]] = []   # CSV pre-extracted samples

    @model_validator(mode="after")
    def resolve_raw_type(self) -> "ProfilerColumnMeta":
        if not self.raw_type:
            self.raw_type = self.raw_dtype or self.type or "unknown"
        return self


class ProfilerInput(BaseModel):
    """Source-agnostic input for profile_table(). Empty rows → schema-only mode."""
    table_name: str
    columns:    List[ProfilerColumnMeta]
    rows:       List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# OUTPUT — statistics blocks
# ---------------------------------------------------------------------------

class NumericStats(BaseModel):
    """Descriptive statistics for integer / float / decimal columns."""
    min:            Optional[float] = None
    max:            Optional[float] = None
    mean:           Optional[float] = None
    median:         Optional[float] = None
    std_dev:        Optional[float] = None
    p25:            Optional[float] = None  # 25th percentile
    p75:            Optional[float] = None  # 75th percentile
    zero_count:     int = 0
    negative_count: int = 0


class DateStats(BaseModel):
    """Min / max / range for date and datetime columns."""
    min_date:        Optional[str] = None   # ISO date string
    max_date:        Optional[str] = None   # ISO date string
    date_range_days: Optional[int] = None


class ValueFrequency(BaseModel):
    """One value with its frequency count and percentage of non-null rows."""
    value: Any
    count: int
    pct:   float


class CategoricalStats(BaseModel):
    """Top-N value frequency distribution for text / boolean columns."""
    unique_count: int = 0
    top_values:   List[ValueFrequency] = []


class UniquenessDetail(BaseModel):
    """Extended uniqueness report — duplicates and most-frequent value."""
    unique_count:      int   = 0
    unique_pct:        float = 0.0   # unique_count / total_count × 100
    uniqueness_ratio:  float = 0.0   # unique_count / total_count  (0–1)
    has_duplicates:    bool  = False
    duplicate_count:   int   = 0
    most_frequent_val: Any   = None
    most_frequent_cnt: int   = 0


class MixedTypeDetail(BaseModel):
    """Type-bucket breakdown for object-dtype columns with heterogeneous values."""
    is_mixed:        bool           = False
    type_counts:     Dict[str, int] = {}   # {"str": N, "int": N, ...}
    dominant_type:   str            = "unknown"
    malformed_count: int            = 0    # values outside str/int/float/bool


# ---------------------------------------------------------------------------
# OUTPUT — column profile
# ---------------------------------------------------------------------------

class ColumnProfile(BaseModel):
    """
    Complete profile for one column.

    Core fields  column_name, inferred_data_type, null_count, null_percentage,
                 unique_count, unique_percentage, sample_values, min, max, avg,
                 possible_primary_key

    Extended     raw_type, total_count, is_empty, uniqueness_detail,
                 mixed_type_detail, numeric_stats, date_stats, categorical_stats
    """
    # Identity
    column_name:         str
    raw_type:            str    # original dtype string from parser
    inferred_data_type:  str    # semantic label: integer|float|text|date|boolean|…

    # Volume
    total_count:         int
    null_count:          int
    null_percentage:     float  # null_count / total_count × 100

    # Uniqueness
    unique_count:        int
    unique_percentage:   float  # unique_count / total_count × 100

    # PK candidacy
    possible_primary_key: bool  # True when 0 nulls AND all values unique

    # Top-level min / max / avg
    min: Optional[Any]   = None  # numeric value or ISO date string
    max: Optional[Any]   = None  # numeric value or ISO date string
    avg: Optional[float] = None  # numeric mean only

    # Samples
    sample_values: List[Any] = []  # up to 5 non-null examples

    # Quality flags
    is_empty: bool = False

    # Detail blocks (None for DDL / schema-only path)
    uniqueness_detail:  Optional[UniquenessDetail]  = None
    mixed_type_detail:  Optional[MixedTypeDetail]   = None
    numeric_stats:      Optional[NumericStats]       = None
    date_stats:         Optional[DateStats]          = None
    categorical_stats:  Optional[CategoricalStats]   = None


# ---------------------------------------------------------------------------
# OUTPUT — table summary + top-level result
# ---------------------------------------------------------------------------

class TableSummary(BaseModel):
    """Rolled-up table insights for LLM / downstream layer consumption."""
    table_name:               str
    row_count:                int
    column_count:             int
    total_null_cells:         int
    overall_completeness_pct: float

    # Column classification lists
    pk_candidates:      List[str]   # zero nulls + all-unique
    high_null_columns:  List[str]   # null_pct > 20 %
    constant_columns:   List[str]   # only 1 unique value
    empty_columns:      List[str]   # 100 % null
    mixed_type_columns: List[str]   # heterogeneous object columns
    numeric_columns:    List[str]
    text_columns:       List[str]
    date_columns:       List[str]
    boolean_columns:    List[str]

    schema_only: bool  # True when profiled from DDL (no row data)


class ProfilingResult(BaseModel):
    """Final output of profile_table(). Call .model_dump() for JSON-safe dict."""
    table_name:      str
    column_profiles: List[ColumnProfile]
    table_summary:   TableSummary
