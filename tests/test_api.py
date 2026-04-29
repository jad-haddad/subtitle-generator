"""Integration tests for the subtitle generator API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from subtitle_generator.main import create_app


@pytest.fixture
def client() -> TestClient:  # ty: ignore[invalid-return-type]
    """Create a test client with mocked services."""
    mock_queue = MagicMock()
    mock_queue.list_jobs = AsyncMock(return_value=[])
    mock_queue.get = AsyncMock(return_value=None)
    mock_queue.submit = AsyncMock(
        return_value=MagicMock(
            job_id="test-uuid",
            status="pending",
            output_path=None,
            created_at="2026-04-28T10:00:00Z",
        )
    )

    with (
        patch("subtitle_generator.main.lifespan") as mock_lifespan,
        patch("subtitle_generator.dependencies._job_queue", mock_queue),
    ):
        from collections.abc import AsyncGenerator

        async def _noop_lifespan(app) -> AsyncGenerator[None]:
            yield

        mock_lifespan.side_effect = _noop_lifespan

        app = create_app()
        tc = TestClient(app)
        yield tc
        tc.close()


def test_health_check(client: TestClient) -> None:
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_list_jobs_empty(client: TestClient) -> None:
    """Test listing jobs when none exist."""
    response = client.get("/jobs")
    assert response.status_code == 200
    data = response.json()
    assert data["jobs"] == []
    assert data["total"] == 0


def test_get_nonexistent_job(client: TestClient) -> None:
    """Test getting a job that does not exist."""
    response = client.get("/jobs/nonexistent-uuid")
    assert response.status_code == 404


def test_download_nonexistent_job(client: TestClient) -> None:
    """Test downloading SRT for a nonexistent job."""
    response = client.get("/jobs/nonexistent-uuid/srt")
    assert response.status_code == 404


def test_submit_from_path_file_not_found(client: TestClient) -> None:
    """Test submitting a path that does not exist inside the container."""
    response = client.post(
        "/jobs/from-path",
        json={
            "path": "/nonexistent/path/movie.mkv",
            "language": "en",
        },
    )
    assert response.status_code == 404


def test_submit_from_path_unsupported_format(client: TestClient) -> None:
    """Test submitting a path with unsupported extension."""
    # Create a temporary file with unsupported extension
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        response = client.post(
            "/jobs/from-path",
            json={
                "path": str(tmp_path),
                "language": "en",
            },
        )
        assert response.status_code == 415
    finally:
        tmp_path.unlink(missing_ok=True)
