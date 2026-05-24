#Command to run : uvicorn ingestion_parsing.main:app --reload

from typing import List

from fastapi import FastAPI, UploadFile, File
#import the two parsing modules for CSV metadata and SQL schemas
from ingestion_parsing.csv_parser import parse_csv
from ingestion_parsing.ddl_parser import parse_ddl

# Pipeline orchestration — parse + profile + schema match
from pipeline.runner import run_csv_pipeline, run_ddl_pipeline
from pipeline.match_runner import run_match_pipeline
from pipeline.logger import get_logger
from pipeline.responses import api_error

app = FastAPI(title="DM-Agent API", version="0.3.1")

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
    filename = file.filename.lower() if file.filename else ""

    #check if filename is valid (not empty or just whitespace)
    if not filename or filename.strip() == "":
        return api_error("No filename detected")

    # reject files that exceed the maximum size limit before reading them into memory
    if file.size and file.size > MAX_FILE_SIZE:
        return api_error("File too large. Maximum allowed size is 10MB.")

    content = await file.read() # Read uploaded file content into memory

    # Double-check after read in case Content-Length header was missing (causes file.size to give None)
    if len(content) > MAX_FILE_SIZE:
        return api_error("File too large. Maximum allowed size is 10MB.")

    logger.info(f"Upload request: '{file.filename}'  size={len(content)} bytes")

    # Route to the appropriate parser based on file extension
    if filename.endswith(".csv"):
        result = parse_csv(content)
    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        result = parse_ddl(content.decode("utf-8"))
    else:
        return api_error(
            "Unsupported file type. Please upload a .csv, .sql, or .ddl file."
        )

    if "error" in result:
        return api_error(result["error"], stage="parse")

    # Omit full row payload from parse-only API responses
    if "rows" in result:
        result = {k: v for k, v in result.items() if k != "rows"}
    return result


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
    filename = file.filename.lower() if file.filename else ""

    if not filename or filename.strip() == "":
        logger.warning("Analyze request received with no filename")
        return api_error("No filename detected")

    if file.size and file.size > MAX_FILE_SIZE:
        logger.warning(f"Analyze rejected — file too large: {file.size} bytes")
        return api_error("File too large. Maximum allowed size is 10MB.")

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        logger.warning(f"Analyze rejected after read — file too large: {len(content)} bytes")
        return api_error("File too large. Maximum allowed size is 10MB.")

    logger.info(f"Analyze request: '{file.filename}'  size={len(content)} bytes")

    if filename.endswith(".csv"):
        return run_csv_pipeline(content, filename=file.filename)

    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        return run_ddl_pipeline(content.decode("utf-8"), filename=file.filename)

    else:
        return api_error(
            "Unsupported file type. Please upload a .csv, .sql, or .ddl file."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /match  — parse + profile + schema matching across multiple files (3.3 + 3.4)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/match")
async def match_files(files: List[UploadFile] = File(...)):
    """
    Upload two or more files (.csv, .sql, .ddl). Each file is parsed and profiled,
    then schema matching finds overlapping tables/columns and merge suggestions.
    """
    if len(files) < 2:
        return api_error("At least two files are required for schema matching")

    payloads: List[tuple] = []

    for upload in files:
        filename = (upload.filename or "").lower()
        if not filename.strip():
            return api_error("One or more uploads have no filename")

        if upload.size and upload.size > MAX_FILE_SIZE:
            return api_error(f"File too large: {upload.filename}")

        content = await upload.read()
        if len(content) > MAX_FILE_SIZE:
            return api_error(f"File too large: {upload.filename}")

        if not (
            filename.endswith(".csv")
            or filename.endswith(".sql")
            or filename.endswith(".ddl")
        ):
            return api_error(
                f"Unsupported file type: {upload.filename}. Use .csv, .sql, or .ddl"
            )

        if filename.endswith(".csv"):
            payloads.append((content, upload.filename))
        else:
            payloads.append((content.decode("utf-8"), upload.filename))

    logger.info(f"Match request: {len(payloads)} file(s)")
    return run_match_pipeline(payloads)
