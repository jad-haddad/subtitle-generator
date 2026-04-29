"""CLI client for the subtitle generator API.

Usage:
    uv run subgen /media/movies/Movie.mkv [-l <lang>] [-u <url>]
    uv run subgen -h | --help

Options:
    -l, --language=<lang>   Language hint (default: auto-detect)
    -u, --url=<url>         API base URL (default: http://localhost:8000)
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

DEFAULT_API_URL = "http://localhost:8000"
POLL_INTERVAL_S = 5
REQUEST_TIMEOUT = 300


def _submit_from_path(
    client: httpx.Client,
    media_path: str,
    language: str | None,
    api_url: str,
) -> dict:
    """Submit a media file path and return the job info."""
    url = f"{api_url}/jobs/from-path"
    payload: dict[str, str | int | None] = {"path": media_path}
    if language:
        payload["language"] = language

    response = client.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _poll_status(
    client: httpx.Client,
    job_id: str,
    api_url: str,
) -> dict:
    """Poll job status until completed or failed."""
    url = f"{api_url}/jobs/{job_id}"
    while True:
        try:
            response = client.get(url, timeout=30)
            response.raise_for_status()
        except httpx.ReadTimeout:
            time.sleep(POLL_INTERVAL_S)
            continue

        status = response.json()

        if status["status"] == "completed":
            return status
        if status["status"] == "failed":
            print(
                f"\nJob failed: {status.get('error', 'Unknown error')}",
                file=sys.stderr,
            )
            sys.exit(1)

        progress = status.get("progress_pct", 0)
        stage = status.get("stage", "working")
        print(
            f"\r  [{progress:>3}%] {stage}",
            end="",
            flush=True,
        )
        time.sleep(POLL_INTERVAL_S)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="subgen",
        description="Generate subtitles from a media file via the subtitle-generator API.",
    )
    parser.add_argument(
        "path",
        help="Absolute path to the media file (as visible inside the container)",
    )
    parser.add_argument(
        "-l",
        "--language",
        type=str,
        default=None,
        help="Language hint (ISO 639-1, e.g. 'en', 'zh'). Omit for auto-detect.",
    )
    parser.add_argument(
        "-u",
        "--url",
        default=DEFAULT_API_URL,
        help=f"API base URL [default: {DEFAULT_API_URL}]",
    )
    args = parser.parse_args(argv)

    media_path: str = args.path
    language: str | None = args.language

    with httpx.Client() as client:
        print(f"Submitting: {media_path}")
        print(f"API: {args.url}")
        if language:
            print(f"Language: {language}")
        else:
            print("Language: auto-detect")

        try:
            job_info = _submit_from_path(
                client,
                media_path,
                language,
                args.url,
            )
        except httpx.HTTPStatusError as exc:
            print(
                f"Submission failed: {exc.response.status_code} "
                f"- {exc.response.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        job_id = job_info["job_id"]
        output_path = job_info.get("output_path")
        print(f"Job submitted: {job_id}")
        if output_path:
            print(f"Expected output: {output_path}")
        print("Waiting for processing...")

        status = _poll_status(client, job_id, args.url)
        print("\r  [100%] completed")

        final_path = status.get("output_path")
        detected = status.get("language")
        if detected:
            print(f"Detected language: {detected}")
        if final_path:
            print(f"Subtitle written: {final_path}")


if __name__ == "__main__":
    main()
