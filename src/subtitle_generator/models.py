"""Pydantic models and schemas for the API."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Status of a subtitle generation job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class TranscriptionOptions(BaseModel):
    """Options for transcription."""

    language: str | None = Field(default=None, description="Language hint or None for auto-detect")
    max_chars_per_line: int = Field(default=42, ge=20, le=80)


class JobFromPathRequest(BaseModel):
    """Request body for starting a subtitle job from a file path."""

    path: str = Field(..., description="Absolute path to the media file inside the container")
    language: str | None = Field(default=None, description="Language hint or None for auto-detect")
    max_chars_per_line: int = Field(default=42, ge=20, le=80)


class JobSubmitResponse(BaseModel):
    """Response when submitting a new job."""

    job_id: str
    status: JobStatus
    output_path: str | None = None
    created_at: datetime


class JobStatusResponse(BaseModel):
    """Response for job status queries."""

    job_id: str
    status: JobStatus
    progress_pct: int = Field(ge=0, le=100)
    stage: str
    media_path: str
    output_path: str | None = None
    language: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobListItem(BaseModel):
    """Item in the job list."""

    job_id: str
    status: JobStatus
    progress_pct: int
    stage: str
    filename: str
    language: str | None = None
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobListItem]
    total: int
