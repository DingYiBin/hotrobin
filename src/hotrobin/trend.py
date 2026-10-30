"""Render the hot-topic trend page as static inline SVG."""

from __future__ import annotations

import html
import math
from datetime import date, timedelta
from typing import Any

CHART_WIDTH = 900
CHART_HEIGHT = 300
MARGIN_LEFT = 52
MARGIN_RIGHT = 20
MARGIN_TOP = 16
MARGIN_BOTTOM = 34
PLOT_WIDTH = CHART_WIDTH - MARGIN_LEFT - MARGIN_RIGHT

SERIES = (
    ("new", "新增", "#16a34a"),
    ("continuing", "持续", "#2563eb"),
    ("retired", "退榜", "#dc2626"),
)


def _escape(value: Any, quote: bool = True) -> str:
    return html.escape(str(value), quote=quote)


def _counts(report: dict) -> dict[str, int]:
    counts = report.get("counts") or {}
    return {
        "new": int(counts.get("new") or len(report.get("new_topics") or [])),
        "continuing": int(
            counts.get("continuing") or len(report.get("continuing_topics") or [])
        ),
        "retired": int(counts.get("retired") or len(report.get("retired_topics") or [])),
    }


def _points(days: list[tuple[str, str, dict, dict]]) -> list[dict[str, Any]]:
    points = []
    for day, platform, report, _top in days:
        if platform != "zhihu":
            continue
        try:
            value = date.fromisoformat(day)
        except ValueError:
            continue
        counts = _counts(report)
        points.append({"date": value, "counts": counts})
    points.sort(key=lambda point: point["date"])
    return points


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def _time_ticks(start: date, end: date) -> list[tuple[date, str]]:
    span = end - start
    ticks: list[tuple[date, str]] = []
    if span <= timedelta(days=60):
        current = start
        while current <= end:
            ticks.append((current, current.strftime("%m-%d")))
            current += timedelta(days=7)
    elif span <= timedelta(days=800):
        current = start.replace(day=1)
        while current < start:
            current = _add_months(current, 1)
        while current <= end:
            ticks.append((current, current.strftime("%Y-%m")))
            current = _add_months(current, 1)
    elif span <= timedelta(days=4000):
        current = start.replace(day=1, month=((start.month - 1) // 3) * 3 + 1)
        while current < start:
            current = _add_months(current, 3)
        while current <= end:
            ticks.append((current, current.strftime("%Y-%m")))
            current = _add_months(current, 3)
    else:
        current = start.replace(month=1, day=1)
        while current < start:
            current = current.replace(year=current.year + 1)
        while current <= end:
            ticks.append((current, current.strftime("%Y")))
            current = current.replace(year=current.year + 1)

    if len(ticks) < 2:
        ticks = [(start, start.isoformat()), (end, end.isoformat())]
    return ticks


def _x(value: date, start: date, end: date) -> float:
    total = (end - start).days or 1
    return MARGIN_LEFT + (value - start).days / total * PLOT_WIDTH


def _y(value: float, y_max: float, plot_height: float) -> float:
    return MARGIN_TOP + (1 - value / y_max) * plot_height


def _segments(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    segments = []
    current = []
    previous = None
    for point in points:
        if previous is not None and (point["date"] - previous).days > 1:
            if current:
                segments.append(current)
            current = []
        current.append(point)
        previous = point["date"]
    if current:
        segments.append(current)
    return segments


def _axes_svg(start: date, end: date, y_max: int, step: int) -> str:
    plot_height = CHART_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    parts = []
    for index in range(5):
        value = index * step
        y = _y(value, y_max, plot_height)
        parts.append(
            f'<line x1="{MARGIN_LEFT}" y1="{y:.1f}" '
            f'x2="{MARGIN_LEFT + PLOT_WIDTH}" y2="{y:.1f}" '
            'stroke="currentColor" stroke-opacity="0.12"/>'
        )
        parts.append(
            f'<text x="{MARGIN_LEFT - 8}" y="{y + 4:.1f}" '
            f'text-anchor="end" font-size="11">{value}</text>'
        )
    for tick, label in _time_ticks(start, end):
        x = _x(tick, start, end)
        parts.append(
            f'<line x1="{x:.1f}" y1="{MARGIN_TOP}" x2="{x:.1f}" '
            f'y2="{MARGIN_TOP + plot_height}" stroke="currentColor" '
            'stroke-opacity="0.06"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{CHART_HEIGHT - 10}" text-anchor="middle" '
            f'font-size="11">{_escape(label)}</text>'
        )
    return "".join(parts)


def _chart(points: list[dict[str, Any]]) -> str:
    if not points:
        return '<p class="empty">还没有数据。</p>'

    start = points[0]["date"]
    end = points[-1]["date"]
    if start == end:
        start -= timedelta(days=1)
        end += timedelta(days=1)

    max_value = max(
        max(point["counts"][key] for key, _label, _color in SERIES)
        for point in points
    )
    step = max(1, math.ceil(max_value / 4))
    y_max = step * 4
    plot_height = CHART_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    segments = _segments(points)

    parts = []
    for key, label, color in SERIES:
        for segment in segments:
            if len(segment) > 1:
                coords = " ".join(
                    f"{_x(point['date'], start, end):.1f},"
                    f"{_y(point['counts'][key], y_max, plot_height):.1f}"
                    for point in segment
                )
                parts.append(
                    f'<polyline points="{coords}" fill="none" stroke="{color}" '
                    'stroke-width="2" stroke-linejoin="round" '
                    'stroke-linecap="round"/>'
                )
        for point in points:
            title = (
                f"{point['date'].isoformat()} {label} {point['counts'][key]}"
            )
            parts.append(
                f'<circle cx="{_x(point["date"], start, end):.1f}" '
                f'cy="{_y(point["counts"][key], y_max, plot_height):.1f}" '
                f'r="2.5" fill="{color}"><title>{_escape(title)}</title></circle>'
            )

    return (
        f'<svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        'role="img" aria-label="每日新增、持续、退榜热点数量" class="trend-chart">'
        + _axes_svg(start, end, y_max, step)
        + "".join(parts)
        + "</svg>"
    )


def _legend() -> str:
    items = "".join(
        f'<span class="legend-item"><i style="background:{color}"></i>'
        f"{_escape(label)}</span>"
        for _key, label, color in SERIES
    )
    return f'<div class="legend">{items}</div>'


def _persistence(days: list[tuple[str, str, dict, dict]]) -> str:
    topics: dict[str, dict[str, Any]] = {}
    for day, platform, _report, top in days:
        if platform != "zhihu":
            continue
        for entry in top.get("items") or []:
            if not isinstance(entry, dict):
                continue
            question_id = str(entry.get("id") or "")
            if not question_id:
                continue
            record = topics.get(question_id)
            if record is None:
                record = {
                    "id": question_id,
                    "title": str(entry.get("title") or ""),
                    "url": str(entry.get("url") or ""),
                    "days": set(),
                    "best_rank": int(entry.get("best_rank") or 999),
                    "appearances": 0,
                }
                topics[question_id] = record
            record["days"].add(day)
            record["best_rank"] = min(
                record["best_rank"], int(entry.get("best_rank") or 999)
            )
            record["appearances"] += int(entry.get("appearances") or 0)

    ranked = sorted(
        (record for record in topics.values() if len(record["days"]) > 1),
        key=lambda record: (
            -len(record["days"]),
            record["best_rank"],
            -record["appearances"],
        ),
    )[:20]

    if not ranked:
        return '<p class="empty">还没有跨天持续的话题。</p>'

    rows = []
    for record in ranked:
        rows.append(
            "<tr>"
            f'<td><a href="{_escape(record["url"], quote=True)}" '
            'target="_blank" rel="noopener noreferrer">'
            f'{_escape(record["title"])}</a></td>'
            f'<td class="num">{len(record["days"])}</td>'
            f'<td class="num">{record["best_rank"]}</td>'
            f'<td class="num">{record["appearances"]}</td>'
            "</tr>"
        )
    return (
        '<table><thead><tr><th>话题</th><th class="num">上榜天数</th>'
        '<th class="num">最好名次</th><th class="num">出现次数</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def render_trend_body(days: list[tuple[str, str, dict, dict]]) -> str | None:
    points = _points(days)
    if not points:
        return None

    body = (
        "<header>\n"
        '<a class="back" href="../index.html">← 全部日报</a>\n'
        "<h1>知乎热点趋势</h1>\n"
        f'<p class="meta">共 {len(points)} 个逻辑日 · '
        "新增/持续按 Top 100 统计，退榜按昨日全部题目统计</p>\n"
        "</header>\n"
        f"{_legend()}\n"
        '<section><h2>每日新增 / 持续 / 退榜</h2>\n'
        f"{_chart(points)}\n"
        "</section>\n"
        '<section><h2>跨天持续话题</h2>\n'
        f"{_persistence(days)}\n"
        "</section>"
    )
    return body
