from fastapi import FastAPI, UploadFile, File
import pandas as pd
import sqlparse
import io

# Main FastAPI application for ingestion + parsing
app = FastAPI()


# -----------------------------
# CSV PARSER
# -----------------------------
def parse_csv(content):
    """
    Parse uploaded CSV files and extract
    basic structural information.
    """

    # Read uploaded CSV bytes into a pandas DataFrame
    df = pd.read_csv(io.BytesIO(content))

    # Return a simple structured representation
    return {
        "source_type": "csv",

        # Extract column names
        "columns": list(df.columns),

        # Extract inferred pandas datatypes
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},

        # Total number of rows
        "row_count": len(df),

        # Small sample preview for quick inspection
        "sample": df.head(5).to_dict(orient="records")
    }


# -----------------------------
# DDL PARSER
# -----------------------------
def parse_ddl(content):
    """
    Parse SQL DDL scripts and extract
    CREATE TABLE statements.
    """

    # Parse SQL statements using sqlparse
    parsed = sqlparse.parse(content)

    tables = []

    # Iterate through parsed SQL statements
    for stmt in parsed:

        # Convert statement back to string
        stmt_str = str(stmt).strip()

        # Only keep CREATE TABLE statements
        if stmt_str.upper().startswith("CREATE TABLE"):
            tables.append(stmt_str)

    return {
        "source_type": "ddl",

        # Store extracted CREATE TABLE definitions
        "tables": tables
    }


# -----------------------------
# HEALTH CHECK ROUTE
# -----------------------------
@app.get("/health")
def health():
    """
    Simple endpoint to confirm
    backend server is running.
    """

    return {"status": "ok"}


# -----------------------------
# FILE UPLOAD ROUTE
# -----------------------------
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Main ingestion endpoint.

    Accepts:
    - CSV files
    - SQL / DDL files

    Routes files to the correct parser.
    """

    # Normalize filename for safer extension checks
    filename = file.filename.lower()

    # Read uploaded file content
    content = await file.read()

    # Route CSV files to CSV parser
    if filename.endswith(".csv"):
        result = parse_csv(content)

    # Route SQL / DDL files to DDL parser
    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        result = parse_ddl(content.decode("utf-8"))

    # Reject unsupported file types
    else:
        return {"error": "Unsupported file type"}

    # Return parsed structured output
    return result