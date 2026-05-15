from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


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

    anthropic_api_key: str

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

    return Config(
        gmail_user=_require("GMAIL_USER"),
        gmail_app_password=_require("GMAIL_APP_PASSWORD"),
        imap_host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
        imap_port=int(os.environ.get("IMAP_PORT", "993")),
        smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        digest_to=_require("DIGEST_TO"),
        digest_from_name=os.environ.get("DIGEST_FROM_NAME", "Newsletter Digest"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
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
    )
