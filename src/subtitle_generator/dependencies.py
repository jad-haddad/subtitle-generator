"""Shared dependencies and singletons."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException

from subtitle_generator.queue import JobQueue
from subtitle_generator.services.groq_asr import GroqASRService
from subtitle_generator.utils.logger import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger(__name__)

# Global singletons (set during lifespan; accessed lazily through getters)
_asr_service: GroqASRService | None = None
_job_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    """Return the initialized job queue."""
    if _job_queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return _job_queue


async def initialize_services() -> None:
    """Initialize model services on startup."""
    global _asr_service, _job_queue

    configure_logging()
    logger.info("initializing_services")

    logger.info("loading_groq_asr")
    _asr_service = GroqASRService()
    _asr_service.load()

    logger.info("initializing_job_queue")
    _job_queue = JobQueue(_asr_service)
    await _job_queue.start()

    logger.info("services_initialized")


async def shutdown_services() -> None:
    """Clean up services on shutdown."""
    logger.info("shutting_down_services")
    if _job_queue is not None:
        await _job_queue.stop()
    logger.info("services_shutdown_complete")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager."""
    await initialize_services()
    yield
    await shutdown_services()
