from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlsplit, urlunsplit

BOILERPLATE_PATTERN = re.compile(
    r"(unsubscribe|manage preferences|view in browser|privacy policy|terms of service|stop receiving|"
    r"aby wypisa[cć] si[eę]|kliknij tutaj|pozdrawiamy|redakcja)",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
WORD_RE = re.compile(r"[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}")
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


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _tokenize(value: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(value) if w.lower() not in STOPWORDS}


def _normalize_plain_text(value: str) -> str:
    return _normalize_space(
        value.replace("**", "").replace("__", "").replace("_", "").replace("`", "")
    )


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


def extract_rule_topics(text_content: str, raw_links: list[dict], max_topics: int = 24) -> list[RuleTopic]:
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


def rule_output_is_sufficient(topics: list[RuleTopic]) -> bool:
    if not topics:
        return False
    summary_words = [len(t.summary.split()) for t in topics if t.summary.strip()]
    if not summary_words:
        return False
    if max(summary_words) < 10:
        return False
    if all(not t.links for t in topics):
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
