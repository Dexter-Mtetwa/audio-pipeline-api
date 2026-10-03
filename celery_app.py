from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv()

celery_app = Celery(
    "audio_pipeline",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
)