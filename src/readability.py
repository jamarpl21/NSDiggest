from __future__ import annotations

import re
from dataclasses import dataclass

WORD_RE = re.compile(r"[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}")
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
NOISE_MARKER_RE = re.compile(
    r"(api-esp|smng\.|attrs=|order=|utm_|/[-]?\d+/[-]?\d+|vib-cmp|mailing|click|redirect)",
    re.IGNORECASE,
)
SYMBOL_CLUSTER_RE = re.compile(r"[-_=|]{3,}")
MARKUP_NOISE_RE = re.compile(r"(\[\]\(|\]\(<|&lt;|&gt;|mailto:|---)", re.IGNORECASE)


@dataclass(frozen=True)
class ReadabilityStats:
    score: float
    unreadable_topics: int
    topics_count: int


def _topic_score(title: str, summary: str) -> float:
    text = f"{title or ''} {summary or ''}".strip()
    if not text:
        return 0.0

    words = WORD_RE.findall(text)
    word_count = len(words)
    lower_text = text.lower()

    score = 1.0
    if word_count < 10:
        score -= 0.25
    if URL_RE.search(summary or ""):
        score -= 0.35
    if NOISE_MARKER_RE.search(lower_text):
        score -= 0.35
    if MARKUP_NOISE_RE.search(lower_text):
        score -= 0.25
    if SYMBOL_CLUSTER_RE.search(text):
        score -= 0.15

    noisy_tokens = 0
    for token in (summary or "").split():
        low = token.lower()
        has_digits = any(ch.isdigit() for ch in token)
        has_letters = any(ch.isalpha() for ch in token)
        if (has_digits and has_letters and len(token) >= 8) or NOISE_MARKER_RE.search(low):
            noisy_tokens += 1
    noise_ratio = noisy_tokens / max(len((summary or "").split()), 1)
    if noise_ratio >= 0.20:
        score -= 0.30
    elif noise_ratio >= 0.10:
        score -= 0.15

    return max(0.0, min(1.0, score))


def evaluate_human_readability(topics: list[object]) -> ReadabilityStats:
    if not topics:
        return ReadabilityStats(score=0.0, unreadable_topics=0, topics_count=0)

    scores: list[float] = []
    unreadable = 0
    for topic in topics:
        title = str(getattr(topic, "title", "") or "")
        summary = str(getattr(topic, "summary", "") or "")
        s = _topic_score(title, summary)
        scores.append(s)
        if s < 0.45:
            unreadable += 1
    avg = sum(scores) / len(scores)
    return ReadabilityStats(score=avg, unreadable_topics=unreadable, topics_count=len(scores))
