from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

ProcessingMode = Literal["no-llm", "llm-only", "hybrid"]
EmailTransport = Literal["smtp", "gmail-api"]


@dataclass(frozen=True)
class Config:
    gmail_user: str
    gmail_app_password: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int

    digest_to: str
    digest_from_name: str
    email_transport: EmailTransport

    anthropic_api_key: str | None
    processing_mode: ProcessingMode

    # Stage 1: per-newsletter topic extraction
    anthropic_model_stage1: str
    # Stage 2: cross-newsletter deduplication (lightweight call on compact JSON)
    anthropic_model_stage2: str
    # Max concurrent LLM calls in Stage 1
    stage1_max_workers: int
    # Approximate pricing config (USD per 1M tokens)
    stage1_input_usd_per_mtok: float
    stage1_output_usd_per_mtok: float
    stage1_cache_write_usd_per_mtok: float
    stage1_cache_read_usd_per_mtok: float
    stage2_input_usd_per_mtok: float
    stage2_output_usd_per_mtok: float
    stage2_cache_write_usd_per_mtok: float
    stage2_cache_read_usd_per_mtok: float

    lookback_days: int
    data_dir: Path
    log_level: str
    dry_run: bool

    # Gmail API (HTTPS) sending mode; used when EMAIL_TRANSPORT=gmail-api
    gmail_api_client_id: str | None
    gmail_api_client_secret: str | None
    gmail_api_refresh_token: str | None


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def load_config() -> Config:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    data_dir = Path(os.environ.get("DATA_DIR", "./data")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Support legacy ANTHROPIC_MODEL as fallback for both stages
    legacy_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    processing_mode_raw = os.environ.get("PROCESSING_MODE", "llm-only").strip().lower()
    allowed_modes = {"no-llm", "llm-only", "hybrid"}
    if processing_mode_raw not in allowed_modes:
        raise RuntimeError(
            "Invalid PROCESSING_MODE. Expected one of: no-llm, llm-only, hybrid"
        )
    processing_mode: ProcessingMode = processing_mode_raw  # type: ignore[assignment]
    email_transport_raw = os.environ.get("EMAIL_TRANSPORT", "smtp").strip().lower()
    allowed_transports = {"smtp", "gmail-api"}
    if email_transport_raw not in allowed_transports:
        raise RuntimeError("Invalid EMAIL_TRANSPORT. Expected one of: smtp, gmail-api")
    email_transport: EmailTransport = email_transport_raw  # type: ignore[assignment]
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    if processing_mode != "no-llm" and not anthropic_api_key:
        raise RuntimeError("Missing required env var: ANTHROPIC_API_KEY")

    gmail_api_client_id = os.environ.get("GMAIL_API_CLIENT_ID", "").strip() or None
    gmail_api_client_secret = os.environ.get("GMAIL_API_CLIENT_SECRET", "").strip() or None
    gmail_api_refresh_token = os.environ.get("GMAIL_API_REFRESH_TOKEN", "").strip() or None
    if email_transport == "gmail-api":
        missing = [
            name
            for name, value in (
                ("GMAIL_API_CLIENT_ID", gmail_api_client_id),
                ("GMAIL_API_CLIENT_SECRET", gmail_api_client_secret),
                ("GMAIL_API_REFRESH_TOKEN", gmail_api_refresh_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"Missing required env var(s) for EMAIL_TRANSPORT=gmail-api: {', '.join(missing)}"
            )

    return Config(
        gmail_user=_require("GMAIL_USER"),
        gmail_app_password=_require("GMAIL_APP_PASSWORD"),
        imap_host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
        imap_port=int(os.environ.get("IMAP_PORT", "993")),
        smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        digest_to=_require("DIGEST_TO"),
        digest_from_name=os.environ.get("DIGEST_FROM_NAME", "Newsletter Digest"),
        email_transport=email_transport,
        anthropic_api_key=anthropic_api_key,
        processing_mode=processing_mode,
        anthropic_model_stage1=os.environ.get("ANTHROPIC_MODEL_STAGE1", legacy_model),
        anthropic_model_stage2=os.environ.get("ANTHROPIC_MODEL_STAGE2", legacy_model),
        stage1_max_workers=int(os.environ.get("STAGE1_MAX_WORKERS", "5")),
        stage1_input_usd_per_mtok=float(os.environ.get("STAGE1_INPUT_USD_PER_MTOK", "3.0")),
        stage1_output_usd_per_mtok=float(os.environ.get("STAGE1_OUTPUT_USD_PER_MTOK", "15.0")),
        stage1_cache_write_usd_per_mtok=float(os.environ.get("STAGE1_CACHE_WRITE_USD_PER_MTOK", "3.75")),
        stage1_cache_read_usd_per_mtok=float(os.environ.get("STAGE1_CACHE_READ_USD_PER_MTOK", "0.30")),
        stage2_input_usd_per_mtok=float(os.environ.get("STAGE2_INPUT_USD_PER_MTOK", "3.0")),
        stage2_output_usd_per_mtok=float(os.environ.get("STAGE2_OUTPUT_USD_PER_MTOK", "15.0")),
        stage2_cache_write_usd_per_mtok=float(os.environ.get("STAGE2_CACHE_WRITE_USD_PER_MTOK", "3.75")),
        stage2_cache_read_usd_per_mtok=float(os.environ.get("STAGE2_CACHE_READ_USD_PER_MTOK", "0.30")),
        lookback_days=int(os.environ.get("LOOKBACK_DAYS", "1")),
        data_dir=data_dir,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        dry_run=os.environ.get("DRY_RUN", "0") in ("1", "true", "True", "yes"),
        gmail_api_client_id=gmail_api_client_id,
        gmail_api_client_secret=gmail_api_client_secret,
        gmail_api_refresh_token=gmail_api_refresh_token,
    )
