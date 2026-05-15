from __future__ import annotations

from .link_dense import LinkDenseNewsletterParser


class RedakcjaXYZParser(LinkDenseNewsletterParser):
    parser_name = "redakcja-xyz"
    min_title_words = 5
