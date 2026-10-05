# audio-pipeline-api

A speaker-diarization + transcription service. Upload an audio file, get back a speaker-labeled transcript. Built as a production-shaped async pipeline: FastAPI accepts uploads and queues work, a Celery worker runs the actual ML pipeline, Redis brokers the queue, and Postgres persists job status and results.

## Architecture

```
Client → FastAPI (/transcribe, /jobs/{id})
             │
             ├──→ Postgres (job row: status, result, metadata)
             │
             └──→ Redis (task queue)
                       │
                       └──→ Celery worker → pyannote (diarization) + faster-whisper (transcription)
                                 │
                                 └──→ writes result back to Postgres
```
## Architecture

```mermaid
flowchart TB
    Client[Client] --> API[FastAPI app]
    API -->|job row| DB[(Postgres)]
    API -->|queue task| Redis[(Redis)]
    Redis -->|pulls task| Worker[Celery worker]
    Worker -->|writes result| DB
    Worker --> Pipeline[pyannote + faster-whisper]
```

The API and the ML pipeline run in separate processes with separate dependencies — the FastAPI process never imports torch/pyannote/faster-whisper directly; it only ever queues work by task name via `celery_app.send_task(...)`. This keeps the lightweight API environment free of multi-GB ML dependencies.

The API and the ML pipeline run in separate processes with separate dependencies — the FastAPI process never imports torch/pyannote/faster-whisper directly; it only ever queues work by task name via `celery_app.send_task(...)`. This keeps the lightweight API environment free of multi-GB ML dependencies.

## Requirements

- Docker (for Redis and Postgres)
- Conda (or another way to manage two separate Python environments)
- Python 3.11

## Setup

### 1. Infrastructure (Redis + Postgres)

```bash
docker run -d --name audio-redis -p 6379:6379 redis
docker run -d --name audio-postgres -p 5433:5432 \
  -e POSTGRES_PASSWORD=devpassword \
  -e POSTGRES_DB=audio_pipeline \
  postgres
```

Note Postgres is mapped to host port `5433`, not the default `5432`, to avoid colliding with any other local Postgres instance.

### 2. Python environments

This project uses two separate environments, since the API layer and the ML pipeline have very different dependency weights.

**`audio` env** — runs the Celery worker (needs torch, pyannote, faster-whisper):
```bash
conda create -n audio python=3.11 -y
conda activate audio
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install pyannote.audio faster-whisper celery redis sqlalchemy psycopg2-binary python-dotenv
```

**`audio-pipeline-api` env** — runs the FastAPI server (lightweight, no ML libs):
```bash
conda create -n audio-pipeline-api python=3.11 -y
conda activate audio-pipeline-api
pip install fastapi uvicorn python-multipart celery redis sqlalchemy psycopg2-binary python-dotenv slowapi
```

### 3. Hugging Face access

Diarization uses gated pyannote models. You'll need a Hugging Face account and to accept the terms on:
- `huggingface.co/pyannote/speaker-diarization-3.1`
- `huggingface.co/pyannote/segmentation-3.0`

Then authenticate locally:
```bash
hf auth login
```

### 4. Environment variables

Copy `.env.example` to `.env` and fill in your own values:
```bash
cp .env.example .env
```

```
DATABASE_URL=postgresql+psycopg2://postgres:devpassword@localhost:5433/audio_pipeline
REDIS_URL=redis://localhost:6379/0
API_KEY=your-own-secret-key-here
```

### 5. Create the database table

Run once (either environment works, since this only needs sqlalchemy/psycopg2):
```bash
python models.py
```

## Running

Three things need to be running at once, in separate terminals.

**Terminal 1 — Celery worker:**
```bash
conda activate audio
celery -A tasks worker --loglevel=info --concurrency=2
```

**Terminal 2 — FastAPI server:**
```bash
conda activate audio-pipeline-api
uvicorn main:app --reload
```

(Redis and Postgres should already be running as Docker containers from setup.)

## API

All endpoints require an `X-API-Key` header matching the `API_KEY` set in `.env`.

### `POST /transcribe`

Upload an audio file. Returns immediately with a job ID; processing happens in the background.

```bash
curl -X POST "http://127.0.0.1:8000/transcribe" \
  -F "file=@audio.mp3" \
  -H "X-API-Key: your-key"
```

Response:
```json
{"job_id": "uuid-here"}
```

Rate limited to 5 requests per minute per IP.

### `GET /jobs/{job_id}`

Poll for job status and result.

```bash
curl "http://127.0.0.1:8000/jobs/<job_id>" -H "X-API-Key: your-key"
```

Response:
```json
{
  "job_id": "...",
  "status": "pending | processing | done | failed",
  "original_filename": "audio.mp3",
  "duration_seconds": 21.1,
  "result": [
    {"start": 0.03, "end": 2.6, "speaker": "SPEAKER_00", "text": "..."},
    ...
  ],
  "error": null
}
```

## Request lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant A as FastAPI
    participant D as Postgres
    participant R as Redis
    participant W as Celery worker

    C->>A: POST /transcribe (audio file)
    A->>D: create job (status=pending)
    A->>R: queue task
    A-->>C: {job_id}
    R->>W: deliver task
    W->>D: update status=processing
    W->>W: diarize + transcribe
    W->>D: write result, status=done
    C->>A: GET /jobs/{id}
    A->>D: read job row
    A-->>C: status + result
```

## How the pipeline works

1. Audio is diarized first (pyannote) to find speaker turns.
2. Each turn is sliced out of the audio, padded with ~0.75s of surrounding context (improves transcription accuracy on short/isolated clips), and resampled to 16kHz.
3. Each padded slice is transcribed independently (faster-whisper, `small` model), using word-level timestamps to discard any text that bled in from the padding.
4. Results are merged into a single speaker-labeled transcript, ordered by turn.

```mermaid
flowchart LR
    A[Diarize full audio] --> B[Get speaker turns]
    B --> C[Pad each turn ±0.75s]
    C --> D[Resample to 16kHz]
    D --> E[Transcribe each slice]
    E --> F[Trim padding via word timestamps]
    F --> G[Merge into labeled transcript]
```

This diarize-first approach (rather than transcribing the whole file once and merging speaker labels after) was chosen specifically because it correctly separates overlapping speech — a single Whisper pass on a full file collapses overlapping turns from different speakers into one block of text.

## Known limitations

- Diarization can misattribute speaker identity when voices are very similar (e.g. identical twins).
- Short utterances (under ~1s) are harder to transcribe accurately even with context padding.
- Uploaded files and job rows are not automatically pruned after some retention period — `uploads/` only clears per-job, and old Postgres rows persist indefinitely.
- No horizontal scaling tested beyond `--concurrency=2`; this is the verified-stable ceiling on a 6GB GPU / limited-RAM dev machine, not a theoretical maximum.

## Stack

FastAPI · Celery · Redis · PostgreSQL · pyannote.audio (speaker-diarization-3.1) · faster-whisper (small)
