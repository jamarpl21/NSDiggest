from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .link_dense import LinkDenseNewsletterParser

if TYPE_CHECKING:
    from ..rule_digest import RuleTopic, SenderRuleProfile
    from .base import ParserHelpers


class PulsBiznesuParser(LinkDenseNewsletterParser):
    parser_name = "puls-biznesu"
    min_title_words = 5
    max_title_words = 18
    prefer_capitalized_titles = False
    _NOISE_SUMMARY_SNIPPETS = LinkDenseNewsletterParser._NOISE_SUMMARY_SNIPPETS + (
        "tadeusz stasiuk",
        "dzień dobry, dziś już",
        "to warto wiedzieć przed sesją",
    )

    _NOISE_TITLE_SNIPPETS = (
        "temat z newslettera",
        "zobacz w przeglądarce",
    )

    def _clean_fragment(self, value: str) -> str:
        clean = re.sub(r"^[\s,;:\-\.\)\]]+", "", value or "").strip()
        clean = re.sub(r"\[[^\]]*$", "", clean).strip()
        clean = re.sub(r"\]\(<[^)]*$", "", clean).strip()
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _topic_looks_usable(self, title: str, summary: str) -> bool:
        title_low = title.lower()
        if any(marker in title_low for marker in self._NOISE_TITLE_SNIPPETS):
            return False
        title_words = title.split()
        if len(title_words) < self.min_title_words or len(title_words) > self.max_title_words:
            return False
        if len(summary.split()) < 6:
            return False
        return True

    def _normalize_topics(self, topics: list["RuleTopic"], max_topics: int) -> list["RuleTopic"]:
        out: list["RuleTopic"] = []
        seen_titles: set[str] = set()
        seen_urls: set[str] = set()
        for topic in topics:
            title = self._clean_fragment(topic.title)
            summary = self._clean_fragment(topic.summary)
            summary = self._cleanup_summary(summary)
            if not self._topic_looks_usable(title, summary):
                continue
            links: list[dict] = []
            for link in topic.links:
                url = (link.get("url") or "").strip()
                text = self._clean_fragment(link.get("text") or "Źródło")[:120] or "Źródło"
                if not url or url in seen_urls:
                    continue
                if "unsubscribe" in url.lower() or "wypisanie-z-newslettera" in url.lower():
                    continue
                links.append({"text": text, "url": url})
                seen_urls.add(url)
                if len(links) >= 3:
                    break
            if not links:
                continue
            title_key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
            if not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            out.append(
                type(topic)(
                    title=title,
                    summary=summary,
                    links=links,
                )
            )
            if len(out) >= max_topics:
                break
        return out

    def parse(
        self,
        text_content: str,
        raw_links: list[dict],
        profile: "SenderRuleProfile",
        helpers: "ParserHelpers",
    ) -> list["RuleTopic"]:
        primary = super().parse(text_content=text_content, raw_links=raw_links, profile=profile, helpers=helpers)
        normalized_primary = self._normalize_topics(primary, profile.max_topics)
        if len(raw_links) < 20 or len(normalized_primary) >= 8:
            return normalized_primary

        generic = helpers.extract_rule_topics_generic(
            text_content=text_content,
            raw_links=raw_links,
            max_topics=profile.max_topics,
        )
        merged = normalized_primary + generic
        normalized_merged = self._normalize_topics(merged, profile.max_topics)
        if len(normalized_merged) >= max(len(normalized_primary), 6):
            return normalized_merged
        return normalized_primary
