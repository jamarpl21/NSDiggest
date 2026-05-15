from __future__ import annotations

import html
import re

from .digest import Digest, DigestNewsletter, DigestTopic

AVATAR_PALETTE = [
    ("#E6F1FB", "#185FA5"),
    ("#EEEDFE", "#3C3489"),
    ("#E1F5EE", "#085041"),
    ("#FAECE7", "#993C1D"),
    ("#FBEAF0", "#993556"),
]


def _initials(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "?"
    words = re.split(r"\s+", name)
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()


def _palette(idx: int) -> tuple[str, str]:
    return AVATAR_PALETTE[idx % len(AVATAR_PALETTE)]


def _format_date_pl(iso_date: str) -> str:
    months = {
        "01": "stycznia", "02": "lutego", "03": "marca", "04": "kwietnia",
        "05": "maja", "06": "czerwca", "07": "lipca", "08": "sierpnia",
        "09": "września", "10": "października", "11": "listopada", "12": "grudnia",
    }
    try:
        y, m, d = iso_date.split("-")
        return f"{int(d)} {months[m]} {y}"
    except Exception:
        return iso_date


def _render_links(links: list[dict]) -> str:
    if not links:
        return '<span style="font-size:12px;color:#999;">(brak linka w oryginale)</span>'
    parts = []
    for i, lk in enumerate(links[:3]):
        text = html.escape((lk.get("text") or "Źródło").strip()[:80] or "Źródło")
        url = html.escape(lk.get("url", ""), quote=True)
        sep = '<span style="color:#ccc;margin:0 6px;">·</span>' if i > 0 else ""
        parts.append(
            f'{sep}<a href="{url}" style="font-size:12px;color:#185FA5;text-decoration:none;">{text} →</a>'
        )
    return "".join(parts)


def _render_topic(topic: DigestTopic) -> str:
    title = html.escape(topic.title)
    summary = html.escape(topic.summary)
    duplicate_badge = ""
    if topic.duplicate_of is not None:
        duplicate_badge = (
            '<span style="display:inline-block;font-size:10px;font-weight:600;color:#888;'
            'background:#f0f0f0;padding:2px 6px;border-radius:4px;margin-left:6px;'
            'vertical-align:middle;">duplikat</span>'
        )
    return (
        '<div style="margin-left:36px;margin-bottom:14px;">'
        f'<p style="font-weight:600;font-size:14px;margin:0 0 4px;color:#1a1a1a;">{title}{duplicate_badge}</p>'
        f'<p style="font-size:13px;color:#555;margin:0 0 6px;line-height:1.6;">{summary}</p>'
        f'<div>{_render_links(topic.links)}</div>'
        '</div>'
    )


def _render_newsletter(newsletter: DigestNewsletter, idx: int) -> str:
    bg, fg = _palette(idx)
    initials = _initials(newsletter.sender)
    sender = html.escape(newsletter.sender)
    cost_txt = f"${newsletter.estimated_cost_usd:.4f}"
    source_badge = "LLM" if newsletter.processed_with == "llm" else "reguły"
    topics_html = "\n".join(_render_topic(t) for t in newsletter.topics)
    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px;">
      <div style="display:flex;align-items:center;gap:8px;">
      <div style="width:28px;height:28px;border-radius:50%;background:{bg};display:flex;align-items:center;justify-content:center;font-weight:600;font-size:11px;color:{fg};">{html.escape(initials)}</div>
      <span style="font-weight:600;font-size:15px;color:#1a1a1a;">{sender}</span>
      </div>
      <span style="font-size:11px;color:#888;">{source_badge} · koszt LLM ~ {cost_txt}</span>
    </div>
    {topics_html}
    """


def render_email(digest: Digest) -> tuple[str, str]:
    date_pl = _format_date_pl(digest.date)
    n_newsletters = len(digest.newsletters)
    n_topics = digest.topic_count
    n_dupes = digest.duplicate_count
    total_cost = digest.estimated_cost_usd
    llm_newsletters = sum(1 for n in digest.newsletters if n.processed_with == "llm")
    mode_label = {
        "no-llm": "bez LLM",
        "llm-only": "tylko LLM",
        "hybrid": "hybryda",
    }.get(digest.processing_mode, digest.processing_mode)

    sections = []
    for i, nl in enumerate(digest.newsletters):
        sections.append(_render_newsletter(nl, i))
        if i < n_newsletters - 1:
            sections.append('<div style="height:1px;background:#eee;margin:16px 0;"></div>')

    body = "\n".join(sections)

    stats_suffix = (
        f"tryb: {mode_label} · {llm_newsletters}/{n_newsletters} przez LLM · koszt LLM ~ ${total_cost:.4f}"
        if digest.processing_mode == "hybrid"
        else f"tryb: {mode_label} · koszt LLM ~ ${total_cost:.4f}"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:600px;margin:20px auto;background:#fff;border-radius:12px;overflow:hidden;">

  <div style="padding:24px 20px 16px;border-bottom:1px solid #eee;">
    <h1 style="margin:0;font-size:20px;font-weight:600;color:#1a1a1a;">Newslettery {html.escape(date_pl)}</h1>
    <p style="margin:4px 0 0;font-size:13px;color:#888;">{n_newsletters} newsletter{'ów' if n_newsletters != 1 else ''} · {n_topics} temat{'ów' if n_topics != 1 else ''} · {n_dupes} powtarzających się · {stats_suffix}</p>
  </div>

  <div style="padding:20px;">
{body}
  </div>

</div>
</body></html>"""

    subject = f"Newslettery {date_pl} — {n_newsletters} źródeł, {n_topics} tematów"
    return subject, html_body
