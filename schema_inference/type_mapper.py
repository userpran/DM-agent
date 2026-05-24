"""
Type mapping tables for converting raw dtype strings → semantic type labels.

Two separate maps handle the two source formats:
  - PANDAS_TYPE_MAP  : for CSV columns (pandas dtype strings like "int64", "object")
  - SQL_TYPE_MAP     : for DDL columns (SQL keyword strings like "VARCHAR", "TIMESTAMP")

semantic types are a small controlled vocabulary:
  integer | float | decimal | text | boolean | date | time | datetime |
  duration | categorical | json | uuid | binary | array | unknown
"""


# Maps pandas dtype strings (as they appear in df[col].dtype) to semantic types
PANDAS_TYPE_MAP: dict[str, str] = {
    "int8":               "integer",
    "int16":              "integer",
    "int32":              "integer",
    "int64":              "integer",
    "uint8":              "integer",
    "uint16":             "integer",
    "uint32":             "integer",
    "uint64":             "integer",
    "float32":            "float",
    "float64":            "float",
    "object":             "text",
    "bool":               "boolean",
    "datetime64[ns]":     "datetime",
    "datetime64[ns, utc]":"datetime",
    "timedelta64[ns]":    "duration",
    "category":           "categorical",
}

# Maps SQL data type keywords (uppercase, without precision) to semantic types
SQL_TYPE_MAP: dict[str, str] = {
    # Integer family
    "INT":        "integer",
    "INTEGER":    "integer",
    "BIGINT":     "integer",
    "SMALLINT":   "integer",
    "TINYINT":    "integer",
    "SERIAL":     "integer",
    "BYTEINT":    "integer",

    # Float family
    "FLOAT":      "float",
    "REAL":       "float",
    "DOUBLE":     "float",

    # Decimal / precise numeric
    "DECIMAL":    "decimal",
    "NUMERIC":    "decimal",
    "MONEY":      "decimal",

    # Text family
    "VARCHAR":    "text",
    "NVARCHAR":   "text",
    "CHAR":       "text",
    "NCHAR":      "text",
    "TEXT":       "text",
    "CLOB":       "text",
    "STRING":     "text",

    # Date / time family
    "DATE":       "date",
    "TIME":       "time",
    "TIMESTAMP":  "datetime",
    "DATETIME":   "datetime",

    # Boolean
    "BOOLEAN":    "boolean",
    "BOOL":       "boolean",
    "BIT":        "boolean",

    # Binary
    "BLOB":       "binary",
    "BYTEA":      "binary",
    "VARBINARY":  "binary",
    "BINARY":     "binary",

    # Semi-structured
    "JSON":       "json",
    "JSONB":      "json",
    "VARIANT":    "json",

    # Other
    "UUID":       "uuid",
    "ARRAY":      "array",
    "OBJECT":     "json",
}


def map_pandas_type(dtype_str: str) -> str:
    """
    Return a semantic type label for a pandas dtype string.
    Falls back to 'unknown' if no mapping exists.
    """
    return PANDAS_TYPE_MAP.get(dtype_str.lower(), "unknown")


def map_sql_type(sql_type_str: str) -> str:
    """
    Return a semantic type label for a SQL column type string.
    Strips precision specs (e.g. VARCHAR(255) → VARCHAR) before lookup.
    Falls back to 'unknown' if no mapping exists.
    """
    # Remove anything in parentheses e.g. DECIMAL(10,2) → DECIMAL
    base_type = sql_type_str.upper().split("(")[0].strip()
    return SQL_TYPE_MAP.get(base_type, "unknown")
