import shutil
import os
import json
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException

from models import SessionLocal, Job
from celery_app import celery_app


app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# This endpoint handles the uploading of audio files for transcription. It saves the uploaded file to a designated directory, creates a new job entry in the database with a unique job ID, and then triggers the asynchronous processing of the audio file using Celery.
@app.post("/transcribe")
def transcribe(file: UploadFile = File(...)):
    db = SessionLocal()

    job_id = str(uuid.uuid4())
    saved_path = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)  # Save the uploaded file to the designated path

    job = Job(id=job_id, status="pending", original_filename=file.filename)
    db.add(job)
    db.commit()
    db.close()

    # Send a task to the Celery worker to process the uploaded audio file asynchronously. The task is identified by its name "tasks.process_audio" and is provided with the job ID and the path to the saved audio file as arguments.
    celery_app.send_task("tasks.process_audio", args=[job_id, saved_path])

    return {"job_id": job_id}


# This endpoint retrieves the status and results of a previously submitted transcription job. It queries the database for the job using the provided job ID, and if found, returns the job's status, original filename, duration of the audio processing, transcription results (if available), and any error messages. If the job is not found, it raises a 404 HTTP exception.
@app.get("/jobs/{job_id}")
def get_job(job_id: str):
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