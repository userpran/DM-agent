#Command to run : uvicorn ingestion_parsing.main:app --reload

from fastapi import FastAPI, UploadFile, File
from ingestion_parsing.csv_parser import parse_csv
from ingestion_parsing.ddl_parser import parse_ddl  

app = FastAPI()

# define a maximum file size limit to prevent backend crash from large uploads
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename.lower()
    content = await file.read()

    if not filename or filename.strip() == "":
        return {"error": "No filename detected"}

    if len(content) > MAX_FILE_SIZE:
        return {"error": "File too large. Maximum allowed size is 10MB."}

    if filename.endswith(".csv"):
        return parse_csv(content)
    elif filename.endswith(".sql") or filename.endswith(".ddl"):
        return parse_ddl(content.decode("utf-8"))
    else:
        return {"error": "Unsupported file type. Please upload a .csv, .sql, or .ddl file."}