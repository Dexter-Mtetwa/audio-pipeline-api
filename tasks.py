from celery_app import celery_app
from pipeline import run_pipeline
from models import SessionLocal, Job
import json
import traceback
import os
import logging

logger = logging.getLogger(__name__)

@celery_app.task
def process_audio(job_id: str, audio_path: str):
    db = SessionLocal()
    try:
        logger.info(f"Starting job {job_id} for file {audio_path}")
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "processing"
        db.commit()

        output, duration = run_pipeline(audio_path)

        job.status = "done"
        job.duration_seconds = duration
        job.result = json.dumps(output)
        db.commit()
        logger.info(f"Job {job_id} completed successfully, duration={duration:.1f}s")

    except Exception as e:
        job.status = "failed"
        job.error = str(e) + "\n" + traceback.format_exc()
        db.commit()
        logger.error(f"Job {job_id} failed: {e}")

    finally:
        db.close()
        if os.path.exists(audio_path):
            os.remove(audio_path)