from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .link_dense import LinkDenseNewsletterParser

if TYPE_CHECKING:
    from ..rule_digest import RuleTopic, SenderRuleProfile
    from .base import ParserHelpers


class ExanteParser(LinkDenseNewsletterParser):
    parser_name = "exante"
    min_title_words = 3

    _SECTION_RE = re.compile(r"(?m)^\s*(#{1,2})\s+(.+?)\s*$")
    _BOLD_LEAD_RE = re.compile(r"\*\*([^*]{12,220})\*\*\s*(.+?)(?=(?:\n\s*\*\*[^*]{12,220}\*\*)|\Z)", re.S)
    _SKIP_SECTION_PREFIXES = (
        "key data to move markets today",
        "read the report",
    )
    _SKIP_TITLE_PREFIXES = (
        "read more",
        "try pulse",
        "daily insights",
    )

    def _split_sections(self, text: str) -> list[tuple[str, str]]:
        matches = list(self._SECTION_RE.finditer(text))
        if not matches:
            return []
        sections: list[tuple[str, str]] = []
        for idx, match in enumerate(matches):
            title = match.group(2).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if body:
                sections.append((title, body))
        return sections

    def parse(
        self,
        text_content: str,
        raw_links: list[dict],
        profile: "SenderRuleProfile",
        helpers: "ParserHelpers",
    ) -> list["RuleTopic"]:
        text_content = helpers.strip_newsletter_footer(text_content)
        text_content = (
            text_content.replace("\u00a0", " ")
            .replace("\u200c", " ")
            .replace("\ufeff", " ")
        )
        used_urls: set[str] = set()
        raw_link_cursor = [0]
        topics: list["RuleTopic"] = []
        seen_titles: set[str] = set()

        def add_topic(title: str, block: str) -> None:
            clean_title = helpers.normalize_plain_text(title).strip(" -:;,.")
            if not clean_title:
                return
            low = clean_title.lower()
            if low.startswith(self._SKIP_TITLE_PREFIXES):
                return
            title_key = re.sub(r"[^a-z0-9]+", " ", low).strip()
            if not title_key or title_key in seen_titles:
                return
            summary = helpers.summary_from_block(helpers.normalize_plain_text(block))
            if len(summary.split()) < max(8, profile.min_summary_words - 3):
                return
            links = helpers.pick_links_for_block(block, raw_links, used_urls, raw_link_cursor)
            topics.append(
                helpers.make_topic(
                    title=helpers.shorten_title(clean_title[:220], max_words=14),
                    summary=summary,
                    links=links,
                )
            )
            seen_titles.add(title_key)

        for section_title, section_body in self._split_sections(text_content):
            if len(topics) >= profile.max_topics:
                break
            normalized_title = helpers.normalize_plain_text(section_title)
            if normalized_title.lower().startswith(self._SKIP_SECTION_PREFIXES):
                continue
            # Prefer richer sub-blocks if present.
            rich_blocks = list(self._BOLD_LEAD_RE.finditer(section_body))
            if rich_blocks:
                for block_match in rich_blocks:
                    if len(topics) >= profile.max_topics:
                        break
                    lead = block_match.group(1).strip()
                    body = block_match.group(2).strip()
                    add_topic(lead, f"{lead}. {body}")
            else:
                add_topic(normalized_title, section_body)

        if len(topics) >= 3:
            return topics[: profile.max_topics]

        baseline = super().parse(text_content, raw_links, profile, helpers)
        if len(baseline) > len(topics):
            return baseline
        return topics or baseline
