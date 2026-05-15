from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime

from .config import ProcessingMode, load_config
from .digest import digest_to_json, generate_digest, make_anthropic_client
from .fetch import FetchedEmail, fetch_newsletters, group_by_date, mark_seen
from .render import render_email
from .send import send_html_email


def _parse_index_list(raw: str) -> set[int]:
    out: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        idx = int(token)
        if idx < 0:
            raise ValueError("indices must be >= 0")
        out.add(idx)
    return out


def _setup_logging(level: str, log_file=None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=handlers,
        force=True,
    )


def _persist_raw_emails(data_dir, date: str, emails: list[FetchedEmail]) -> None:
    out_path = data_dir / "raw" / f"{date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.to_dict() for e in emails]
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.getLogger(__name__).info("Saved raw emails: %s (count=%d)", out_path, len(emails))


def _persist_digest(data_dir, date: str, digest_json: str) -> None:
    out_path = data_dir / "digests" / f"{date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(digest_json, encoding="utf-8")


def run() -> int:
    parser = argparse.ArgumentParser(description="NSDiggest — daily newsletter digest")
    parser.add_argument("--dry-run", action="store_true", help="Do not send or mark SEEN")
    parser.add_argument(
        "--only-indices",
        type=str,
        default="",
        help="Process only selected newsletter indices within each day, e.g. 0,3,7",
    )
    parser.add_argument(
        "--max-newsletters",
        type=int,
        default=0,
        help="Limit newsletters per day to first N items after filtering (0 = no limit)",
    )
    parser.add_argument("--skip-send", action="store_true", help="Generate digest but do not send email")
    parser.add_argument("--skip-mark-seen", action="store_true", help="Never mark emails as SEEN")
    parser.add_argument(
        "--processing-mode",
        choices=("no-llm", "llm-only", "hybrid"),
        default="",
        help="Processing strategy override: no-llm, llm-only, hybrid",
    )
    args = parser.parse_args()

    cfg = load_config()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_file = cfg.data_dir / "logs" / f"run-{run_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    _setup_logging(cfg.log_level, log_file=log_file)
    log = logging.getLogger("nsdiggest")

    dry_run = cfg.dry_run or args.dry_run
    skip_send = dry_run or args.skip_send
    skip_mark_seen = dry_run or args.skip_mark_seen
    processing_mode: ProcessingMode = (
        args.processing_mode if args.processing_mode else cfg.processing_mode
    )
    only_indices: set[int] | None = None
    if args.only_indices.strip():
        try:
            only_indices = _parse_index_list(args.only_indices)
        except ValueError as exc:
            logging.getLogger("nsdiggest").error("Invalid --only-indices value: %s", exc)
            return 2
    if args.max_newsletters < 0:
        logging.getLogger("nsdiggest").error("--max-newsletters must be >= 0")
        return 2

    log.info(
        "Starting NSDiggest run_id=%s dry_run=%s skip_send=%s skip_mark_seen=%s only_indices=%s max_newsletters=%d processing_mode=%s stage1_model=%s stage2_model=%s stage1_workers=%d",
        run_id, dry_run, skip_send, skip_mark_seen, sorted(only_indices) if only_indices is not None else None, args.max_newsletters,
        processing_mode, cfg.anthropic_model_stage1, cfg.anthropic_model_stage2, cfg.stage1_max_workers,
    )

    t_run_start = time.monotonic()

    anthropic_client = None
    if processing_mode != "no-llm":
        try:
            anthropic_client = make_anthropic_client(cfg)
        except Exception:
            log.exception("Failed to initialize Anthropic client for processing_mode=%s", processing_mode)
            return 2

    try:
        emails = fetch_newsletters(cfg)
    except Exception:
        log.exception("Failed to fetch newsletters from IMAP")
        return 2

    if not emails:
        log.info("No emails to process — exiting cleanly.")
        return 0

    by_date = group_by_date(emails)
    if only_indices is not None or args.max_newsletters > 0:
        filtered_by_date: dict[str, list[FetchedEmail]] = {}
        for date, day_emails in by_date.items():
            filtered = [
                e
                for idx, e in enumerate(day_emails)
                if only_indices is None or idx in only_indices
            ]
            if args.max_newsletters > 0:
                filtered = filtered[: args.max_newsletters]
            if filtered:
                filtered_by_date[date] = filtered
        by_date = filtered_by_date
    log.info("Grouped into %d day(s): %s", len(by_date), list(by_date.keys()))
    if not by_date:
        log.info("No emails left after CLI filters — exiting cleanly.")
        return 0

    sent_uids: list[str] = []
    failures = 0

    for date, day_emails in by_date.items():
        log.info("=== Processing %s: %d newsletters ===", date, len(day_emails))
        _persist_raw_emails(cfg.data_dir, date, day_emails)

        t_day_start = time.monotonic()
        try:
            digest = generate_digest(
                cfg,
                date,
                day_emails,
                client=anthropic_client,
                processing_mode=processing_mode,
            )
        except Exception:
            log.exception("Digest generation failed for %s", date)
            failures += 1
            continue
        t_day_elapsed = time.monotonic() - t_day_start

        if digest is None or digest.topic_count == 0:
            log.warning(
                "No topics extracted for %s (newsletters=%d elapsed=%.1fs) — skipping send.",
                date, len(day_emails), t_day_elapsed,
            )
            continue

        log.info(
            "Digest ready for %s: newsletters=%d topics=%d duplicates=%d empty=%d est_cost_usd=%.6f elapsed=%.1fs",
            date,
            len(digest.newsletters),
            digest.topic_count,
            digest.duplicate_count,
            sum(1 for n in digest.newsletters if not n.topics),
            digest.estimated_cost_usd,
            t_day_elapsed,
        )

        _persist_digest(cfg.data_dir, date, digest_to_json(digest))
        subject, html_body = render_email(digest)

        if skip_send:
            preview_path = cfg.data_dir / "preview" / f"{date}.html"
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_text(html_body, encoding="utf-8")
            mode = "DRY RUN" if dry_run else "SKIP SEND"
            log.info("%s: wrote preview %s subject=%r", mode, preview_path, subject)
            continue

        try:
            send_html_email(cfg, subject, html_body)
        except Exception:
            log.exception("SMTP send failed for %s — not marking SEEN", date)
            failures += 1
            continue

        sent_uids.extend(e.uid for e in day_emails)

    if sent_uids and not skip_mark_seen:
        try:
            mark_seen(cfg, sent_uids)
        except Exception:
            log.exception("Failed to mark SEEN — emails will be reprocessed on next run")
            failures += 1

    t_run_elapsed = time.monotonic() - t_run_start
    log.info(
        "Done. days=%d emails=%d sent_uids=%d failures=%d total_elapsed=%.1fs",
        len(by_date), len(emails), len(sent_uids), failures, t_run_elapsed,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
