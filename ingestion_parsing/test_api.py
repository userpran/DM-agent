from fastapi import FastAPI, UploadFile, File
import pandas as pd
import sqlparse
import io
import math

app = FastAPI()

def parse_csv(content: bytes) -> dict:
    df = pd.read_csv(io.BytesIO(content))
    return {
        "source_type": "csv",
        "columns": list(df.columns),
        "dtypes": {col: str(df[col].dtype) for col in df.columns},
        "row_count": len(df),
        "sample": df.head(3).to_dict(orient="records")
    }

def parse_ddl(content: str) -> dict:
    parsed = sqlparse.parse(content)
    tables = []
    for statement in parsed:
        if statement.get_type() == "CREATE":
            tables.append(str(statement))
    return {
        "source_type": "ddl",
        "raw_statements": tables
    }

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename.lower()
    content = await file.read()

    if filename.endswith(".csv"):
        result = parse_csv(content)
    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        result = parse_ddl(content.decode("utf-8"))
    else:
        return {"error": "Unsupported file type"}

    return result
