# Subtitle Generator

Production-ready subtitle generation service using the **Groq Whisper API**.

Transforms audio/video files (wav, mp3, m4a, mp4, mkv, etc.) into properly-formatted `.srt` subtitle files via an async HTTP API. No local GPU/CPU ML — all inference is offloaded to Groq's Whisper endpoint with word-level timestamp support.

---

## Quick Start

### With Docker Compose (Recommended)

```bash
# Set your Groq API key
export SG_GROQ_API_KEY=gsk_...

# Start the service
docker compose up --build -d

# The API is available at http://localhost:8000
```

```bash
# Submit a file using the CLI client
uv run subgen /path/to/The.Lion.King.mp3 -l English
```

### Local Development

```bash
# Requires Python 3.12, uv, and FFmpeg
uv sync

# Set your Groq API key
export SG_GROQ_API_KEY=gsk_...

# Run server
uv run uvicorn subtitle_generator.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## CLI Client

A convenience CLI is included to submit, poll, and download subtitles in one command:

```bash
# Run directly with uv (no separate activation needed)
uv run subgen /path/to/The.Lion.King.mp3 -l English
# → Saves as The.Lion.King.srt in the same directory

# Custom output path
uv run subgen interview.mp3 -o ./subtitles/interview.srt

# Point to a different API server
uv run subgen podcast.mp3 -u http://api.example.com:8000

# Auto-detect language (omit -l)
uv run subgen speech.wav

# See all available languages
uv run subgen --help
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jobs` | Submit a new audio/video file for subtitle generation |
| `GET`  | `/jobs` | List all active (non-expired) jobs |
| `GET`  | `/jobs/{job_id}` | Get status and progress of a job |
| `GET`  | `/jobs/{job_id}/srt` | Download the generated `.srt` file |
| `GET`  | `/health` | Health check |

---

## Example Requests

### Submit a file

```bash
curl -X POST http://localhost:8000/jobs \
  -F "file=@/path/to/The.Lion.King.mp3" \
  -F "language=English" \
  -F "max_chars_per_line=42"
```

Response (202 Accepted):
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "pending",
  "created_at": "2026-04-28T10:00:00Z"
}
```

### Poll for status

```bash
curl http://localhost:8000/jobs/a1b2c3d4-...
```

Response:
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "completed",
  "progress_pct": 100,
  "filename": "The.Lion.King.mp3",
  "language": "English",
  "error": null,
  "download_url": "/jobs/a1b2c3d4-.../srt"
}
```

### Download SRT

```bash
curl -O -J http://localhost:8000/jobs/a1b2c3d4-.../srt
# Saved as: The.Lion.King.srt
```

### List active jobs

```bash
curl http://localhost:8000/jobs
```

---

## Pipeline Architecture

```
audio input
  → FFmpeg normalization (16kHz mono MP3, 32kbps)
  → Split if >25MB (Groq file size limit)
  → Groq Whisper API transcription (word-level timestamps)
  → Timestamp merging across chunks
  → SRT segmentation & formatting
  → .srt file output
```

---

## Configuration

All settings are controlled via environment variables prefixed with `SG_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `SG_GROQ_API_KEY` | *(required)* | Groq API key |
| `SG_GROQ_MODEL` | `whisper-large-v3-turbo` | Groq Whisper model ID |
| `SG_GROQ_CONCURRENCY` | `5` | Max concurrent Groq API calls |
| `SG_MAX_FILE_SIZE_MB` | `500` | Maximum uploaded file size |
| `SG_CHUNK_DURATION_S` | `600` | Max chunk duration for splitting |
| `SG_JOB_RESULT_TTL_SECONDS` | `3600` | How long to keep completed job results |

---

## Project Structure

```
subtitle-generator/
├── src/subtitle_generator/
│   ├── main.py              # FastAPI app
│   ├── config.py            # Settings
│   ├── models.py            # Pydantic schemas
│   ├── dependencies.py      # Lifespan + singletons
│   ├── queue.py             # In-memory job queue + worker
│   ├── routers/
│   │   └── jobs.py          # HTTP endpoints
│   └── services/
│       ├── audio.py         # FFmpeg normalization + chunking
│       ├── groq_asr.py      # Groq Whisper API client
│       └── subtitle.py      # SRT formatter
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Tooling

| Tool | Purpose |
|------|---------|
| **uv** | Package management |
| **ruff** | Linting + formatting |
| **ty** | Type checking (Astral, Rust-based) |

```bash
# Lint
ruff check src

# Format
ruff format src

# Type check
ty check
```

---

## Notes

- **No local ML**: All transcription is done via the Groq API. The service requires only FFmpeg and a Groq API key.
- **File size limit**: Groq accepts files up to 25MB. Longer audio is automatically split into chunks and timestamps are merged.
- **Word-level timestamps**: Groq's `verbose_json` response with `timestamp_granularities=["word"]` provides per-word timestamps natively — no forced alignment needed.
- **Queue**: Single sequential worker per process. For horizontal scaling, run multiple container instances behind a load balancer.
