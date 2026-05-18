import pandas as pd
import io

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
    if len(df.columns) == 0:
     return {"error": "CSV has no columns"}

    if df.empty:
     return {"error": "CSV has no rows"} 
    
    # Detect accidental unnamed columns 
    unnamed = [c for c in df.columns if str(c).startswith("Unnamed")]
    if unnamed:
        return {"error": f"CSV has unnamed columns: {unnamed}."}

    # Extract BASIC structure (Tables, Columns, Data)
    columns_structure = []
    for col in df.columns:
        columns_structure.append({
            "name": col,
            "raw_dtype": str(df[col].dtype),
            "sample_values": df[col].dropna().head(3).tolist()  # raw values
        })

    return {
        "source_type": "csv",
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": columns_structure,
        # Pass a 5-row sample preview downstream. 
        # Missing data is explicitly labeled as "NULL" strings to avoid issues during downstream JSON processing.
        "sample_rows": df.head(5).fillna("NULL").to_dict(orient="records")
    }

