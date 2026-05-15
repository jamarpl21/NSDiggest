from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from datetime import datetime, timezone

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


def _sender_key(email: FetchedEmail) -> str:
    return (email.sender_email or "").strip().lower() or (email.sender_name or "").strip().lower()


def _build_day_metrics(date: str, digest, day_emails: list[FetchedEmail], elapsed_s: float) -> dict:
    by_index = {idx: email for idx, email in enumerate(day_emails)}
    newsletter_metrics: list[dict] = []
    for nl in digest.newsletters:
        src_email = by_index.get(nl.original_index)
        raw_links_count = len(src_email.raw_links) if src_email is not None else 0
        topic_count = len(nl.topics)
        linked_topics = sum(1 for t in nl.topics if t.links)
        missing_links = topic_count - linked_topics
        duplicate_topics = sum(1 for t in nl.topics if t.duplicate_of is not None)
        summary_lengths = [len((t.summary or "").split()) for t in nl.topics if (t.summary or "").strip()]
        median_summary_words = statistics.median(summary_lengths) if summary_lengths else 0.0
        newsletter_metrics.append(
            {
                "original_index": nl.original_index,
                "sender": nl.sender,
                "sender_key": _sender_key(src_email) if src_email is not None else "",
                "subject": nl.subject,
                "processed_with": nl.processed_with,
                "topic_count": topic_count,
                "linked_topics": linked_topics,
                "missing_link_topics": missing_links,
                "link_coverage_ratio": (linked_topics / topic_count) if topic_count > 0 else 0.0,
                "duplicate_topics": duplicate_topics,
                "median_summary_words": median_summary_words,
                "raw_links_count": raw_links_count,
                "estimated_cost_usd": nl.estimated_cost_usd,
                "stage1_input_tokens": nl.stage1_input_tokens,
                "stage1_output_tokens": nl.stage1_output_tokens,
            }
        )

    total_topics = sum(n["topic_count"] for n in newsletter_metrics)
    total_linked_topics = sum(n["linked_topics"] for n in newsletter_metrics)
    total_missing_link_topics = sum(n["missing_link_topics"] for n in newsletter_metrics)
    return {
        "date": date,
        "processing_mode": digest.processing_mode,
        "elapsed_s": elapsed_s,
        "newsletter_count": len(digest.newsletters),
        "topic_count": total_topics,
        "duplicate_count": digest.duplicate_count,
        "empty_newsletter_count": sum(1 for n in digest.newsletters if not n.topics),
        "estimated_cost_usd": digest.estimated_cost_usd,
        "link_coverage_ratio": (total_linked_topics / total_topics) if total_topics > 0 else 0.0,
        "missing_link_topics": total_missing_link_topics,
        "newsletters": newsletter_metrics,
    }


def _persist_run_metrics(data_dir, run_id: str, payload: dict) -> None:
    run_path = data_dir / "runs" / f"{run_id}.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = data_dir / "runs" / "latest.json"
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.getLogger(__name__).info("Saved run metrics: %s", run_path)


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
    run_started_at = datetime.now(timezone.utc)
    run_id = run_started_at.strftime("%Y%m%dT%H%M%SZ")
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
    days_metrics: list[dict] = []

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
        days_metrics.append(_build_day_metrics(date, digest, day_emails, t_day_elapsed))

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
    run_payload = {
        "run_id": run_id,
        "started_at_utc": run_started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "processing_mode": processing_mode,
        "dry_run": dry_run,
        "skip_send": skip_send,
        "skip_mark_seen": skip_mark_seen,
        "only_indices": sorted(only_indices) if only_indices is not None else None,
        "max_newsletters": args.max_newsletters,
        "total_days": len(by_date),
        "total_fetched_emails": len(emails),
        "total_sent_uids": len(sent_uids),
        "failures": failures,
        "total_elapsed_s": t_run_elapsed,
        "days": days_metrics,
        "totals": {
            "newsletters": sum(day["newsletter_count"] for day in days_metrics),
            "topics": sum(day["topic_count"] for day in days_metrics),
            "duplicates": sum(day["duplicate_count"] for day in days_metrics),
            "empty_newsletters": sum(day["empty_newsletter_count"] for day in days_metrics),
            "missing_link_topics": sum(day["missing_link_topics"] for day in days_metrics),
            "estimated_cost_usd": sum(day["estimated_cost_usd"] for day in days_metrics),
        },
    }
    _persist_run_metrics(cfg.data_dir, run_id, run_payload)

    log.info(
        "Done. days=%d emails=%d sent_uids=%d failures=%d total_elapsed=%.1fs",
        len(by_date), len(emails), len(sent_uids), failures, t_run_elapsed,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
