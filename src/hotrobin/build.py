"""Render daily hot-topic reports into a static site."""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .trend import render_trend_body

PLATFORM_NAMES = {
    "zhihu": "知乎",
    "weibo": "微博",
}

PAGE_PREFIX = "../../../"
TREND_PREFIX = "../"
STATUS_LABELS = {"new": "新增", "continuing": "持续", "retired": "退榜"}
STATUS_ORDER = {"new": 0, "continuing": 1, "retired": 2}

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --card: #ffffff;
  --text: #1c1e21;
  --muted: #6b7280;
  --accent: #2563eb;
  --border: #e5e7eb;
  --green: #16a34a;
  --red: #dc2626;
  --blue: #2563eb;
  --orange: #d97706;
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
main { max-width: 860px; margin: 0 auto; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.5rem; margin: 0.5rem 0 1rem; }
h2 { font-size: 1.15rem; margin: 0 0 0.75rem; }
h3 { font-size: 1rem; margin: 1rem 0 0.4rem; }
header { margin-bottom: 1.5rem; }
.back { font-size: 0.9rem; }
.meta { color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0; }
.empty { color: var(--muted); }
section { margin-top: 2rem; }
.snapshots, .topic-list { list-style: none; padding: 0; margin: 0; }
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
.topic-list > li {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.6rem;
}
.topic-list a { font-weight: 600; }
.trend-link { margin: 0 0 0.75rem; }
.trend-link a { font-weight: 600; }
.tag {
  display: inline-block;
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  margin-right: 0.4rem;
}
.tag.new { background: rgba(22, 163, 74, 0.14); color: var(--green); }
.tag.continuing { background: rgba(37, 99, 235, 0.12); color: var(--blue); }
.tag.retired { background: rgba(220, 38, 38, 0.12); color: var(--red); }
.tag.up { background: rgba(22, 163, 74, 0.14); color: var(--green); }
.tag.down { background: rgba(220, 38, 38, 0.12); color: var(--red); }
.tag.flat { background: rgba(107, 114, 128, 0.18); color: var(--muted); }
.tag.rank { background: rgba(37, 99, 235, 0.10); color: var(--blue); }
.top-list { list-style: none; padding: 0; margin: 0; }
.top-item {
  display: flex;
  gap: 0.6rem;
  padding: 0.65rem 0.9rem;
  margin-bottom: 0.5rem;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.top-item .rank {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  min-width: 1.8rem;
  text-align: right;
}
.top-body { flex: 1; min-width: 0; }
.top-details > summary { list-style: none; cursor: pointer; }
.top-details > summary::-webkit-details-marker { display: none; }
.top-title { font-weight: 600; }
.top-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  align-items: center;
  color: var(--muted);
  font-size: 0.85rem;
  margin-top: 0.2rem;
}
.top-meta .tag { margin-right: 0; }
.top-details .q-analysis { margin-left: 0; }
.question-links { list-style: none; margin: 0.3rem 0 0; padding-left: 0; font-size: 0.9rem; }
.question-links li { margin: 0.25rem 0; }
.theme-question { display: flex; gap: 0.35rem; align-items: flex-start; }
.q-marker {
  flex: 0 0 1.15em;
  width: 1.15em;
  height: 1.15em;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 0.8em;
  line-height: 1;
  margin-top: 0.62rem;
  transition: transform 0.15s;
}
.q-box {
  flex: 1;
  min-width: 0;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.3rem 0.75rem;
}
.q-analysis { margin: 0.3rem 0 0 1.3rem; font-size: 0.9rem; }
.q-link { margin: 0 0 0.3rem; overflow-wrap: anywhere; }
.q-link a { font-weight: 600; }
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
th, td {
  padding: 0.4rem 0.45rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
th { text-align: left; color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.spark { color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 0.9rem; margin: 0.75rem 0 0.25rem; font-size: 0.9rem; }
.legend-item i {
  display: inline-block;
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 2px;
  margin-right: 0.3rem;
}
.trend-chart { width: 100%; height: auto; color: var(--text); }
.trend-chart text { fill: currentColor; }
details summary { cursor: pointer; color: var(--accent); }
code {
  background: var(--border);
  padding: 0.1rem 0.35rem;
  border-radius: 5px;
  font-size: 0.9em;
}
"""


def _escape(value: Any, quote: bool = True) -> str:
    return html.escape(str(value), quote=quote)


def _platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform)


def _slug(platform: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", platform.lower()).strip("-")
    return slug or "platform"


def _page(title: str, body: str, prefix: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def _time_label(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _load_days(data_dir: Path) -> list[tuple[str, str, dict, dict]]:
    days: list[tuple[str, str, dict, dict]] = []
    if not data_dir.is_dir():
        raise SystemExit(f"数据目录不存在：{data_dir}")

    for path in data_dir.glob("*/*/*/*/report.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("schema_version") != 2:
            continue
        platform = str(report.get("platform") or path.parents[3].name)
        day = str(report.get("date") or path.parent.name)
        top_path = path.parent / "top.json"
        top: dict = {"items": []}
        if top_path.exists():
            try:
                loaded = json.loads(top_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    top = loaded
            except (OSError, ValueError):
                pass
        days.append((day, platform, report, top))

    days.sort(key=lambda entry: entry[0], reverse=True)
    return days


def _status_tag(status: str) -> str:
    label = STATUS_LABELS.get(status, status)
    return f'<span class="tag {_escape(status, quote=True)}">{_escape(label)}</span>'


def _topic_list(items: list[dict], *, show_prev: bool = False) -> str:
    if not items:
        return '<p class="empty">无</p>'
    rows = []
    for entry in items:
        score = float(entry.get("score") or 0)
        meta = (
            f'上榜 {entry.get("appearances", 0)} 次 · '
            f'最好第 {entry.get("best_rank", "?")} 名 · 分数 {score:.2f}'
        )
        if show_prev and entry.get("prev_best_rank") is not None:
            meta += f' · 昨日最好第 {entry.get("prev_best_rank")} 名'
        rows.append(
            "<li>"
            f'<a href="{_escape(entry.get("url") or "", quote=True)}" '
            'target="_blank" rel="noopener noreferrer">'
            f'{_escape(entry.get("title") or "")}</a>'
            f'<p class="meta">{_escape(meta)}</p>'
            "</li>"
        )
    return f'<ul class="topic-list">{"".join(rows)}</ul>'


def _sparkline(history: list[dict]) -> str:
    points = [
        item for item in history if isinstance(item, dict) and item.get("rank") is not None
    ]
    if len(points) < 2:
        return ""
    width, height, pad = 96, 20, 2
    values = [int(point["rank"]) for point in points]
    low, high = min(values), max(values)
    span = max(1, high - low)
    coords = []
    for index, value in enumerate(values):
        x = pad + index / (len(values) - 1) * (width - pad * 2)
        y = pad + (value - low) / span * (height - pad * 2)
        coords.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<polyline points="{" ".join(coords)}" fill="none" stroke="currentColor" '
        'stroke-width="1.5" stroke-linejoin="round"/></svg>'
    )


def _analysis_body(analysis: dict) -> str:
    points = "".join(
        f"<li>{_escape(point)}</li>"
        for point in analysis.get("key_points") or []
    )
    points_html = f"<ul>{points}</ul>" if points else ""
    controversy = analysis.get("controversy")
    controversy_html = (
        f'<p class="meta">争议：{_escape(controversy)}</p>' if controversy else ""
    )
    topics = analysis.get("topics") or []
    topics_html = (
        f'<p class="meta">议题：{_escape(" / ".join(str(t) for t in topics))}</p>'
        if topics
        else ""
    )
    why = analysis.get("why_hot")
    why_html = f'<p class="meta">上榜原因：{_escape(why)}</p>' if why else ""
    return (
        f'<p>{_escape(analysis.get("summary") or "")}</p>'
        f"{points_html}{controversy_html}{topics_html}{why_html}"
    )


def _movement_tags(entry: dict) -> str:
    status = str(entry.get("status") or "")
    tags = []
    if status in ("new", "continuing"):
        tags.append(_status_tag(status))
    if status == "continuing":
        prev_score = entry.get("prev_score")
        if prev_score is not None:
            delta = float(entry.get("score") or 0) - float(prev_score)
            if delta > 0:
                tags.append(f'<span class="tag up">上升 {delta:+.2f}</span>')
            elif delta < 0:
                tags.append(f'<span class="tag down">下降 {delta:+.2f}</span>')
            else:
                tags.append('<span class="tag flat">持平</span>')
        prev_best = entry.get("prev_best_rank")
        if prev_best is not None:
            rank_delta = int(prev_best) - int(entry.get("best_rank") or 0)
            if rank_delta != 0:
                tags.append(f'<span class="tag rank">名次 {rank_delta:+d}</span>')
    return " ".join(tags)


def _render_top_list(items: list[dict]) -> str:
    rows = []
    for index, entry in enumerate(items, start=1):
        analysis = entry.get("analysis")
        title = _escape(entry.get("title") or "")
        url = _escape(entry.get("url") or "", quote=True)
        meta = (
            f'上榜 {entry.get("appearances", 0)} 次 · '
            f'最好第 {entry.get("best_rank", "?")} 名 · '
            f'分数 {float(entry.get("score") or 0):.2f}'
        )
        extra = (
            f"<span>{_escape(meta)}</span>"
            f"{_movement_tags(entry)}"
            f'{_sparkline(entry.get("rank_history") or [])}'
        )

        if isinstance(analysis, dict):
            url_text = _escape(entry.get("url") or "")
            link_line = (
                f'<p class="q-link"><a href="{url}" target="_blank" '
                f'rel="noopener noreferrer">{url_text}</a></p>'
            )
            body = (
                '<details class="top-details">'
                '<summary class="top-summary">'
                f'<span class="top-title">{title}</span>'
                f'<span class="top-meta">{extra}</span>'
                "</summary>"
                f'<div class="q-analysis">{link_line}{_analysis_body(analysis)}</div>'
                "</details>"
            )
        else:
            body = (
                f'<a class="top-title" href="{url}" target="_blank" '
                f'rel="noopener noreferrer">{title}</a>'
                f'<div class="top-meta">{extra}</div>'
            )

        rows.append(
            '<li class="top-item">'
            f'<span class="rank">{index}</span>'
            f'<div class="top-body">{body}</div>'
            "</li>"
        )
    return f'<ol class="top-list">{"".join(rows)}</ol>'


def _render_day_over_day(report: dict) -> str:
    dod = report.get("day_over_day") or {}
    if not isinstance(dod, dict):
        return ""

    summary = str(dod.get("summary") or "")
    meta_bits = []
    if dod.get("previous_date"):
        meta_bits.append(f"对比 {dod.get('previous_date')}")
    if dod.get("unique_delta") is not None:
        meta_bits.append(f"唯一题目较昨日 {int(dod.get('unique_delta')):+d}")
    meta = " · ".join(meta_bits)

    return (
        '<section><h2>较昨日变化</h2>\n'
        f"<p>{_escape(summary)}</p>\n"
        + (f'<p class="meta">{_escape(meta)}</p>\n' if meta else "")
        + "</section>\n"
    )


def _theme_questions(
    question_ids: list[str], by_id: dict[str, dict]
) -> str:
    rows = []
    for qid in question_ids or []:
        item = by_id.get(qid)
        if item is None:
            continue
        title = _escape(item.get("title") or qid)
        url = _escape(item.get("url") or "", quote=True)
        analysis = item.get("analysis")
        marker = (
            '<span class="q-marker">🔥</span>'
            if isinstance(analysis, dict)
            else '<span class="q-marker empty"></span>'
        )
        link = (
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f"{title}</a>"
        )
        rows.append(
            '<li class="theme-question">'
            f'{marker}<span class="q-box">{link}</span>'
            "</li>"
        )
    return f'<ul class="question-links">{"".join(rows)}</ul>' if rows else ""


def _render_day(day: str, platform: str, report: dict, top: dict) -> str:
    coverage = report.get("coverage") or {}
    window = report.get("window") or {}
    name = _platform_name(platform)
    items = [entry for entry in top.get("items") or [] if isinstance(entry, dict)]

    body = (
        "<header>\n"
        f'<a class="back" href="{PAGE_PREFIX}index.html">← 全部日报</a>\n'
        f"<h1>{_escape(name)}热点日报 · {_escape(day)}</h1>\n"
        f'<p class="meta">窗口 {_escape(_time_label(str(window.get("start") or "")))}'
        f' 至 {_escape(_time_label(str(window.get("end") or "")))} · '
        f'快照 {coverage.get("snapshots", 0)} 次 · '
        f'唯一题目 {coverage.get("unique_questions", 0)} · '
        f'分析 {coverage.get("analyzed_questions", 0)} 题 · '
        f'回答摘要 {coverage.get("answers_analyzed", 0)} 条</p>\n'
        "</header>\n"
        f'<section class="overall"><h2>今日概览</h2>'
        f'<p>{_escape(report.get("overview") or "")}</p></section>\n'
        f"{_render_day_over_day(report)}"
    )

    themes = report.get("themes") or []
    if themes:
        links_by_id = {entry.get("id"): entry for entry in items}
        theme_rows = []
        for theme in themes:
            questions = _theme_questions(
                theme.get("question_ids") or [], links_by_id
            )
            theme_rows.append(
                "<li>"
                f'<strong>{_escape(theme.get("topic") or "")}</strong>'
                f'<p>{_escape(theme.get("summary") or "")}</p>'
                f"{questions}"
                "</li>"
            )
        body += (
            '<section><h2>议题</h2><ul class="topic-list">'
            + "".join(theme_rows)
            + "</ul></section>\n"
        )

    body += (
        '<section><h2>Top 100</h2>\n'
        f"{_render_top_list(items)}\n"
        "</section>\n"
        '<section><h2>退榜热点</h2>\n'
        f'{_topic_list(report.get("retired_topics") or [])}\n'
        "</section>"
    )
    return _page(f"{name}热点日报 · {day}", body, PAGE_PREFIX)


def _render_index(
    days: list[tuple[str, str, dict, dict]],
    trend_links: dict[str, str],
) -> str:
    title = "hotrobin 每日热点"
    trend_html = ""
    zhihu_trend = trend_links.get("zhihu")
    if zhihu_trend:
        trend_html = (
            f'<p class="trend-link"><a href="{_escape(zhihu_trend, quote=True)}">'
            "查看热点趋势 →</a></p>\n"
        )

    if not days:
        body = (
            f"<h1>{title}</h1>\n"
            f"{trend_html}"
            '<p class="empty">还没有日报。等调度器跑完一个逻辑日后，'
            "运行 <code>hotrobin build</code>。</p>"
        )
        return _page(title, body)

    rows = []
    for day, platform, report, top in days:
        coverage = report.get("coverage") or {}
        link = f"{_slug(platform)}/{day[:4]}/{day[5:7]}/{day}.html"
        rows.append(
            f'<li><span class="date">{_escape(day)}</span>'
            f'<a href="{_escape(link, quote=True)}">{_escape(_platform_name(platform))}日报</a>'
            f'<span class="count">{coverage.get("unique_questions", 0)} 题 · '
            f'新增 {len(report.get("new_topics") or [])} · '
            f'退榜 {len(report.get("retired_topics") or [])}</span></li>'
        )
    body = (
        f"<h1>{title}</h1>\n"
        f"{trend_html}"
        f'<p class="meta">共 {len(days)} 份日报</p>\n'
        f'<ul class="snapshots">{"".join(rows)}</ul>'
    )
    return _page(title, body)


def build_site(data_dir: Path, out_dir: Path) -> int:
    """Render daily reports under ``data_dir`` into ``out_dir``."""
    days = _load_days(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in out_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        elif entry.suffix in (".html", ".css"):
            entry.unlink()

    for day, platform, report, top in days:
        slug = _slug(platform)
        target_dir = out_dir / slug / day[:4] / day[5:7]
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{day}.html").write_text(
            _render_day(day, platform, report, top), encoding="utf-8"
        )

    trend_links: dict[str, str] = {}
    if days:
        trend_body = render_trend_body(days)
        if trend_body is not None:
            trend_dir = out_dir / "zhihu"
            trend_dir.mkdir(parents=True, exist_ok=True)
            (trend_dir / "trend.html").write_text(
                _page("知乎热点趋势", trend_body, TREND_PREFIX), encoding="utf-8"
            )
            trend_links["zhihu"] = "zhihu/trend.html"

    (out_dir / "style.css").write_text(STYLE, encoding="utf-8")
    (out_dir / "index.html").write_text(
        _render_index(days, trend_links), encoding="utf-8"
    )
    return len(days)
