from celery_app import celery_app
from pipeline import run_pipeline
from models import SessionLocal, Job
import json
import traceback

@celery_app.task
def process_audio(job_id: str, audio_path: str):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "processing"
        db.commit()

        output, duration = run_pipeline(audio_path)

        job.status = "done"
        job.duration_seconds = duration
        job.result = json.dumps(output)
        db.commit()

    except Exception as e:
        job.status = "failed"
        job.error = str(e) + "\n" + traceback.format_exc()
        db.commit()

    finally:
        db.close()