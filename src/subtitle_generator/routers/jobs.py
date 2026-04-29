"""FastAPI routers for job management."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from subtitle_generator.dependencies import get_job_queue
from subtitle_generator.models import (
    JobFromPathRequest,
    JobListResponse,
    JobStatusResponse,
    JobSubmitResponse,
    TranscriptionOptions,
)
from subtitle_generator.queue import JobQueue
from subtitle_generator.services.audio import AudioFormatError, AudioProcessor
from subtitle_generator.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/from-path",
    response_model=JobSubmitResponse,
    status_code=202,
)
async def create_job_from_path(
    body: JobFromPathRequest,
    queue: JobQueue = Depends(get_job_queue),
) -> JobSubmitResponse:
    """Submit a subtitle generation job from a media file path.

    The service will:
    1. Check that the path exists and is a supported format.
    2. If language is provided, check if the SRT already exists (409 if so).
    3. Normalize audio, send to Groq Whisper, and write the .srt next to the media file.

    Returns 202 Accepted with job_id to poll for status.
    """
    media_path = Path(body.path)
    logger.info("job_request_from_path", path=str(media_path), language=body.language)

    # Validate that the file exists inside the container
    if not media_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Media file not found: {media_path}",
        )

    # Validate extension
    try:
        AudioProcessor.validate_extension(media_path.name)
    except AudioFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    # Determine output path (None if auto-detect; we'll compute it after transcription)
    output_path: Path | None = None
    if body.language:
        output_path = JobQueue._srt_path(media_path, body.language)
        if output_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Subtitle already exists: {output_path.name}",
            )

    options = TranscriptionOptions(
        language=body.language,
        max_chars_per_line=body.max_chars_per_line,
    )
    job = await queue.submit(media_path, output_path, options)

    return JobSubmitResponse(
        job_id=job.job_id,
        status=job.status,
        output_path=str(output_path) if output_path else None,
        created_at=job.created_at,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    queue: JobQueue = Depends(get_job_queue),
) -> JobListResponse:
    """List all active (non-expired) jobs."""
    jobs = await queue.list_jobs()
    return JobListResponse(
        jobs=[j.to_list_item() for j in jobs],
        total=len(jobs),
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    queue: JobQueue = Depends(get_job_queue),
) -> JobStatusResponse:
    """Get status and progress of a specific job."""
    job = await queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress_pct=job.progress_pct,
        stage=job.stage,
        media_path=str(job.media_path),
        output_path=str(job.output_path) if job.output_path else None,
        language=job.language,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/{job_id}/srt")
async def download_srt(
    job_id: str,
    queue: JobQueue = Depends(get_job_queue),
) -> dict[str, str]:
    """Return the resolved SRT path for a completed job.

    Since the SRT is written directly next to the media file, this endpoint
    is a convenience for the client to confirm the output location.
    """
    job = await queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status.value not in ("completed", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Job is not finished (status: {job.status.value})",
        )

    if job.output_path and job.output_path.exists():
        return {"srt_path": str(job.output_path)}

    raise HTTPException(status_code=404, detail="SRT file not found")
