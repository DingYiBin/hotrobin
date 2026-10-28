"""Render snapshot JSON files into a static HTML site."""

from __future__ import annotations

import html
import re
from pathlib import Path

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


def _render_index(entries: list[tuple[str, Snapshot, str]]) -> str:
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
        f'<span class="count">{len(snapshot.items)} 条</span></li>'
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
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.html"):
        stale.unlink()

    entries: list[tuple[str, Snapshot, str]] = []
    for date, snapshot in snapshots:
        filename = f"{date}-{_slug(snapshot.platform)}.html"
        (out_dir / filename).write_text(_render_day(date, snapshot), encoding="utf-8")
        entries.append((date, snapshot, filename))

    (out_dir / "style.css").write_text(STYLE, encoding="utf-8")
    (out_dir / "index.html").write_text(_render_index(entries), encoding="utf-8")
    return len(entries)
