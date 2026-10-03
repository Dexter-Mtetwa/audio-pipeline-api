from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from models import SessionLocal, Job
from celery_app import celery_app
from dotenv import load_dotenv
import shutil
import os
import json
import uuid

load_dotenv()
API_KEY = os.environ["API_KEY"]

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# This function verifies the API key provided in the request header. If the key does not match the expected value, it raises an HTTP 401 Unauthorized exception.
def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# Endpoint to upload an audio file for transcription. It saves the file, creates a job in the database, and sends a task to Celery for processing.
@app.post("/transcribe")
@limiter.limit("5/minute") # Limit to 5 requests per minute per IP address
def transcribe(request: Request, file: UploadFile = File(...), _: None = Depends(verify_api_key)):
    db = SessionLocal()
    job_id = str(uuid.uuid4())
    saved_path = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job = Job(id=job_id, status="pending", original_filename=file.filename)
    db.add(job)
    db.commit()
    db.close()

    # Send the job to Celery for processing
    celery_app.send_task("tasks.process_audio", args=[job_id, saved_path])
    return {"job_id": job_id}


# Endpoint to retrieve the status and result of a transcription job. It checks the database for the job and returns its details, including the transcription result if available.
@app.get("/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(verify_api_key)):
    db = SessionLocal()
    job = db.query(Job).filter(Job.id == job_id).first()
    db.close()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job.id,
        "status": job.status,
        "original_filename": job.original_filename,
        "duration_seconds": job.duration_seconds,
        "result": json.loads(job.result) if job.result else None,
        "error": job.error,
    }