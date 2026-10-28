"""Render snapshot JSON files into a static HTML site."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from .models import HotItem, Snapshot, load_snapshot

PLATFORM_NAMES = {
    "zhihu": "知乎",
    "weibo": "微博",
}

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --card: #ffffff;
  --text: #1c1e21;
  --muted: #6b7280;
  --accent: #2563eb;
  --border: #e5e7eb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418;
    --card: #1a1e24;
    --text: #e6e8eb;
    --muted: #9aa1ab;
    --accent: #6ea8fe;
    --border: #2a2f37;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2rem 1rem 4rem;
  background: var(--bg);
  color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.6;
}
main { max-width: 760px; margin: 0 auto; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.5rem; margin: 0.5rem 0 1rem; }
header { margin-bottom: 1.5rem; }
.back { font-size: 0.9rem; }
.meta { color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0; }
.snapshots, .items { list-style: none; padding: 0; margin: 0; }
.snapshots li {
  display: flex;
  gap: 0.75rem;
  align-items: baseline;
  padding: 0.6rem 0.9rem;
  margin-bottom: 0.5rem;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.snapshots .date { font-variant-numeric: tabular-nums; font-weight: 600; }
.snapshots .count { margin-left: auto; color: var(--muted); font-size: 0.85rem; }
.items li {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.6rem;
}
.items .rank {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  margin-right: 0.5rem;
}
.items .title { font-weight: 600; }
.excerpt { color: var(--muted); font-size: 0.9rem; margin: 0.35rem 0 0; }
.empty { color: var(--muted); }
code {
  background: var(--border);
  padding: 0.1rem 0.35rem;
  border-radius: 5px;
  font-size: 0.9em;
}
section { margin-top: 2rem; }
h2 { font-size: 1.15rem; margin: 0 0 0.75rem; }
.badge {
  display: inline-block;
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  margin-right: 0.4rem;
}
.badge.pos { background: rgba(22, 163, 74, 0.14); color: #15803d; }
.badge.neg { background: rgba(220, 38, 38, 0.14); color: #b91c1c; }
.badge.neutral { background: rgba(107, 114, 128, 0.18); color: #4b5563; }
.badge.mixed { background: rgba(217, 119, 6, 0.16); color: #b45309; }
@media (prefers-color-scheme: dark) {
  .badge.pos { color: #4ade80; }
  .badge.neg { color: #f87171; }
  .badge.neutral { color: #cbd5e1; }
  .badge.mixed { color: #fbbf24; }
}
.themes, .report-items { list-style: none; padding: 0; margin: 0; }
.themes li, .report-items > li {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.6rem;
}
.report-item .title { font-weight: 600; }
.viewpoints { margin: 0.4rem 0 0; padding-left: 1.1rem; }
.viewpoints li { margin: 0.15rem 0; }
.question-links { margin: 0.35rem 0 0; font-size: 0.85rem; }
.question-links a { margin-right: 0.6rem; }
.overall .tone { font-weight: 600; margin: 0.25rem 0; }
.report-link { font-size: 0.85rem; }
"""


def _escape(value: str, quote: bool = True) -> str:
    return html.escape(value, quote=quote)


def _platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform)


def _slug(platform: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", platform.lower()).strip("-")
    return slug or "platform"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def _render_item(item: HotItem) -> str:
    parts = []
    if item.hot_score is not None:
        parts.append(f"热度 {item.hot_score:,}")
    if item.answer_count is not None:
        parts.append(f"{item.answer_count:,} 个回答")
    meta = f'<p class="meta">{" · ".join(parts)}</p>\n' if parts else ""
    excerpt = f'<p class="excerpt">{_escape(item.excerpt)}</p>\n' if item.excerpt else ""
    return (
        "<li>\n"
        f'<span class="rank">{item.rank}</span>\n'
        f'<a class="title" href="{_escape(item.url, quote=True)}" '
        f'rel="noopener noreferrer" target="_blank">{_escape(item.title)}</a>\n'
        f"{meta}{excerpt}"
        "</li>"
    )


def _render_day(date: str, snapshot: Snapshot) -> str:
    if snapshot.items:
        items = "\n".join(_render_item(item) for item in snapshot.items)
        items_html = f'<ol class="items">\n{items}\n</ol>'
    else:
        items_html = '<p class="empty">这个快照没有条目。</p>'

    platform = _platform_name(snapshot.platform)
    body = (
        "<header>\n"
        '<a class="back" href="index.html">← 全部快照</a>\n'
        f"<h1>{_escape(platform)}热点 · {_escape(date)}</h1>\n"
        f'<p class="meta">抓取时间：{_escape(snapshot.fetched_at)}</p>\n'
        "</header>\n"
        f"{items_html}"
    )
    return _page(f"{platform}热点 · {date}", body)


def _badge(sentiment: str) -> str:
    css = {"正面": "pos", "负面": "neg", "中性": "neutral", "两极": "mixed"}.get(
        sentiment, "neutral"
    )
    return f'<span class="badge {css}">{_escape(sentiment)}</span>'


def _clip(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _question_links(question_ids: Any, by_id: dict[str, dict]) -> str:
    if not isinstance(question_ids, list):
        return ""
    links = "".join(
        f'<a href="{_escape(str(by_id[qid].get("url") or ""), quote=True)}">'
        f'{_escape(_clip(str(by_id[qid].get("title") or "")))}</a>'
        for qid in map(str, question_ids)
        if qid in by_id
    )
    return f'<p class="question-links">{links}</p>' if links else ""


def _render_report(report: dict) -> str:
    date = str(report.get("date") or "")
    overall = report.get("overall") or {}
    coverage = report.get("coverage") or {}
    questions = [q for q in report.get("questions") or [] if isinstance(q, dict)]
    by_id = {str(q.get("id")): q for q in questions}

    counts = overall.get("sentiment_counts") or {}
    counts_text = " · ".join(f"{key} {value}" for key, value in counts.items()) or "无"
    meta = (
        f"基于 {coverage.get('questions_analyzed', 0)}/{coverage.get('questions', 0)} 个问题、"
        f"{coverage.get('answers_analyzed', 0)} 条回答摘要 · 模型 {report.get('model', '')}"
    )

    theme_items = []
    for theme in report.get("themes") or []:
        if not isinstance(theme, dict):
            continue
        theme_items.append(
            "<li>"
            f'{_badge(str(theme.get("sentiment") or ""))}'
            f'<strong>{_escape(str(theme.get("topic") or ""))}</strong>'
            f'<p>{_escape(str(theme.get("summary") or ""))}</p>'
            f'{_question_links(theme.get("question_ids"), by_id)}'
            "</li>"
        )
    themes_html = (
        '<section><h2>议题</h2><ul class="themes">'
        + "".join(theme_items)
        + "</ul></section>"
        if theme_items
        else ""
    )

    controversy_items = []
    for item in report.get("controversies") or []:
        if not isinstance(item, dict):
            continue
        controversy_items.append(
            "<li>"
            f'<strong>{_escape(str(item.get("title") or ""))}</strong>'
            f'<p>{_escape(str(item.get("summary") or ""))}</p>'
            f'{_question_links(item.get("question_ids"), by_id)}'
            "</li>"
        )
    controversies_html = (
        '<section><h2>争议点</h2><ul class="themes">'
        + "".join(controversy_items)
        + "</ul></section>"
        if controversy_items
        else ""
    )

    question_items = []
    for question in questions:
        viewpoints = "".join(
            f"<li>{_escape(str(point))}</li>" for point in question.get("key_viewpoints") or []
        )
        viewpoints_html = f'<ul class="viewpoints">{viewpoints}</ul>' if viewpoints else ""
        controversy = question.get("controversy")
        controversy_html = (
            f'<p class="excerpt">争议：{_escape(str(controversy))}</p>' if controversy else ""
        )
        topics = question.get("topics") or []
        topics_html = (
            f'<p class="meta">议题：{_escape(" / ".join(str(t) for t in topics))}</p>'
            if topics
            else ""
        )
        question_items.append(
            '<li class="report-item">'
            f'<div>{_badge(str(question.get("sentiment") or ""))}'
            f'<a class="title" href="{_escape(str(question.get("url") or ""), quote=True)}" '
            f'rel="noopener noreferrer" target="_blank">'
            f'{_escape(str(question.get("title") or ""))}</a></div>'
            f'<p class="meta">{_escape(str(question.get("tone") or ""))}'
            f' · 回答摘要 {question.get("answers_analyzed", 0)} 条</p>'
            f'<p>{_escape(str(question.get("summary") or ""))}</p>'
            f"{viewpoints_html}{controversy_html}{topics_html}"
            "</li>"
        )

    body = (
        "<header>\n"
        '<a class="back" href="index.html">← 全部快照</a>\n'
        f"<h1>知乎情绪日报 · {_escape(date)}</h1>\n"
        f'<p class="meta">{_escape(meta)}</p>\n'
        "</header>\n"
        '<section class="overall">\n'
        "<h2>整体基调</h2>\n"
        f'<p class="tone">{_escape(str(overall.get("tone") or ""))}</p>\n'
        f"<p>{_escape(str(overall.get('summary') or ''))}</p>\n"
        f'<p class="meta">情绪分布（{_escape(str(overall.get("method") or ""))}）：'
        f"{_escape(counts_text)}</p>\n"
        "</section>\n"
        f"{themes_html}\n{controversies_html}\n"
        "<section><h2>逐题分析</h2>\n"
        f'<ol class="report-items">{"".join(question_items)}</ol>\n'
        "</section>"
    )
    return _page(f"知乎情绪日报 · {date}", body)


def _load_reports(data_dir: Path) -> list[dict]:
    reports: list[dict] = []
    for path in data_dir.glob("*/*/report.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        data.setdefault("platform", path.parent.parent.name)
        data.setdefault("date", path.parent.name)
        reports.append(data)
    reports.sort(
        key=lambda report: (str(report.get("date")), str(report.get("platform"))),
        reverse=True,
    )
    return reports


def _render_index(
    entries: list[tuple[str, Snapshot, str]],
    report_links: dict[tuple[str, str], str],
) -> str:
    title = "hotrobin 热点快照"
    if not entries:
        body = (
            f"<h1>{title}</h1>\n"
            '<p class="empty">还没有数据。把快照放到 '
            "<code>data/&lt;platform&gt;/&lt;date&gt;/snapshot.json</code> "
            "后运行 <code>hotrobin build</code>。</p>"
        )
        return _page(title, body)

    rows = "\n".join(
        f'<li><span class="date">{_escape(date)}</span>'
        f'<a href="{_escape(filename, quote=True)}">{_escape(_platform_name(snapshot.platform))}</a>'
        + (
            f'<a class="report-link" href="{_escape(report_links[(date, snapshot.platform)], quote=True)}">'
            "情绪日报</a>"
            if (date, snapshot.platform) in report_links
            else ""
        )
        + f'<span class="count">{len(snapshot.items)} 条</span></li>'
        for date, snapshot, filename in entries
    )
    body = (
        f"<h1>{title}</h1>\n"
        f'<p class="meta">共 {len(entries)} 份快照</p>\n'
        f'<ul class="snapshots">\n{rows}\n</ul>'
    )
    return _page(title, body)


def _load_snapshots(data_dir: Path) -> list[tuple[str, Snapshot]]:
    if not data_dir.is_dir():
        raise SystemExit(f"数据目录不存在：{data_dir}")

    snapshots: list[tuple[str, Snapshot]] = []
    for path in data_dir.glob("*/*/snapshot.json"):
        try:
            snapshot = load_snapshot(path)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"无法读取 {path}：{exc}") from exc
        snapshots.append((snapshot.date, snapshot))

    snapshots.sort(key=lambda pair: (pair[0], pair[1].platform), reverse=True)
    return snapshots


def build_site(data_dir: Path, out_dir: Path) -> int:
    """Render all snapshots under ``data_dir`` into ``out_dir``.

    Returns the number of snapshot pages written.
    """
    snapshots = _load_snapshots(data_dir)
    reports = _load_reports(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.html"):
        stale.unlink()

    entries: list[tuple[str, Snapshot, str]] = []
    for date, snapshot in snapshots:
        filename = f"{date}-{_slug(snapshot.platform)}.html"
        (out_dir / filename).write_text(_render_day(date, snapshot), encoding="utf-8")
        entries.append((date, snapshot, filename))

    report_links: dict[tuple[str, str], str] = {}
    for report in reports:
        date = str(report.get("date") or "")
        platform = str(report.get("platform") or "")
        filename = f"{date}-{_slug(platform)}-report.html"
        (out_dir / filename).write_text(_render_report(report), encoding="utf-8")
        report_links[(date, platform)] = filename

    (out_dir / "style.css").write_text(STYLE, encoding="utf-8")
    (out_dir / "index.html").write_text(
        _render_index(entries, report_links), encoding="utf-8"
    )
    return len(entries)
