FROM python:3.12-slim-bookworm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies: FFmpeg, curl (for healthcheck), and libsndfile1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Change the working directory to the `app` directory
WORKDIR /app

# Use the system Python and compile bytecode for faster startup
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1

# Enable caching
ENV UV_CACHE_DIR=/root/.cache/uv

# Mount the lockfile and pyproject.toml as a cache optimization:
# install dependencies in their own layer before copying source code.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Copy the full project into the image
COPY . /app

# Sync the project itself (brings in the editable install of subtitle-generator)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Ensure the virtual environment binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "subtitle_generator.main:app", "--host", "0.0.0.0", "--port", "8000"]
