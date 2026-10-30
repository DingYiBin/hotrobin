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
.topic-list li, .analysis {
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
.analysis .title { font-weight: 600; }
.analysis ul { margin: 0.4rem 0 0; padding-left: 1.1rem; }
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


def _render_table(items: list[dict]) -> str:
    rows = []
    for index, entry in enumerate(items, start=1):
        score = float(entry.get("score") or 0)
        rows.append(
            "<tr>"
            f'<td class="num">{index}</td>'
            f'<td><a href="{_escape(entry.get("url") or "", quote=True)}" '
            'target="_blank" rel="noopener noreferrer">'
            f'{_escape(entry.get("title") or "")}</a></td>'
            f'<td class="num">{entry.get("appearances", 0)}</td>'
            f'<td class="num">{entry.get("best_rank", "")}</td>'
            f'<td class="num">{score:.2f}</td>'
            f'<td>{_status_tag(str(entry.get("status") or ""))}</td>'
            f'<td>{_sparkline(entry.get("rank_history") or [])}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th class=\"num\">#</th><th>标题</th>"
        "<th class=\"num\">上榜</th><th class=\"num\">最好</th>"
        "<th class=\"num\">分数</th><th>状态</th><th>走势</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _render_analyses(items: list[dict]) -> str:
    cards = []
    for entry in items:
        analysis = entry.get("analysis")
        if not isinstance(analysis, dict):
            continue
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
        cards.append(
            '<article class="analysis">'
            f'<div>{_status_tag(str(entry.get("status") or ""))}'
            f'<a class="title" href="{_escape(entry.get("url") or "", quote=True)}" '
            'target="_blank" rel="noopener noreferrer">'
            f'{_escape(entry.get("title") or "")}</a></div>'
            f'<p>{_escape(analysis.get("summary") or "")}</p>'
            f"{points_html}{controversy_html}{topics_html}{why_html}"
            "</article>"
        )
    return "".join(cards) if cards else '<p class="empty">无</p>'


def _render_movers(items: list[dict]) -> str:
    if not items:
        return '<p class="empty">无</p>'
    rows = []
    for entry in items:
        score_delta = float(entry.get("score_delta") or 0)
        rank_delta = int(entry.get("rank_delta") or 0)
        meta = (
            f'分数 {float(entry.get("prev_score") or 0):.2f} → '
            f'{float(entry.get("score") or 0):.2f} ({score_delta:+.2f}) · '
            f'最好名次 {entry.get("prev_best_rank", "?")} → '
            f'{entry.get("best_rank", "?")} ({rank_delta:+d})'
        )
        rows.append(
            "<li>"
            f'<a href="{_escape(entry.get("url") or "", quote=True)}" '
            'target="_blank" rel="noopener noreferrer">'
            f'{_escape(entry.get("title") or "")}</a>'
            f'<p class="meta">{_escape(meta)}</p>'
            "</li>"
        )
    return f'<ul class="topic-list">{"".join(rows)}</ul>'


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
        + "<h3>上升</h3>"
        + _render_movers(dod.get("risers") or [])
        + "<h3>下降</h3>"
        + _render_movers(dod.get("fallers") or [])
        + "\n</section>\n"
    )


def _render_day(day: str, platform: str, report: dict, top: dict) -> str:
    coverage = report.get("coverage") or {}
    window = report.get("window") or {}
    name = _platform_name(platform)
    items = [entry for entry in top.get("items") or [] if isinstance(entry, dict)]
    analyses = [entry for entry in items if isinstance(entry.get("analysis"), dict)]

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
        '<section><h2>新增热点</h2>'
        f'{_topic_list(report.get("new_topics") or [])}</section>\n'
        '<section><h2>持续热点</h2>'
        f'{_topic_list(report.get("continuing_topics") or [], show_prev=True)}</section>\n'
        '<section><h2>退榜热点</h2>'
        f'{_topic_list(report.get("retired_topics") or [])}</section>\n'
    )

    themes = report.get("themes") or []
    if themes:
        theme_rows = []
        links_by_id = {entry.get("id"): entry for entry in items}
        for theme in themes:
            links = " ".join(
                f'<a href="{_escape((links_by_id.get(qid) or {}).get("url") or "", quote=True)}">'
                f'{_escape(str((links_by_id.get(qid) or {}).get("title") or qid)[:40])}</a>'
                for qid in theme.get("question_ids") or []
                if qid in links_by_id
            )
            theme_rows.append(
                "<li>"
                f'<strong>{_escape(theme.get("topic") or "")}</strong>'
                f'<p>{_escape(theme.get("summary") or "")}</p>'
                f'<p class="meta">{links}</p>'
                "</li>"
            )
        body += (
            '<section><h2>议题</h2><ul class="topic-list">'
            + "".join(theme_rows)
            + "</ul></section>\n"
        )

    body += (
        '<section><h2>Top 100</h2>\n'
        f"{_render_table(items)}\n"
        "</section>\n"
        '<section><h2>Top 30 逐题分析</h2>\n'
        f"{_render_analyses(analyses)}\n"
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
