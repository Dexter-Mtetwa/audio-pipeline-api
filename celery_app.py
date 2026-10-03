from celery import Celery
from celery.signals import after_setup_logger
from dotenv import load_dotenv
import os
import logging

from logging.handlers import RotatingFileHandler

load_dotenv()

celery_app = Celery(
    "audio_pipeline",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
)

@after_setup_logger.connect
def setup_file_logging(logger, **kwargs):
    file_handler = RotatingFileHandler("pipeline.log", maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)