from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterable

import html2text
from bs4 import BeautifulSoup

from .config import Config

log = logging.getLogger(__name__)

UNSUBSCRIBE_PATTERNS = re.compile(
    r"(unsubscribe|manage[-_]?preferences|list-manage|beacon|tracking[-_]?pixel|/open[/?.]|/pixel[/?.])",
    re.IGNORECASE,
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


@dataclass
class FetchedEmail:
    uid: str
    message_id: str
    sender_name: str
    sender_email: str
    subject: str
    date: str  # YYYY-MM-DD (sender-local-ish; we use UTC date)
    received_at: datetime
    text_content: str
    raw_links: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "message_id": self.message_id,
            "sender": self.sender_name or self.sender_email,
            "sender_email": self.sender_email,
            "subject": self.subject,
            "date": self.date,
            "received_at": self.received_at.isoformat(),
            "text_content": self.text_content,
            "raw_links": self.raw_links,
        }


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _is_useful_link(url: str) -> bool:
    if not url:
        return False
    low = url.lower()
    if low.startswith("mailto:"):
        return False
    if UNSUBSCRIBE_PATTERNS.search(low):
        return False
    if any(low.split("?", 1)[0].endswith(ext) for ext in IMAGE_EXTS):
        return False
    if not (low.startswith("http://") or low.startswith("https://")):
        return False
    return True


def _extract_html_part(msg: Message) -> str:
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            html_parts.append(text)
        elif ctype == "text/plain":
            text_parts.append(text)
    if html_parts:
        return "\n".join(html_parts)
    # fallback: wrap plain text so html2text/BeautifulSoup still work uniformly
    return "<pre>" + "\n".join(text_parts) + "</pre>"


def _html_to_text(html: str) -> str:
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    h.ignore_emphasis = False
    h.protect_links = True
    h.unicode_snob = True
    text = h.handle(html)
    # collapse 3+ blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _extract_raw_links(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not _is_useful_link(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        text = a.get_text(" ", strip=True) or ""
        out.append({"text": text[:200], "url": href})
    return out


def _parse_message(uid: str, raw: bytes) -> FetchedEmail | None:
    msg = email.message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    subject = _decode(msg.get("Subject"))
    from_header = _decode(msg.get("From"))
    sender_name, sender_email = parseaddr(from_header)
    date_header = msg.get("Date")
    try:
        received_at = parsedate_to_datetime(date_header) if date_header else datetime.now(timezone.utc)
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
    except Exception:
        received_at = datetime.now(timezone.utc)

    html = _extract_html_part(msg)
    text_content = _html_to_text(html)
    raw_links = _extract_raw_links(html)

    if not text_content.strip():
        log.warning("Empty text content for UID=%s subject=%r", uid, subject)

    return FetchedEmail(
        uid=uid,
        message_id=message_id,
        sender_name=sender_name or sender_email,
        sender_email=sender_email,
        subject=subject,
        date=received_at.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        received_at=received_at,
        text_content=text_content,
        raw_links=raw_links,
    )


def _search_uids(imap: imaplib.IMAP4_SSL, lookback_days: int) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    uids: set[str] = set()
    for criteria in (f'(SINCE "{since}")', "(UNSEEN)"):
        typ, data = imap.uid("SEARCH", None, criteria)
        if typ != "OK":
            log.warning("IMAP SEARCH %s failed: %s", criteria, data)
            continue
        for chunk in data:
            if not chunk:
                continue
            uids.update(chunk.decode().split())
    return sorted(uids, key=int)


def fetch_newsletters(cfg: Config) -> list[FetchedEmail]:
    log.info("Connecting to IMAP %s:%s as %s", cfg.imap_host, cfg.imap_port, cfg.gmail_user)
    imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        imap.login(cfg.gmail_user, cfg.gmail_app_password)
        imap.select("INBOX")
        uids = _search_uids(imap, cfg.lookback_days)
        log.info("Found %d candidate UIDs (lookback=%dd + UNSEEN)", len(uids), cfg.lookback_days)

        results: list[FetchedEmail] = []
        seen_msgids: set[str] = set()

        for uid in uids:
            typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not data[0]:
                log.warning("FETCH failed for UID=%s", uid)
                continue
            raw = data[0][1] if isinstance(data[0], tuple) else None
            if not raw:
                continue
            parsed = _parse_message(uid, raw)
            if parsed is None:
                continue
            key = parsed.message_id or f"uid:{uid}"
            if key in seen_msgids:
                log.debug("Dedup: skip duplicate Message-ID %s", key)
                continue
            seen_msgids.add(key)
            results.append(parsed)
            log.info(
                "Fetched UID=%s sender=%r subject=%r text_len=%d links=%d",
                uid, parsed.sender_name, parsed.subject, len(parsed.text_content), len(parsed.raw_links),
            )
        return results
    finally:
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()


def mark_seen(cfg: Config, uids: Iterable[str]) -> None:
    uids = list(uids)
    if not uids:
        return
    log.info("Marking %d UIDs as SEEN", len(uids))
    imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        imap.login(cfg.gmail_user, cfg.gmail_app_password)
        imap.select("INBOX")
        # batch in groups of 200 to keep command size sane
        for i in range(0, len(uids), 200):
            batch = uids[i : i + 200]
            typ, data = imap.uid("STORE", ",".join(batch), "+FLAGS", "(\\Seen)")
            if typ != "OK":
                log.warning("STORE +Seen failed for batch starting at %d: %s", i, data)
    finally:
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()


def group_by_date(emails: list[FetchedEmail]) -> dict[str, list[FetchedEmail]]:
    out: dict[str, list[FetchedEmail]] = {}
    for e in emails:
        out.setdefault(e.date, []).append(e)
    for v in out.values():
        v.sort(key=lambda x: x.received_at)
    return dict(sorted(out.items()))
