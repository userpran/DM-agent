"""
profiling/utils.py

Low-level helper functions for computing column statistics.
All functions operate on a single pandas Series and return
only JSON-serialisable Python types (no numpy scalars or NaN).

Helpers:
  is_empty_series     — all-null / zero-length guard
  safe_scalar / list  — numpy → native Python conversion
  detect_numeric      — checks if a series can be coerced to float
  detect_datetime     — checks if a series looks like dates
  infer_type          — layered semantic type inference
  score_uniqueness    — uniqueness ratio + duplicate report
  detect_mixed_types  — type-bucket breakdown for object columns
  compute_numeric_stats    — min/max/mean/median/std/percentiles
  compute_date_stats       — date min/max/range
  compute_categorical_stats — top-N frequency distribution
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

import numpy as np
import pandas as pd

from profiling.models import CategoricalStats, DateStats, NumericStats, ValueFrequency


# ---------------------------------------------------------------------------
# GUARD: is the series empty or entirely null?
# ---------------------------------------------------------------------------

def is_empty_series(series: pd.Series) -> bool:
    """
    Return True when the series has no data worth analysing:
      - zero length, OR
      - every value is NaN / None / pd.NaT

    Used at the top of every helper to short-circuit safely.
    """
    if series is None or len(series) == 0:
        return True
    return series.isna().all()


# ---------------------------------------------------------------------------
# JSON-SAFE SCALAR CONVERSION
# ---------------------------------------------------------------------------

def safe_scalar(val: Any) -> Any:
    """
    Convert a numpy scalar to a native Python type for JSON serialisation.

      - numpy integers  → int
      - numpy floats    → float   (NaN / Inf → None)
      - numpy booleans  → bool
      - pandas NaT      → None
      - everything else → unchanged
    """
    if val is None:
        return None
    if val is pd.NaT:
        return None
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    return val


def safe_list(values: list) -> list:
    """Apply safe_scalar to every element in a list."""
    return [safe_scalar(v) for v in values]


def safe_round(val: Any, ndigits: int = 4) -> Optional[float]:
    """Round a value safely, returning None on NaN / None input."""
    v = safe_scalar(val)
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# NUMERIC DETECTION
# ---------------------------------------------------------------------------

def detect_numeric(series: pd.Series) -> Tuple[bool, float]:
    """
    Determine whether a column is numeric and how cleanly it parses.

    Strategy
    --------
    1. If pandas dtype is already numeric (int, float) → immediately True, 1.0.
    2. For object dtype columns, attempt pd.to_numeric on non-null values.
       A column is considered "numeric" when ≥ 90 % of non-null values parse
       successfully (the 10 % tolerance handles occasional dirty cells).

    Parameters
    ----------
    series : Raw column Series (may contain nulls and mixed types).

    Returns
    -------
    (is_numeric: bool, parse_rate: float)
        parse_rate is the fraction of non-null values that successfully
        converted to a number (0.0 – 1.0). Always 1.0 for native numeric dtypes.

    Handles
    -------
    - Empty / all-null series          → (False, 0.0)
    - Mixed type object columns        → (True/False depending on threshold)
    - Numeric strings with whitespace  → stripped before coercion
    - Currency / percentage symbols    → stripped (e.g. "$1,200" → 1200)
    """
    if is_empty_series(series):
        return False, 0.0

    # Native numeric dtype — no coercion needed
    if pd.api.types.is_numeric_dtype(series):
        return True, 1.0

    non_null = series.dropna()
    if non_null.empty:
        return False, 0.0

    # Strip common non-numeric decorators from string values before coercion
    cleaned = (
        non_null.astype(str)
        .str.strip()
        .str.replace(r"[$,%£€¥]", "", regex=True)   # currency / percentage
        .str.replace(r",", "", regex=False)           # thousands separator
    )

    coerced   = pd.to_numeric(cleaned, errors="coerce")
    parse_rate = coerced.notna().sum() / len(non_null)

    return parse_rate >= 0.90, round(float(parse_rate), 4)


# ---------------------------------------------------------------------------
# DATETIME DETECTION
# ---------------------------------------------------------------------------

# Regex patterns for common date/time formats — checked before pandas coercion
# so we can reject obviously non-date strings fast and avoid false positives.
_DATE_PATTERNS = [
    r"^\d{4}-\d{2}-\d{2}$",                         # 2024-01-15
    r"^\d{2}[/\-\.]\d{2}[/\-\.]\d{4}$",             # 15/01/2024 or 15-01-2024
    r"^\d{4}[/\-\.]\d{2}[/\-\.]\d{2}$",             # 2024/01/15
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}",           # ISO 8601 datetime
    r"^\d{2}:\d{2}(:\d{2})?$",                       # Time only
    r"^\d{1,2}\s+\w+\s+\d{4}$",                      # 15 Jan 2024
    r"^\w+\s+\d{1,2},?\s+\d{4}$",                    # January 15, 2024
]
_DATE_RE = [re.compile(p) for p in _DATE_PATTERNS]


def _looks_like_date_string(val: str) -> bool:
    """Quick regex pre-screen: does this string resemble a date/time value?"""
    val = val.strip()
    return any(pattern.match(val) for pattern in _DATE_RE)


def detect_datetime(series: pd.Series) -> Tuple[bool, Optional[str]]:
    """
    Determine whether a column contains date or datetime values.

    Strategy
    --------
    1. If pandas dtype is already datetime64 → immediately True.
    2. For object/string columns:
       a. Sample up to 200 non-null values for speed.
       b. Pre-screen with regex patterns — at least 80 % must look date-like.
       c. Attempt pd.to_datetime with dayfirst=True, errors="coerce".
       d. Require ≥ 80 % successful parse rate.
    3. Detect whether it is date-only or datetime by checking for time
       components in the successfully parsed values.

    Returns
    -------
    (is_datetime: bool, sub_type: Optional[str])
        sub_type is "date", "datetime", or "time" when is_datetime is True;
        None otherwise.

    Handles
    -------
    - Empty / all-null series       → (False, None)
    - Numeric columns               → (False, None)  (integers are not dates)
    - Strings that look like years  → treated as integer, not date
    - Mixed date formats            → True if 80 %+ parse
    """
    if is_empty_series(series):
        return False, None

    # Already a datetime dtype
    if pd.api.types.is_datetime64_any_dtype(series):
        return True, "datetime"

    # Pure numeric dtypes are not dates (epoch ints are out of scope here)
    if pd.api.types.is_numeric_dtype(series):
        return False, None

    non_null = series.dropna()
    if non_null.empty:
        return False, None

    sample = non_null.astype(str).head(200)

    # Pre-screen: require 80 % of sampled values to match a date regex pattern
    regex_hits = sample.apply(_looks_like_date_string).sum()
    if regex_hits / len(sample) < 0.80:
        return False, None

    # Attempt coercion
    try:
        parsed = pd.to_datetime(sample, dayfirst=True, errors="coerce")
    except Exception:
        return False, None

    parse_rate = parsed.notna().sum() / len(sample)
    if parse_rate < 0.80:
        return False, None

    # Distinguish date-only vs datetime by checking time components
    has_time = parsed.dropna().apply(
        lambda dt: dt.hour != 0 or dt.minute != 0 or dt.second != 0
    ).any()

    sub_type = "datetime" if has_time else "date"
    return True, sub_type


# ---------------------------------------------------------------------------
# UNIQUENESS SCORING
# ---------------------------------------------------------------------------

def score_uniqueness(series: pd.Series) -> dict:
    """
    Compute a structured uniqueness report for a column.

    Returns a dict (not a Pydantic model so it stays lightweight and embeddable):
    {
        "unique_count"      : int,    # distinct non-null values
        "unique_pct"        : float,  # unique_count / total_count × 100
        "uniqueness_ratio"  : float,  # unique_count / total_count (0.0 – 1.0)
        "is_pk_candidate"   : bool,   # True when 0 nulls AND all values unique
        "has_duplicates"    : bool,
        "duplicate_count"   : int,    # non-null rows that are not unique
        "most_frequent_val" : Any,    # value appearing most often (None if empty)
        "most_frequent_cnt" : int,    # occurrence count of the most frequent value
    }

    Handles
    -------
    - Empty / all-null series  → zeroed-out dict, is_pk_candidate=False
    - Single-row tables        → unique_pct=100, is_pk_candidate=True if not null
    - All-duplicate columns    → duplicate_count == total_count - 1
    """
    total = len(series)
    null_count = int(series.isna().sum())
    non_null = series.dropna()

    if is_empty_series(series) or non_null.empty:
        return {
            "unique_count":      0,
            "unique_pct":        0.0,
            "uniqueness_ratio":  0.0,
            "is_pk_candidate":   False,
            "has_duplicates":    False,
            "duplicate_count":   0,
            "most_frequent_val": None,
            "most_frequent_cnt": 0,
        }

    unique_count    = int(non_null.nunique())
    uniqueness_ratio = round(unique_count / total, 4)
    unique_pct       = round(uniqueness_ratio * 100, 2)
    is_pk            = (null_count == 0 and unique_count == total and total > 0)

    # Duplicates = non-null rows whose value appears more than once
    vc             = non_null.value_counts()
    duplicate_count = int((vc[vc > 1] - 1).sum())   # extra occurrences beyond first

    most_freq_val = safe_scalar(vc.index[0]) if not vc.empty else None
    most_freq_cnt = int(vc.iloc[0])          if not vc.empty else 0

    return {
        "unique_count":      unique_count,
        "unique_pct":        unique_pct,
        "uniqueness_ratio":  uniqueness_ratio,
        "is_pk_candidate":   is_pk,
        "has_duplicates":    duplicate_count > 0,
        "duplicate_count":   duplicate_count,
        "most_frequent_val": most_freq_val,
        "most_frequent_cnt": most_freq_cnt,
    }


# ---------------------------------------------------------------------------
# MIXED-TYPE DETECTION
# ---------------------------------------------------------------------------

def detect_mixed_types(series: pd.Series) -> dict:
    """
    Identify columns where values belong to more than one Python type.

    Only meaningful for object-dtype columns; all others are inherently homogeneous.

    Returns
    -------
    {
        "is_mixed"        : bool,
        "type_counts"     : {"int": N, "float": N, "str": N, "bool": N, "other": N},
        "dominant_type"   : str,   # Python type name with highest count
        "malformed_count" : int,   # Values that are not str/int/float/bool/None
    }

    Handles
    -------
    - Native numeric dtypes → is_mixed=False immediately
    - Empty series          → zeroed result
    - NaN / None values     → excluded from type counting (they are nulls)
    """
    empty_result = {
        "is_mixed":        False,
        "type_counts":     {},
        "dominant_type":   "unknown",
        "malformed_count": 0,
    }

    if is_empty_series(series):
        return empty_result

    # Non-object dtypes are homogeneous by definition
    if series.dtype != object:
        return {
            "is_mixed":        False,
            "type_counts":     {series.dtype.name: int(series.notna().sum())},
            "dominant_type":   series.dtype.name,
            "malformed_count": 0,
        }

    non_null = series.dropna()
    if non_null.empty:
        return empty_result

    # Map each value to a simplified type bucket
    type_counts: dict[str, int] = {"int": 0, "float": 0, "str": 0, "bool": 0, "other": 0}
    malformed = 0

    for val in non_null:
        if isinstance(val, bool):          # bool must come before int (bool subclasses int)
            type_counts["bool"] += 1
        elif isinstance(val, int):
            type_counts["int"]  += 1
        elif isinstance(val, float):
            type_counts["float"] += 1
        elif isinstance(val, str):
            type_counts["str"]  += 1
        else:
            type_counts["other"] += 1
            malformed += 1

    # Remove zero-count buckets for cleaner output
    type_counts = {k: v for k, v in type_counts.items() if v > 0}
    distinct_types = len(type_counts)

    dominant = max(type_counts, key=type_counts.get) if type_counts else "unknown"

    return {
        "is_mixed":        distinct_types > 1,
        "type_counts":     type_counts,
        "dominant_type":   dominant,
        "malformed_count": malformed,
    }


# ---------------------------------------------------------------------------
# TYPE INFERENCE  (layered, coercion-based)
# ---------------------------------------------------------------------------

def infer_type(series: pd.Series, raw_type: str = "") -> str:
    """
    Infer the semantic type of a pandas Series using a layered strategy.

    Layer 0 — Empty / all-null  → "unknown"
    Layer 1 — Trust specific pandas dtypes (not "object") via type_mapper
    Layer 2 — Coercion for object dtype:
                2a. Numeric detection  (≥ 90 % parse rate)
                2b. Datetime detection (≥ 80 % parse rate, regex pre-screened)
                2c. Boolean-string check
                2d. Fallback → "text"

    Parameters
    ----------
    series   : The column data (may include nulls).
    raw_type : Original dtype/type string from the parser.

    Returns a semantic type from the controlled vocabulary:
        integer | float | decimal | text | boolean | date | datetime |
        time | duration | categorical | json | uuid | binary | unknown
    """
    from schema_inference.type_mapper import map_pandas_type

    # Layer 0: empty / all-null
    if is_empty_series(series):
        return "unknown"

    # Layer 1: trust known non-object pandas dtypes
    if raw_type and raw_type.lower() != "object":
        mapped = map_pandas_type(raw_type.lower())
        if mapped != "unknown":
            return mapped

    non_null = series.dropna()
    if non_null.empty:
        return "unknown"

    # Layer 2a: numeric
    is_num, _ = detect_numeric(series)
    if is_num:
        # Distinguish integer vs float (use coerced values, not original dtype)
        coerced = pd.to_numeric(non_null, errors="coerce").dropna()
        try:
            if not coerced.empty and (coerced % 1 == 0).all():
                return "integer"
            return "float"
        except Exception:
            return "float"

    # Layer 2b: datetime
    is_dt, sub_type = detect_datetime(series)
    if is_dt:
        return sub_type or "datetime"

    # Layer 2c: boolean-string
    _BOOL_STRINGS = {"true", "false", "yes", "no", "1", "0", "t", "f", "y", "n"}
    unique_lower = set(non_null.astype(str).str.strip().str.lower().unique())
    if unique_lower and unique_lower.issubset(_BOOL_STRINGS):
        return "boolean"

    # Layer 2d: fallback
    return "text"


# ---------------------------------------------------------------------------
# NUMERIC STATISTICS
# ---------------------------------------------------------------------------

def compute_numeric_stats(series: pd.Series) -> NumericStats:
    """
    Compute descriptive statistics for a numeric column.

    - Coerces to float before computation (handles mixed object columns).
    - Non-numeric values become NaN and are silently excluded.
    - Returns an empty NumericStats (all None) when no valid numbers exist.

    Handles
    -------
    - All-null series             → NumericStats() with all None
    - Single-value series         → std_dev=None (undefined), all others computed
    - Overflow-safe rounding      → safe_round used on all float fields
    """
    if is_empty_series(series):
        return NumericStats()

    numeric = pd.to_numeric(series, errors="coerce").dropna()

    if numeric.empty:
        return NumericStats()

    std = numeric.std()

    return NumericStats(
        min           = safe_scalar(numeric.min()),
        max           = safe_scalar(numeric.max()),
        mean          = safe_round(numeric.mean(),   6),
        median        = safe_round(numeric.median(), 4),
        std_dev       = safe_round(std,              6) if len(numeric) > 1 else None,
        p25           = safe_round(numeric.quantile(0.25), 4),
        p75           = safe_round(numeric.quantile(0.75), 4),
        zero_count    = int((numeric == 0).sum()),
        negative_count= int((numeric < 0).sum()),
    )


# ---------------------------------------------------------------------------
# DATETIME STATISTICS
# ---------------------------------------------------------------------------

def compute_date_stats(series: pd.Series) -> DateStats:
    """
    Compute min / max / range for datetime columns.

    Handles
    -------
    - Object dtype strings → coerced with pd.to_datetime, errors ignored
    - Native datetime64   → used directly
    - All-null / no-parse → DateStats() with all None
    - Mixed formats       → best-effort parse; unparseable values excluded
    """
    if is_empty_series(series):
        return DateStats()

    try:
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = series.dropna()
        else:
            parsed = pd.to_datetime(series, dayfirst=True, errors="coerce").dropna()
    except Exception:
        return DateStats()

    if parsed.empty:
        return DateStats()

    dt_min = parsed.min()
    dt_max = parsed.max()
    delta  = dt_max - dt_min

    return DateStats(
        min_date      = str(dt_min.date()) if hasattr(dt_min, "date") else str(dt_min),
        max_date      = str(dt_max.date()) if hasattr(dt_max, "date") else str(dt_max),
        date_range_days = int(delta.days) if hasattr(delta, "days") else None,
    )


# ---------------------------------------------------------------------------
# CATEGORICAL STATISTICS
# ---------------------------------------------------------------------------

def compute_categorical_stats(series: pd.Series, top_n: int = 5) -> CategoricalStats:
    """
    Compute value-frequency distribution for text / boolean / categorical columns.

    Parameters
    ----------
    series : Full column Series (nulls are dropped internally).
    top_n  : Maximum number of top values to return (default 5).

    Handles
    -------
    - Empty / all-null series → CategoricalStats(unique_count=0)
    - Very high cardinality   → only top_n returned; unique_count still reflects full column
    - Numeric values in an    → converted to str for counting (mixed-type object columns)
      object column
    """
    non_null = series.dropna()

    if non_null.empty:
        return CategoricalStats(unique_count=0)

    unique_count   = int(non_null.nunique())
    total_non_null = len(non_null)
    counts         = non_null.astype(str).value_counts().head(top_n)

    top_values = [
        ValueFrequency(
            value = safe_scalar(val),
            count = int(cnt),
            pct   = round(int(cnt) / total_non_null * 100, 2),
        )
        for val, cnt in counts.items()
    ]

    return CategoricalStats(unique_count=unique_count, top_values=top_values)
