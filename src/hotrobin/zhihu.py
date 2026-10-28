"""Fetch Zhihu's anonymous hot list into a Snapshot."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import HotItem, Snapshot

HOT_LIST_URL = "https://api.zhihu.com/topstory/hot-list?limit=50&reverse_order=0"
SHANGHAI_TZ = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.zhihu.com/hot",
    "Accept": "application/json, text/plain, */*",
}
HEAT_RE = re.compile(r"([\d.]+)\s*(万|亿)?\s*热度")


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"知乎接口返回 HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"请求知乎失败: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"知乎接口返回的不是合法 JSON: {exc}") from exc


def parse_hot_score(text: str | None) -> int | None:
    """Parse ``"2584 万热度"`` into ``25_840_000``."""
    if not text:
        return None
    match = HEAT_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "万":
        value *= 10_000
    elif unit == "亿":
        value *= 100_000_000
    return int(value)


def _item_from_target(rank: int, raw: dict[str, Any]) -> HotItem | None:
    target = raw.get("target") or {}
    item_id = target.get("id")
    title = target.get("title")
    if item_id is None or not title:
        return None

    if target.get("type") == "question":
        url = f"https://www.zhihu.com/question/{item_id}"
    else:
        url = str(target.get("url") or "")

    answer_count = target.get("answer_count")
    excerpt = target.get("excerpt")
    if excerpt is not None:
        excerpt = str(excerpt).strip() or None

    return HotItem(
        rank=rank,
        id=str(item_id),
        title=str(title).strip(),
        url=url,
        hot_score=parse_hot_score(raw.get("detail_text")),
        answer_count=int(answer_count) if answer_count is not None else None,
        excerpt=excerpt,
    )


def fetch_snapshot() -> Snapshot:
    """Fetch the current anonymous Zhihu hot list."""
    payload = _fetch_json(HOT_LIST_URL)
    items: list[HotItem] = []
    for raw in payload.get("data") or []:
        item = _item_from_target(len(items) + 1, raw)
        if item is not None:
            items.append(item)

    if not items:
        raise SystemExit("知乎热榜返回为空")

    return Snapshot(
        platform="zhihu",
        fetched_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        items=items,
    )
