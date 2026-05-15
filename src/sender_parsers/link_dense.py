from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import BaseNewsletterParser, ParserHelpers

if TYPE_CHECKING:
    from ..rule_digest import RuleTopic, SenderRuleProfile


class LinkDenseNewsletterParser(BaseNewsletterParser):
    parser_name = "link-dense"

    min_title_words = 4
    _NOISE_TITLE_PREFIXES = (
        "zobacz w przeglądarce",
        "przejdź na",
        "czytaj w dzisiejszym wydaniu",
        "pobierz aplikację",
        "read more",
        "czytaj więcej",
        "audio",
    )
    _NOISE_SUMMARY_SNIPPETS = (
        "otrzymujesz niniejszą korespondencję",
        "administratorem twoich danych",
        "wyraziłeś zgodę na otrzymywanie",
        "dzień dobry",
        "wydawca",
        "zapraszam do lektury",
    )
    _NOISE_TITLE_PREFIXES_EXTRA = (
        "i ",
        "a ",
        "oraz ",
        "ale ",
        "bo ",
        ",",
        ".",
        "—",
        "-",
    )
    max_title_words = 16
    prefer_capitalized_titles = True

    def _looks_like_good_title(self, title: str) -> bool:
        words = title.split()
        if len(words) < self.min_title_words or len(words) > self.max_title_words:
            return False
        low = title.lower()
        if low.startswith(self._NOISE_TITLE_PREFIXES + self._NOISE_TITLE_PREFIXES_EXTRA):
            return False
        alpha_chars = sum(1 for ch in title if ch.isalpha())
        if alpha_chars / max(len(title), 1) < 0.60:
            return False
        if self.prefer_capitalized_titles:
            first_alpha = next((ch for ch in title if ch.isalpha()), "")
            if first_alpha and first_alpha.islower():
                return False
        return True

    def _cleanup_summary(self, summary: str) -> str:
        clean = re.sub(r"\[[^\]]*$", "", summary).strip()
        clean = re.sub(r"\]\(<[^)]*$", "", clean).strip()
        clean = re.sub(r"^[\s,;:\-\.\)\]]+", "", clean).strip()
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean and clean[0].islower():
            clean = clean[0].upper() + clean[1:]
        if clean and not clean.endswith((".", "!", "?")):
            clean += "."
        return clean

    def parse(
        self,
        text_content: str,
        raw_links: list[dict],
        profile: "SenderRuleProfile",
        helpers: ParserHelpers,
    ) -> list["RuleTopic"]:
        text_content = helpers.strip_newsletter_footer(text_content)
        topics: list["RuleTopic"] = []
        seen_titles: set[str] = set()
        seen_urls: set[str] = set()
        link_spans = helpers.iter_markdown_links_with_spans(text_content)
        for i, (start, end, link_text, url) in enumerate(link_spans):
            if helpers.is_noise_link(link_text, url):
                continue
            clean_title = helpers.normalize_plain_text(link_text)
            if not self._looks_like_good_title(clean_title):
                continue
            title_key = re.sub(r"[^a-z0-9]+", " ", clean_title.lower()).strip()
            if not title_key or title_key in seen_titles or url in seen_urls:
                continue
            next_start = link_spans[i + 1][0] if i + 1 < len(link_spans) else len(text_content)
            # Prefer local context AFTER current link to avoid cross-topic bleed.
            context_end = min(next_start, end + 320)
            window = text_content[end:context_end]
            if len(window) < 80:
                window = text_content[end: min(len(text_content), end + 420)]
            summary = self._cleanup_summary(helpers.summary_from_block(helpers.normalize_plain_text(window)))
            first_sentence = summary.split(". ", 1)[0].strip()
            if len(first_sentence.split()) >= 8:
                summary = first_sentence.rstrip(".") + "."
            summary = self._cleanup_summary(summary)
            if not summary:
                continue
            summary_low = summary.lower()
            if any(marker in summary_low for marker in self._NOISE_SUMMARY_SNIPPETS):
                continue
            if len(summary.split()) < max(8, profile.min_summary_words - 2):
                continue
            topics.append(
                helpers.make_topic(
                    title=helpers.shorten_title(clean_title[:220], max_words=14),
                    summary=summary,
                    links=[{"text": clean_title[:120], "url": url}],
                )
            )
            seen_titles.add(title_key)
            seen_urls.add(url)
            if len(topics) >= profile.max_topics:
                break

        if topics:
            return topics
        return helpers.extract_rule_topics_generic(
            text_content=text_content,
            raw_links=raw_links,
            max_topics=profile.max_topics,
        )
