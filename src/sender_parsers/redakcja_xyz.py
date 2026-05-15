from __future__ import annotations

from .link_dense import LinkDenseNewsletterParser


class RedakcjaXYZParser(LinkDenseNewsletterParser):
    parser_name = "redakcja-xyz"
    min_title_words = 6
    max_title_words = 15
    _NOISE_TITLE_PREFIXES = LinkDenseNewsletterParser._NOISE_TITLE_PREFIXES + (
        "wydarzyło się wczoraj",
        "dzisiejszy newsletter przygotowała",
        "dzień dobry tu xyz",
    )
    _NOISE_SUMMARY_SNIPPETS = LinkDenseNewsletterParser._NOISE_SUMMARY_SNIPPETS + (
        "co jeszcze wydarzyło się",
        "ważne dane",
    )
