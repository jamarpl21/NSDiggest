from __future__ import annotations

from .link_dense import LinkDenseNewsletterParser


class PulsBiznesuParser(LinkDenseNewsletterParser):
    parser_name = "puls-biznesu"
    min_title_words = 5
