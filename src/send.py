from __future__ import annotations

import base64
import json
import logging
import smtplib
from urllib.parse import urlencode
from urllib import error, request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import Config

log = logging.getLogger(__name__)


def _build_message(cfg: Config, subject: str, html_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.digest_from_name, cfg.gmail_user))
    msg["To"] = cfg.digest_to
    msg["Message-ID"] = make_msgid(domain="nsdiggest")
    msg.set_content("Ten email wymaga klienta wspierającego HTML.")
    msg.add_alternative(html_body, subtype="html")
    return msg


def _gmail_api_access_token(cfg: Config) -> str:
    if not cfg.gmail_api_client_id or not cfg.gmail_api_client_secret or not cfg.gmail_api_refresh_token:
        raise RuntimeError(
            "EMAIL_TRANSPORT=gmail-api requires GMAIL_API_CLIENT_ID, "
            "GMAIL_API_CLIENT_SECRET, and GMAIL_API_REFRESH_TOKEN"
        )
    payload = {
        "client_id": cfg.gmail_api_client_id,
        "client_secret": cfg.gmail_api_client_secret,
        "refresh_token": cfg.gmail_api_refresh_token,
        "grant_type": "refresh_token",
    }
    body = urlencode(payload).encode("utf-8")
    req = request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    token = parsed.get("access_token")
    if not token:
        raise RuntimeError("Failed to obtain Gmail API access token")
    return token


def _send_via_gmail_api(cfg: Config, msg: EmailMessage) -> None:
    access_token = _gmail_api_access_token(cfg)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    payload = json.dumps({"raw": raw}).encode("utf-8")
    req = request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as response:
            response.read()
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gmail API send failed: {exc.code} {details}") from exc


def _send_via_smtp(cfg: Config, msg: EmailMessage) -> None:
    log.info("Connecting to SMTP %s:%s", cfg.smtp_host, cfg.smtp_port)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(cfg.gmail_user, cfg.gmail_app_password)
        smtp.send_message(msg)


def send_html_email(cfg: Config, subject: str, html_body: str) -> None:
    msg = _build_message(cfg, subject, html_body)
    if cfg.email_transport == "gmail-api":
        log.info("Sending via Gmail API (HTTPS)")
        _send_via_gmail_api(cfg, msg)
    else:
        _send_via_smtp(cfg, msg)

    log.info("Sent digest to %s subject=%r", cfg.digest_to, subject)
