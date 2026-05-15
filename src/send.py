from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import Config

log = logging.getLogger(__name__)


def send_html_email(cfg: Config, subject: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.digest_from_name, cfg.gmail_user))
    msg["To"] = cfg.digest_to
    msg["Message-ID"] = make_msgid(domain="nsdiggest")
    msg.set_content("Ten email wymaga klienta wspierającego HTML.")
    msg.add_alternative(html_body, subtype="html")

    log.info("Connecting to SMTP %s:%s", cfg.smtp_host, cfg.smtp_port)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(cfg.gmail_user, cfg.gmail_app_password)
        smtp.send_message(msg)
    log.info("Sent digest to %s subject=%r", cfg.digest_to, subject)
