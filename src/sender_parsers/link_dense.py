from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import BaseNewsletterParser, ParserHelpers

if TYPE_CHECKING:
    from ..rule_digest import RuleTopic, SenderRuleProfile


class LinkDenseNewsletterParser(BaseNewsletterParser):
    parser_name = "link-dense"

    min_title_words = 4

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
        for start, end, link_text, url in helpers.iter_markdown_links_with_spans(text_content):
            if helpers.is_noise_link(link_text, url):
                continue
            clean_title = helpers.normalize_plain_text(link_text)
            if len(clean_title.split()) < self.min_title_words:
                continue
            if clean_title.lower().startswith(("zobacz w przeglądarce", "przejdź na", "czytaj w dzisiejszym wydaniu")):
                continue
            title_key = re.sub(r"[^a-z0-9]+", " ", clean_title.lower()).strip()
            if not title_key or title_key in seen_titles or url in seen_urls:
                continue
            window = text_content[max(0, start - 260): min(len(text_content), end + 480)]
            summary = helpers.summary_from_block(helpers.normalize_plain_text(window))
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
