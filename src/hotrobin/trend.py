"""Render the sentiment trend page as static inline SVG.

Each report is one point on a real-time axis; multiple runs on the same day
therefore appear as multiple points spaced by their actual timestamps.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any

from .models import SHANGHAI_TZ

SENTIMENTS = ("正面", "负面", "中性", "两极")
COLORS = {
    "正面": "#16a34a",
    "负面": "#dc2626",
    "中性": "#6b7280",
    "两极": "#d97706",
}

MAX_GAP = timedelta(hours=36)
CHART_WIDTH = 900
CHART_HEIGHT = 320
AREA_HEIGHT = 260
MARGIN_LEFT = 52
MARGIN_RIGHT = 20
MARGIN_TOP = 16
MARGIN_BOTTOM = 34
PLOT_WIDTH = CHART_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
POINT_TOOLTIP_LIMIT = 400
TABLE_LIMIT = 500


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _parse_time(report: dict[str, Any]) -> datetime | None:
    raw = report.get("run_at") or report.get("generated_at") or report.get("date")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def _ratios(report: dict[str, Any]) -> dict[str, float] | None:
    overall = report.get("overall") or {}
    raw_ratios = overall.get("sentiment_ratios") or {}
    values: dict[str, float] = {}

    if raw_ratios:
        for sentiment in SENTIMENTS:
            try:
                values[sentiment] = float(raw_ratios.get(sentiment) or 0)
            except (TypeError, ValueError):
                values[sentiment] = 0.0
    else:
        counts = overall.get("sentiment_counts") or {}
        total = sum(float(counts.get(sentiment) or 0) for sentiment in SENTIMENTS)
        if total <= 0:
            return None
        for sentiment in SENTIMENTS:
            values[sentiment] = float(counts.get(sentiment) or 0) / total

    total = sum(values.values())
    if total <= 0:
        return None
    return {sentiment: value / total for sentiment, value in values.items()}


def _points(reports: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    points = []
    for _run_name, report in reports:
        if str(report.get("platform") or "zhihu") != "zhihu":
            continue
        when = _parse_time(report)
        ratios = _ratios(report)
        if when is None or ratios is None:
            continue
        points.append({"dt": when, "ratios": ratios})
    points.sort(key=lambda point: point["dt"])
    return points


def _bounds(points: list[dict[str, Any]]) -> tuple[datetime, datetime]:
    start = points[0]["dt"]
    end = points[-1]["dt"]
    if end - start < timedelta(hours=1):
        start -= timedelta(hours=12)
        end += timedelta(hours=12)
    return start, end


def _add_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(index, 12)
    return value.replace(
        year=year,
        month=month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _time_ticks(start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    span = end - start
    ticks: list[tuple[datetime, str]] = []

    if span <= timedelta(days=2):
        step = timedelta(hours=6)
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current < start:
            current += step
        while current <= end:
            ticks.append((current, current.strftime("%m-%d %H:%M")))
            current += step
    elif span <= timedelta(days=60):
        step = timedelta(days=7)
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        current -= timedelta(days=current.weekday())
        while current < start:
            current += step
        while current <= end:
            ticks.append((current, current.strftime("%m-%d")))
            current += step
    elif span <= timedelta(days=800):
        current = _add_months(start, 0)
        while current < start:
            current = _add_months(current, 1)
        while current <= end:
            ticks.append((current, current.strftime("%Y-%m")))
            current = _add_months(current, 1)
    elif span <= timedelta(days=4000):
        current = _add_months(start, 0)
        current = current.replace(month=((current.month - 1) // 3) * 3 + 1)
        while current < start:
            current = _add_months(current, 3)
        while current <= end:
            ticks.append((current, current.strftime("%Y-%m")))
            current = _add_months(current, 3)
    else:
        current = start.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        while current < start:
            current = current.replace(year=current.year + 1)
        while current <= end:
            ticks.append((current, current.strftime("%Y")))
            current = current.replace(year=current.year + 1)

    if len(ticks) < 2:
        ticks = [
            (start, start.strftime("%Y-%m-%d")),
            (end, end.strftime("%Y-%m-%d")),
        ]
    return ticks


def _x(value: datetime, start: datetime, end: datetime) -> float:
    total = (end - start).total_seconds() or 1
    return MARGIN_LEFT + (value - start).total_seconds() / total * PLOT_WIDTH


def _y(value: float, height: float) -> float:
    return MARGIN_TOP + (1 - value) * height


def _segments(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: datetime | None = None
    for point in points:
        if previous is not None and point["dt"] - previous > MAX_GAP:
            if current:
                segments.append(current)
            current = []
        current.append(point)
        previous = point["dt"]
    if current:
        segments.append(current)
    return segments


def _axes_svg(start: datetime, end: datetime, height: int) -> str:
    plot_height = height - MARGIN_TOP - MARGIN_BOTTOM
    parts = []
    for percent in (0, 25, 50, 75, 100):
        y = _y(percent / 100, plot_height)
        parts.append(
            f'<line x1="{MARGIN_LEFT}" y1="{y:.1f}" '
            f'x2="{MARGIN_LEFT + PLOT_WIDTH}" y2="{y:.1f}" '
            'stroke="currentColor" stroke-opacity="0.12"/>'
        )
        parts.append(
            f'<text x="{MARGIN_LEFT - 8}" y="{y + 4:.1f}" '
            f'text-anchor="end" font-size="11">{percent}%</text>'
        )
    for tick, label in _time_ticks(start, end):
        x = _x(tick, start, end)
        parts.append(
            f'<line x1="{x:.1f}" y1="{MARGIN_TOP}" '
            f'x2="{x:.1f}" y2="{MARGIN_TOP + plot_height}" '
            'stroke="currentColor" stroke-opacity="0.06"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{height - 10}" text-anchor="middle" '
            f'font-size="11">{_escape(label)}</text>'
        )
    return "".join(parts)


def _line_chart(
    points: list[dict[str, Any]],
    segments: list[list[dict[str, Any]]],
    start: datetime,
    end: datetime,
) -> str:
    plot_height = CHART_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    show_tooltips = len(points) <= POINT_TOOLTIP_LIMIT
    parts = []

    for sentiment in SENTIMENTS:
        color = COLORS[sentiment]
        for segment in segments:
            if len(segment) > 1:
                coords = " ".join(
                    f"{_x(point['dt'], start, end):.1f},"
                    f"{_y(point['ratios'][sentiment], plot_height):.1f}"
                    for point in segment
                )
                parts.append(
                    f'<polyline points="{coords}" fill="none" stroke="{color}" '
                    'stroke-width="2" stroke-linejoin="round" '
                    'stroke-linecap="round"/>'
                )
            elif not show_tooltips:
                point = segment[0]
                parts.append(
                    f'<circle cx="{_x(point["dt"], start, end):.1f}" '
                    f'cy="{_y(point["ratios"][sentiment], plot_height):.1f}" '
                    f'r="2.5" fill="{color}"/>'
                )
        if show_tooltips:
            for point in points:
                title = (
                    f"{point['dt'].strftime('%Y-%m-%d %H:%M')} "
                    f"{sentiment} {point['ratios'][sentiment] * 100:.1f}%"
                )
                parts.append(
                    f'<circle cx="{_x(point["dt"], start, end):.1f}" '
                    f'cy="{_y(point["ratios"][sentiment], plot_height):.1f}" '
                    f'r="2.5" fill="{color}">'
                    f"<title>{_escape(title)}</title></circle>"
                )

    return (
        f'<svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        'role="img" aria-label="四种情绪占比趋势折线图" class="trend-chart">'
        + _axes_svg(start, end, CHART_HEIGHT)
        + "".join(parts)
        + "</svg>"
    )


def _area_chart(
    segments: list[list[dict[str, Any]]],
    start: datetime,
    end: datetime,
) -> str:
    plot_height = AREA_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    parts = []

    for segment in segments:
        if len(segment) < 2:
            continue
        cumulative = [0.0] * len(segment)
        for sentiment in SENTIMENTS:
            lower = list(cumulative)
            upper = [
                lower[index] + segment[index]["ratios"][sentiment]
                for index in range(len(segment))
            ]
            top_points = " ".join(
                f"{_x(point['dt'], start, end):.1f},"
                f"{_y(upper[index], plot_height):.1f}"
                for index, point in enumerate(segment)
            )
            bottom_points = " ".join(
                f"{_x(segment[index]['dt'], start, end):.1f},"
                f"{_y(lower[index], plot_height):.1f}"
                for index in range(len(segment) - 1, -1, -1)
            )
            parts.append(
                f'<polygon points="{top_points} {bottom_points}" '
                f'fill="{COLORS[sentiment]}" fill-opacity="0.75"/>'
            )
            cumulative = upper

    return (
        f'<svg viewBox="0 0 {CHART_WIDTH} {AREA_HEIGHT}" '
        'role="img" aria-label="四种情绪构成堆叠面积图" class="trend-chart">'
        + _axes_svg(start, end, AREA_HEIGHT)
        + "".join(parts)
        + "</svg>"
    )


def _legend() -> str:
    items = "".join(
        f'<span class="legend-item"><i style="background:{COLORS[sentiment]}"></i>'
        f"{_escape(sentiment)}</span>"
        for sentiment in SENTIMENTS
    )
    return f'<div class="legend">{items}</div>'


def _table(points: list[dict[str, Any]]) -> str:
    rows = []
    shown = points[-TABLE_LIMIT:]
    for point in reversed(shown):
        values = "".join(
            f"<td>{point['ratios'][sentiment] * 100:.1f}%</td>"
            for sentiment in SENTIMENTS
        )
        rows.append(
            f"<tr><td>{point['dt'].strftime('%Y-%m-%d %H:%M')}</td>{values}</tr>"
        )
    header = "".join(f"<th>{_escape(sentiment)}</th>" for sentiment in SENTIMENTS)
    note = (
        f"<p class=\"meta\">仅显示最近 {TABLE_LIMIT} 轮（共 {len(points)} 轮）</p>"
        if len(points) > TABLE_LIMIT
        else ""
    )
    return (
        f"<details><summary>逐轮数据（{len(points)} 轮）</summary>{note}"
        f"<table><thead><tr><th>时间</th>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></details>"
    )


def render_trend_body(reports: list[tuple[str, dict[str, Any]]]) -> str | None:
    """Render the trend page body, or ``None`` if there is usable data."""
    points = _points(reports)
    if not points:
        return None

    start, end = _bounds(points)
    segments = _segments(points)
    line = _line_chart(points, segments, start, end)
    area = _area_chart(segments, start, end) if len(points) >= 2 else ""

    body = (
        "<header>\n"
        '<a class="back" href="../index.html">← 全部快照</a>\n'
        "<h1>知乎情绪趋势</h1>\n"
        f'<p class="meta">共 {len(points)} 轮采集 · '
        f"{start.strftime('%Y-%m-%d %H:%M')} 至 {end.strftime('%Y-%m-%d %H:%M')} · "
        "缺口超过 36 小时断开</p>\n"
        "</header>\n"
        f"{_legend()}\n"
        '<section><h2>情绪占比趋势</h2>\n'
        f"{line}\n"
        "</section>\n"
    )
    if area:
        body += f'<section><h2>情绪构成</h2>\n{area}\n</section>\n'
    body += f'<section><h2>数据</h2>\n{_table(points)}\n</section>'
    return body
