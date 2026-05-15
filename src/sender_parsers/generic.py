from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseNewsletterParser, ParserHelpers

if TYPE_CHECKING:
    from ..rule_digest import RuleTopic, SenderRuleProfile


class GenericNewsletterParser(BaseNewsletterParser):
    parser_name = "generic"

    def parse(
        self,
        text_content: str,
        raw_links: list[dict],
        profile: "SenderRuleProfile",
        helpers: ParserHelpers,
    ) -> list["RuleTopic"]:
        return helpers.extract_rule_topics_generic(
            text_content=text_content,
            raw_links=raw_links,
            max_topics=profile.max_topics,
        )
