from __future__ import annotations

from .base import BaseNewsletterParser
from .exante import ExanteParser
from .generic import GenericNewsletterParser
from .puls_biznesu import PulsBiznesuParser
from .redakcja_xyz import RedakcjaXYZParser


def select_parser(sender_name: str = "", sender_email: str = "") -> BaseNewsletterParser:
    sender_key = f"{(sender_name or '').lower()} {(sender_email or '').lower()}"
    if "puls biznesu" in sender_key or "puls-biznesu" in sender_key:
        return PulsBiznesuParser()
    if "redakcja xyz" in sender_key:
        return RedakcjaXYZParser()
    if "exante" in sender_key:
        return ExanteParser()
    return GenericNewsletterParser()
