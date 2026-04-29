"""FastAPI application factory and entry point."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from subtitle_generator.routers import jobs
from subtitle_generator.utils.logger import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    from subtitle_generator.dependencies import initialize_services, shutdown_services

    await initialize_services()
    yield
    await shutdown_services()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    configure_logging()
    app = FastAPI(
        title="Subtitle Generator",
        description="Production-ready subtitle generation using Groq Whisper API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(jobs.router)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    return app


app = create_app()


def main() -> None:
    """CLI entry point for uvicorn."""
    import uvicorn

    from subtitle_generator.config import settings

    uvicorn.run(
        "subtitle_generator.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        access_log=True,
    )


if __name__ == "__main__":
    main()
