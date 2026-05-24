import pandas as pd
import io


def _rows_for_profiling(df: pd.DataFrame) -> list:
    """All data rows as JSON-safe dicts (NaN → None) for the profiling layer."""
    return df.where(pd.notna(df), None).to_dict(orient="records")


def parse_csv(content: bytes) -> dict:
    """
    Ingests raw CSV bytes and safely extracts its basic layout.
    It passes structural metadata and a small data sample forward
    """

    # check if csv is empty
    if not content or len(content) == 0:
       return {"error": "CSV file is empty"}

    try:
        df = pd.read_csv(io.BytesIO(content)) # Wrap the raw bytes in an in-memory stream so pandas can read it like a local file.
    except Exception as e:
       return {"error": f"Could not parse CSV: {str(e)}"}
    
    # Ensure the dataset actually has structural shape (columns and rows)
    if len(df.columns) == 0: #length=0 indicates the first row of the csv is empty or just delimiters with no actual column names (i.e., the file is structurally blank)
      return {"error": "CSV has no columns"}

    if df.empty: #checks if there are no rows of data (even if column headers exist)
      return {"error": "CSV has no data rows"}
    
    # Identify and reject dummy headers created by trailing commas or empty columns (pandas automatically labels them as as "Unnamed: x")
    unnamed = [c for c in df.columns if str(c).startswith("Unnamed")] 
    if unnamed:
       return {"error": f"CSV has unnamed columns: {unnamed}."}

    # Extract BASIC structure (Tables, Columns, Data)
    columns_structure = []
    for col in df.columns:
        columns_structure.append({
            "name": col,
            "raw_dtype": str(df[col].dtype),
             # Temporarily strip missing/blank(NaN) fields, isolate the top 3 records, and convert the pandas Series to a standard Python list
            "sample_values": df[col].dropna().head(3).tolist() 
            }) 
    return {
        "source_type": "csv",
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": columns_structure,
        # 5-row preview for API responses (parse_output)
        "sample_rows": df.head(5).fillna("NULL").to_dict(orient="records"),
        # Full dataset for profiling (omitted from API parse_output by pipeline runner)
        "rows": _rows_for_profiling(df),
    }
