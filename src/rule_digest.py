from __future__ import annotations

import re
import statistics
from html import unescape
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .readability import evaluate_human_readability
from .sender_parsers import ParserHelpers, select_parser

BOILERPLATE_PATTERN = re.compile(
    r"(unsubscribe|manage preferences|view in browser|privacy policy|terms of service|stop receiving|"
    r"aby wypisa[cć] si[eę]|kliknij tutaj|pozdrawiamy|redakcja)",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
WORD_RE = re.compile(r"[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}")
TRACKING_TOKEN_RE = re.compile(r"(api-esp|smng\.|attrs=|order=|utm_|vib-cmp|mailing|redirect)", re.IGNORECASE)
TRACKING_QUERY_PREFIXES = ("utm_", "mc_", "vero_", "mkt_")
HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*$")
NUMBERED_ITEM_RE = re.compile(r"^\s*\d+\.\s+(.*\S)\s*$")
MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]+)\]\(<(https?://[^>]+)>\)|\[([^\]]+)\]\((https?://[^)\s]+)\)"
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "this",
    "from",
    "have",
    "you",
    "your",
    "are",
    "was",
    "will",
    "today",
    "about",
    "jest",
    "oraz",
    "czy",
    "dla",
    "które",
    "który",
    "jako",
    "tego",
    "this",
}


@dataclass
class RuleTopic:
    title: str
    summary: str
    links: list[dict]


@dataclass(frozen=True)
class SenderRuleProfile:
    max_topics: int
    min_summary_words: int
    min_link_coverage: float
    min_topics_for_many_links: int


DEFAULT_SENDER_PROFILE = SenderRuleProfile(
    max_topics=24,
    min_summary_words=12,
    min_link_coverage=0.80,
    min_topics_for_many_links=10,
)

SENDER_RULE_OVERRIDES: list[tuple[tuple[str, ...], SenderRuleProfile]] = [
    (
        ("infopiguła", "infopigula"),
        SenderRuleProfile(max_topics=24, min_summary_words=12, min_link_coverage=0.90, min_topics_for_many_links=14),
    ),
    (
        ("puls biznesu",),
        SenderRuleProfile(max_topics=22, min_summary_words=9, min_link_coverage=0.85, min_topics_for_many_links=8),
    ),
    (
        ("redakcja xyz", "xyz"),
        SenderRuleProfile(max_topics=18, min_summary_words=11, min_link_coverage=0.80, min_topics_for_many_links=8),
    ),
    (
        ("zero.pl", "zero"),
        SenderRuleProfile(max_topics=16, min_summary_words=11, min_link_coverage=0.80, min_topics_for_many_links=7),
    ),
    (
        ("exante",),
        SenderRuleProfile(max_topics=12, min_summary_words=9, min_link_coverage=0.70, min_topics_for_many_links=5),
    ),
    (
        ("wefunder",),
        SenderRuleProfile(max_topics=14, min_summary_words=8, min_link_coverage=0.70, min_topics_for_many_links=5),
    ),
    (
        ("infor",),
        SenderRuleProfile(max_topics=14, min_summary_words=11, min_link_coverage=0.75, min_topics_for_many_links=6),
    ),
]


def _resolve_sender_profile(sender_name: str = "", sender_email: str = "") -> SenderRuleProfile:
    sender_key = f"{(sender_name or '').lower()} {(sender_email or '').lower()}"
    for patterns, profile in SENDER_RULE_OVERRIDES:
        if any(token in sender_key for token in patterns):
            return profile
    return DEFAULT_SENDER_PROFILE


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _tokenize(value: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(value) if w.lower() not in STOPWORDS}


def _normalize_plain_text(value: str) -> str:
    raw = unescape(
        (value or "")
        .replace("\u00a0", " ")
        .replace("\u200c", " ")
        .replace("\ufeff", " ")
        .replace("**", "")
        .replace("__", "")
        .replace("_", "")
        .replace("`", "")
    )
    raw = raw.replace("&lt;", " ").replace("&gt;", " ")
    raw = raw.replace("](", " ").replace(")(", " ")
    raw = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", raw)
    raw = re.sub(r"\[[^\]]*$", " ", raw)
    raw = re.sub(r"\]\(<[^)]*$", " ", raw)
    raw = re.sub(
        MARKDOWN_LINK_RE,
        lambda m: (m.group(1) or m.group(3) or "Źródło"),
        raw,
    )
    raw = re.sub(r"https?://\S+", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"(?m)^\s*[-:]{2,}\s*$", " ", raw)
    raw = re.sub(r"\|+", " ", raw)
    raw = re.sub(r"\b\S*(?:attrs=|order=|api-esp|smng\.|vib-cmp)\S*\b", " ", raw, flags=re.IGNORECASE)
    cleaned_tokens: list[str] = []
    for token in raw.split():
        low = token.lower()
        has_digits = any(ch.isdigit() for ch in token)
        has_letters = any(ch.isalpha() for ch in token)
        if "](<" in token or token in {"[", "]", "<", ">", "(<", ">)"}:
            continue
        if TRACKING_TOKEN_RE.search(low):
            continue
        if len(token) >= 12 and has_digits and has_letters:
            continue
        cleaned_tokens.append(token)
    return _normalize_space(" ".join(cleaned_tokens))


def _shorten_title(value: str, max_words: int = 14) -> str:
    words = value.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).rstrip(",;:") + "..."
    return value


def _strip_newsletter_footer(text: str) -> str:
    markers = [
        "Aby wypisać się z newslettera",
        "aby wypisac sie z newslettera",
        "unsubscribe",
        "Pozdrawiamy,",
        "Wasza Redakcja",
    ]
    cut_idx = len(text)
    low = text.lower()
    for marker in markers:
        idx = low.find(marker.lower())
        if idx != -1:
            cut_idx = min(cut_idx, idx)
    return text[:cut_idx].strip()


def _split_blocks(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in text.splitlines()]
    blocks: list[str] = []
    current: list[str] = []

    for line in lines:
        raw = line.strip()
        if not raw:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if raw.startswith(("* ", "- ", "• ", "## ", "### ", "#### ")):
            if current:
                blocks.append("\n".join(current))
                current = []
            blocks.append(raw.lstrip("*-• ").strip())
            continue
        current.append(raw)

    if current:
        blocks.append("\n".join(current))
    return [_normalize_space(b) for b in blocks if _normalize_space(b)]


def _extract_markdown_links(text: str) -> list[dict]:
    links: list[dict] = []
    seen: set[str] = set()
    for match in MARKDOWN_LINK_RE.finditer(text):
        text_a, url_a, text_b, url_b = match.groups()
        link_text = (text_a or text_b or "Źródło").strip()
        url = (url_a or url_b or "").strip()
        if not url or url in seen:
            continue
        if BOILERPLATE_PATTERN.search(link_text) or BOILERPLATE_PATTERN.search(url):
            continue
        seen.add(url)
        links.append({"text": link_text[:120], "url": url})
    return links


def _iter_markdown_links_with_spans(text: str) -> list[tuple[int, int, str, str]]:
    out: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for match in MARKDOWN_LINK_RE.finditer(text):
        text_a, url_a, text_b, url_b = match.groups()
        link_text = (text_a or text_b or "Źródło").strip()
        url = (url_a or url_b or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append((match.start(), match.end(), link_text, url))
    return out


def _is_noise_link(link_text: str, url: str) -> bool:
    low_text = (link_text or "").lower()
    low_url = (url or "").lower()
    if "wypisanie-z-newslettera" in low_url:
        return True
    if "unsubscribe" in low_url or "unsubscribe" in low_text:
        return True
    if low_text in {"audio", "kliknij tutaj"}:
        return True
    return False


def _extract_numbered_items(text: str) -> list[tuple[str, str]]:
    section = ""
    items: list[tuple[str, str]] = []
    current_lines: list[str] = []
    current_section = ""

    def flush() -> None:
        if not current_lines:
            return
        joined = _normalize_plain_text(" ".join(current_lines))
        if len(WORD_RE.findall(joined)) >= 10 and not BOILERPLATE_PATTERN.search(joined):
            items.append((current_section, joined))
        current_lines.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush()
            section = _normalize_plain_text(heading_match.group(1))
            continue
        item_match = NUMBERED_ITEM_RE.match(line)
        if item_match:
            flush()
            current_section = section
            current_lines.append(item_match.group(1))
            continue
        if current_lines:
            current_lines.append(line)
    flush()
    return items


def _is_candidate_block(block: str) -> bool:
    if BOILERPLATE_PATTERN.search(block):
        return False
    tokens = block.split()
    if tokens:
        noisy = 0
        for token in tokens:
            low = token.lower()
            has_digits = any(ch.isdigit() for ch in token)
            has_letters = any(ch.isalpha() for ch in token)
            if TRACKING_TOKEN_RE.search(low) or (len(token) >= 12 and has_digits and has_letters):
                noisy += 1
        if noisy / len(tokens) > 0.18:
            return False
    words = WORD_RE.findall(block)
    if len(words) < 8:
        return False
    if len(words) > 220:
        return False
    return True


def _extract_title_candidate(text: str) -> str:
    cleaned = _normalize_plain_text(text)
    bold_candidates = [
        _normalize_plain_text(m.group(1))
        for m in re.finditer(r"\*\*(.+?)\*\*", text)
        if len(WORD_RE.findall(m.group(1))) >= 4
    ]
    for candidate in bold_candidates:
        if not BOILERPLATE_PATTERN.search(candidate):
            return _shorten_title(candidate[:180], max_words=14)
    first_sentence = SENTENCE_SPLIT_RE.split(cleaned, maxsplit=1)[0].strip()
    first_sentence = re.sub(r"^[\-\*\d\.\)\s]+", "", first_sentence)
    words = first_sentence.split()
    if len(words) > 14:
        return " ".join(words[:14]).rstrip(",;:") + "..."
    return first_sentence or "Temat z newslettera"


def _title_from_block(block: str) -> str:
    return _extract_title_candidate(block)


def _summary_from_block(block: str) -> str:
    clean = _normalize_plain_text(block)
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(clean) if s.strip()]
    filtered: list[str] = []
    for sentence in sentences:
        sentence = re.sub(r"^[\s,;:\-\.\)\]]+", "", sentence).strip()
        sentence = re.sub(r"\[[^\]]*$", "", sentence).strip()
        low = sentence.lower()
        if low.startswith(("zobacz w przeglądarce", "kliknij tutaj", "pobierz aplikację")):
            continue
        if "@" in sentence or "wydawca" in low:
            continue
        if "[](" in sentence or "---" in sentence or "](<" in sentence:
            continue
        tokens = sentence.split()
        if len(tokens) < 6:
            continue
        noisy = 0
        for token in tokens:
            low_token = token.lower()
            has_digits = any(ch.isdigit() for ch in token)
            has_letters = any(ch.isalpha() for ch in token)
            if TRACKING_TOKEN_RE.search(low_token) or (len(token) >= 12 and has_digits and has_letters):
                noisy += 1
        if noisy / max(len(tokens), 1) > 0.16:
            continue
        filtered.append(sentence)
    if filtered:
        sentences = filtered
    if not sentences:
        return clean[:280]
    selected = sentences[:3]
    summary = " ".join(selected)
    if len(summary) > 420:
        summary = summary[:417].rstrip() + "..."
    return summary


def _pick_links_for_block(
    block: str,
    raw_links: list[dict],
    used_urls: set[str],
    raw_link_cursor: list[int] | None = None,
) -> list[dict]:
    markdown_links = _extract_markdown_links(block)
    selected: list[dict] = []
    for lk in markdown_links:
        if lk["url"] in used_urls:
            continue
        if _is_noise_link(lk.get("text", ""), lk["url"]):
            continue
        used_urls.add(lk["url"])
        selected.append(lk)
        if len(selected) >= 3:
            return selected
    if selected:
        return selected

    block_tokens = _tokenize(block)
    scored_links: list[tuple[int, int, dict]] = []

    for idx, lk in enumerate(raw_links):
        url = (lk.get("url") or "").strip()
        if not url or url in used_urls:
            continue
        text = (lk.get("text") or "").strip()
        if _is_noise_link(text, url):
            continue
        overlap = len(block_tokens & _tokenize(text))
        score = overlap * 10 + max(0, 5 - idx)
        if raw_link_cursor is not None:
            distance = abs(idx - raw_link_cursor[0])
            score += max(0, 8 - min(distance, 8))
        scored_links.append((score, idx, lk))

    scored_links.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    for score, _, lk in scored_links:
        if score <= 0:
            continue
        selected.append({"text": (lk.get("text") or "Źródło").strip()[:120], "url": lk["url"]})
        used_urls.add(lk["url"])
        if len(selected) >= 3:
            break
    if selected:
        return selected

    # Deterministic fallback: take next plausible raw link by order.
    if raw_link_cursor is not None:
        i = raw_link_cursor[0]
        while i < len(raw_links):
            lk = raw_links[i]
            i += 1
            url = (lk.get("url") or "").strip()
            text = (lk.get("text") or "").strip()
            if not url or url in used_urls or _is_noise_link(text, url):
                continue
            used_urls.add(url)
            raw_link_cursor[0] = i
            return [{"text": text[:120] or "Źródło", "url": url}]
        raw_link_cursor[0] = i
    return []


def _fallback_topic(text_content: str, raw_links: list[dict]) -> RuleTopic | None:
    for block in _split_blocks(text_content):
        if _is_candidate_block(block):
            return RuleTopic(
                title=_title_from_block(block),
                summary=_summary_from_block(block),
                links=[{"text": (raw_links[0].get("text") or "Źródło")[:120], "url": raw_links[0]["url"]}] if raw_links else [],
            )
    return None


def _topic_from_item(section: str, item: str, raw_links: list[dict], used_urls: set[str]) -> RuleTopic | None:
    item = _normalize_plain_text(item)
    item = re.sub(
        r"^\[audio\]\(<?https?://[^)>]+>?\)\s*\\?-\s*odsłuchaj[^.]*",
        "",
        item,
        flags=re.IGNORECASE,
    )
    item = _normalize_space(item)
    if BOILERPLATE_PATTERN.search(item):
        return None
    title = _extract_title_candidate(item)
    if len(WORD_RE.findall(title)) < 3:
        return None
    summary = _summary_from_block(item)
    if len(WORD_RE.findall(summary)) < 10:
        return None
    if section and section.upper() not in {"NEWS WYDANIA", "TOP", "TOP STORIES"}:
        title = f"{section}: {title}"
    links = _pick_links_for_block(item, raw_links, used_urls)
    return RuleTopic(title=title[:220], summary=summary, links=links)


def _extract_rule_topics_generic(
    text_content: str,
    raw_links: list[dict],
    max_topics: int,
) -> list[RuleTopic]:
    text_content = _strip_newsletter_footer(text_content)
    blocks = _split_blocks(text_content)
    topics: list[RuleTopic] = []
    used_titles: set[str] = set()
    used_urls: set[str] = set()
    raw_link_cursor = [0]
    numbered_items = _extract_numbered_items(text_content)

    for section, item in numbered_items:
        if len(topics) >= max_topics:
            break
        topic = _topic_from_item(section, item, raw_links, used_urls)
        if topic is None:
            continue
        if not topic.links:
            topic.links = _pick_links_for_block(item, raw_links, used_urls, raw_link_cursor)
        title_key = re.sub(r"[^a-z0-9]+", " ", topic.title.lower()).strip()
        if not title_key or title_key in used_titles:
            continue
        used_titles.add(title_key)
        topics.append(topic)

    # Fallback for newsletters that are not strongly list-structured.
    if len(topics) < min(5, max_topics):
        for block in blocks:
            if len(topics) >= max_topics:
                break
            if not _is_candidate_block(block):
                continue
            title = _title_from_block(block)
            title_key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
            if not title_key or title_key in used_titles:
                continue
            used_titles.add(title_key)
            summary = _summary_from_block(block)
            links = _pick_links_for_block(block, raw_links, used_urls, raw_link_cursor)
            topics.append(RuleTopic(title=title, summary=summary, links=links))

    # Remove near-duplicates while preserving order.
    deduped: list[RuleTopic] = []
    for topic in topics:
        duplicate = False
        norm_title = _normalize_topic_text(topic.title)
        for existing in deduped:
            if _texts_similar(norm_title, _normalize_topic_text(existing.title)):
                duplicate = True
                break
        if not duplicate:
            deduped.append(topic)
    topics = deduped[:max_topics]

    if topics:
        return topics
    fallback = _fallback_topic(text_content, raw_links)
    return [fallback] if fallback is not None else []


def extract_rule_topics(
    text_content: str,
    raw_links: list[dict],
    max_topics: int = 24,
    sender_name: str = "",
    sender_email: str = "",
) -> list[RuleTopic]:
    profile = _resolve_sender_profile(sender_name=sender_name, sender_email=sender_email)
    if max_topics > 0:
        profile = SenderRuleProfile(
            max_topics=min(max_topics, profile.max_topics),
            min_summary_words=profile.min_summary_words,
            min_link_coverage=profile.min_link_coverage,
            min_topics_for_many_links=profile.min_topics_for_many_links,
        )
    parser = select_parser(sender_name=sender_name, sender_email=sender_email)
    helpers = ParserHelpers(
        strip_newsletter_footer=_strip_newsletter_footer,
        iter_markdown_links_with_spans=_iter_markdown_links_with_spans,
        is_noise_link=_is_noise_link,
        normalize_plain_text=_normalize_plain_text,
        summary_from_block=_summary_from_block,
        shorten_title=_shorten_title,
        pick_links_for_block=_pick_links_for_block,
        extract_rule_topics_generic=_extract_rule_topics_generic,
        make_topic=lambda title, summary, links: RuleTopic(title=title, summary=summary, links=links),
    )
    return parser.parse(
        text_content=text_content,
        raw_links=raw_links,
        profile=profile,
        helpers=helpers,
    )


def rule_output_is_sufficient(
    topics: list[RuleTopic],
    raw_link_count: int = 0,
    sender_name: str = "",
    sender_email: str = "",
) -> bool:
    profile = _resolve_sender_profile(sender_name=sender_name, sender_email=sender_email)
    if not topics:
        return False
    readability = evaluate_human_readability(topics)
    if readability.score < 0.62:
        return False
    if readability.unreadable_topics / max(readability.topics_count, 1) > 0.15:
        return False
    summary_words = [len(t.summary.split()) for t in topics if t.summary.strip()]
    if not summary_words:
        return False
    if max(summary_words) < 10:
        return False
    if all(not t.links for t in topics):
        return False
    linked_topics = sum(1 for t in topics if t.links)
    missing_topics = len(topics) - linked_topics
    missing_ratio = missing_topics / max(len(topics), 1)
    median_summary_words = statistics.median(summary_words)
    title_word_counts = [len((t.title or "").split()) for t in topics]
    natural_title_ratio = (
        sum(1 for c in title_word_counts if 3 <= c <= 16) / max(len(title_word_counts), 1)
    )

    # High amount of link-less topics usually means bad rule/link mapping.
    if len(topics) >= 4 and missing_ratio > 0.20:
        return False
    # Very short summaries on many topics indicate low information density.
    if len(topics) >= 8 and median_summary_words < profile.min_summary_words:
        return False
    # Keep titles reasonably headline-like.
    if natural_title_ratio < 0.70:
        return False
    # If topic count greatly exceeds source links, this is often over-segmentation.
    if raw_link_count > 0 and len(topics) > max(24, int(raw_link_count * 0.95) + 2):
        return False
    # If source has many links but only a few extracted topics, this is usually under-segmentation.
    if raw_link_count >= 20:
        if len(topics) < profile.min_topics_for_many_links:
            return False
        if len(topics) / raw_link_count < 0.33:
            return False
    elif raw_link_count >= 12 and len(topics) < 6:
        return False
    if linked_topics / max(len(topics), 1) < profile.min_link_coverage:
        return False
    return True


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    query = [(k, v) for k, v in query if k.lower() not in {"ref", "source", "igshid"}]
    normalized_path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            "&".join(f"{k}={v}" for k, v in query),
            "",
        )
    )


def _normalize_topic_text(value: str) -> str:
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", value.lower()).strip()


def _texts_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.88


def compute_rule_dedup_assignments(newsletters: list[dict]) -> list[dict]:
    assignments: list[dict] = []
    seen_topics: list[dict] = []
    seen_by_url: dict[str, int] = {}

    for nl in newsletters:
        nl_idx = int(nl["newsletter_idx"])
        topics = nl.get("topics", [])
        for topic_idx, topic in enumerate(topics):
            links = topic.get("links") or []
            canonical_urls = [canonicalize_url((lk.get("url") or "").strip()) for lk in links]
            canonical_urls = [u for u in canonical_urls if u]

            duplicate_of: int | None = None
            for url in canonical_urls:
                if url in seen_by_url:
                    duplicate_of = seen_by_url[url]
                    break

            if duplicate_of is None:
                title_norm = _normalize_topic_text(topic.get("title", ""))
                summary_norm = _normalize_topic_text(topic.get("summary", ""))
                for seen in seen_topics:
                    if _texts_similar(title_norm, seen["title_norm"]) and _texts_similar(
                        summary_norm, seen["summary_norm"]
                    ):
                        duplicate_of = int(seen["newsletter_idx"])
                        break

            if duplicate_of is not None and duplicate_of != nl_idx:
                assignments.append(
                    {
                        "newsletter_idx": nl_idx,
                        "topic_idx": topic_idx,
                        "duplicate_of_newsletter_idx": duplicate_of,
                    }
                )
                continue

            for url in canonical_urls:
                seen_by_url[url] = nl_idx
            seen_topics.append(
                {
                    "newsletter_idx": nl_idx,
                    "title_norm": _normalize_topic_text(topic.get("title", "")),
                    "summary_norm": _normalize_topic_text(topic.get("summary", "")),
                }
            )
    return assignments
