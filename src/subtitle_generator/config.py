"""Application configuration using Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="SG_", env_file=".env")

    # Groq API settings
    groq_api_key: str = ""
    groq_model: str = "whisper-large-v3-turbo"
    groq_concurrency: int = 5
    # Groq does not document a hard audio upload limit. Based on
    # empirical testing the CDN rejects files above roughly 12-15 MB.
    # We split at 10 MB to stay well under that threshold.
    groq_safe_chunk_size_mb: int = 10
    groq_base_url: str = "https://api.groq.com"

    # Audio processing
    # Bit-rate for the extracted / normalised MP3 sent to Groq.
    # Lower bit-rate = smaller files. 24 kbps mono is still more
    # than enough for speech recognition while keeping uploads small.
    mp3_bitrate: str = "24k"
    target_sample_rate: int = 16000
    target_channels: int = 1
    max_file_size_mb: int = 500
    chunk_duration_s: int = 600

    # Subtitle formatting
    max_chars_per_line: int = 42
    max_lines_per_subtitle: int = 2
    min_subtitle_duration_s: float = 1.0
    max_subtitle_duration_s: float = 6.0

    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Job queue
    job_result_ttl_seconds: int = 3600
    max_concurrent_jobs: int = 1


settings = Config()
