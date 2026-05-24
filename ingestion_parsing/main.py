#Command to run : uvicorn ingestion_parsing.main:app --reload

from fastapi import FastAPI, UploadFile, File
#import the two parsing modules for CSV metadata and SQL schemas
from ingestion_parsing.csv_parser import parse_csv
from ingestion_parsing.ddl_parser import parse_ddl

# Pipeline orchestration — parse + profile in one call
from pipeline.runner import run_csv_pipeline, run_ddl_pipeline
from pipeline.logger import get_logger

app = FastAPI(title="DM-Agent API", version="0.2.0")

# Module-level logger — appears as 'ingestion_parsing.main' in log output
logger = get_logger(__name__)

# define a maximum file size limit to prevent backend crash from large uploads
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

#Simple endpoint to confirm backend server is running.
@app.get("/health")
def health():
    logger.debug("Health check requested")
    return {"status": "ok"}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename.lower() # Normalize filename for safer extension checks

    #check if filename is valid (not empty or just whitespace)
    if not filename or filename.strip() == "":
        return {"error": "No filename detected"}

    # reject files that exceed the maximum size limit before reading them into memory
    if file.size and file.size > MAX_FILE_SIZE:
        return {"error": "File too large. Maximum allowed size is 10MB."}

    content = await file.read() # Read uploaded file content into memory

    # Double-check after read in case Content-Length header was missing (causes file.size to give None)
    if len(content) > MAX_FILE_SIZE:
        return {"error": "File too large. Maximum allowed size is 10MB."}

    logger.info(f"Upload request: '{file.filename}'  size={len(content)} bytes")

    # Route to the appropriate parser based on file extension
    if filename.endswith(".csv"):
        return parse_csv(content)
    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        return parse_ddl(content.decode("utf-8"))
    else:
        return {"error": "Unsupported file type. Please upload a .csv, .sql, or .ddl file."}


# ─────────────────────────────────────────────────────────────────────────────
# /analyze  — parse + profile in one call
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze_file(file: UploadFile = File(...)):
    """
    Parse the uploaded file and immediately run column-level profiling.

    Returns structured JSON containing:
      - pipeline metadata (status, stages_completed, timing in logs)
      - parse_output  : raw parser output (columns, rows, dtypes)
      - profiling_output : full column profiles + table summary

    Supported file types: .csv  .sql  .ddl
    """
    filename = file.filename.lower()

    if not filename or filename.strip() == "":
        logger.warning("Analyze request received with no filename")
        return {"error": "No filename detected"}

    if file.size and file.size > MAX_FILE_SIZE:
        logger.warning(f"Analyze rejected — file too large: {file.size} bytes")
        return {"error": "File too large. Maximum allowed size is 10MB."}

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        logger.warning(f"Analyze rejected after read — file too large: {len(content)} bytes")
        return {"error": "File too large. Maximum allowed size is 10MB."}

    logger.info(f"Analyze request: '{file.filename}'  size={len(content)} bytes")

    if filename.endswith(".csv"):
        return run_csv_pipeline(content, filename=file.filename)

    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        return run_ddl_pipeline(content.decode("utf-8"), filename=file.filename)

    else:
        return {"error": "Unsupported file type. Please upload a .csv, .sql, or .ddl file."}