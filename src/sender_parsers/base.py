from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..rule_digest import RuleTopic, SenderRuleProfile


@dataclass(frozen=True)
class ParserHelpers:
    strip_newsletter_footer: Callable[[str], str]
    iter_markdown_links_with_spans: Callable[[str], list[tuple[int, int, str, str]]]
    is_noise_link: Callable[[str, str], bool]
    normalize_plain_text: Callable[[str], str]
    summary_from_block: Callable[[str], str]
    shorten_title: Callable[[str, int], str]
    pick_links_for_block: Callable[[str, list[dict], set[str], list[int] | None], list[dict]]
    extract_rule_topics_generic: Callable[[str, list[dict], int], list["RuleTopic"]]
    make_topic: Callable[[str, str, list[dict]], "RuleTopic"]


class BaseNewsletterParser:
    parser_name = "base"

    def parse(
        self,
        text_content: str,
        raw_links: list[dict],
        profile: "SenderRuleProfile",
        helpers: ParserHelpers,
    ) -> list["RuleTopic"]:
        raise NotImplementedError()
