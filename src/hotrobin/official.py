"""Client for Zhihu's official open-platform API (developer.zhihu.com)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import SHANGHAI_TZ, AnswerDigest, HotItem, Snapshot

BASE_URL = "https://developer.zhihu.com"
QUESTION_RE = re.compile(r"/question/(\d+)")
ARTICLE_RE = re.compile(r"/p/(\d+)")
DEFAULT_ANSWER_LIMIT = 50


class ZhihuAPIError(RuntimeError):
    def __init__(self, code: int | str, message: str) -> None:
        super().__init__(f"知乎 API 错误 {code}: {message}")
        self.code = code


def _secret() -> str:
    secret = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
    if not secret:
        raise SystemExit("缺少 ZHIHU_ACCESS_SECRET，请在 .env 中配置")
    return secret


def _api_get(
    path: str, params: dict[str, Any] | None = None, *, attempts: int = 3
) -> Any:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    attempts = max(1, attempts)

    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + _secret(),
                "X-Request-Timestamp": str(int(time.time())),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()[:300].decode("utf-8", "replace")
            if exc.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise ZhihuAPIError(exc.code, raw) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            reason = getattr(exc, "reason", exc)
            raise ZhihuAPIError("network", f"网络请求失败：{reason}") from exc
        except json.JSONDecodeError as exc:
            raise ZhihuAPIError("invalid-json", "响应不是合法 JSON") from exc

        code = body.get("Code")
        if code == 0:
            return body.get("Data")

        raise ZhihuAPIError(code, str(body.get("Message") or ""))


def _clean_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _content_id(url: str) -> str:
    match = QUESTION_RE.search(url) or ARTICLE_RE.search(url)
    return match.group(1) if match else url


def fetch_quota(api_id: str) -> dict[str, Any] | None:
    """Fetch one quota row; returns ``None`` if it cannot be read."""
    try:
        data = _api_get("/api/v1/quota", {"APIIDs": api_id}, attempts=1)
    except (ZhihuAPIError, SystemExit):
        return None
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and str(item.get("APIID")) == api_id:
            return item
    return None


def remaining_quota(api_id: str) -> int | None:
    item = fetch_quota(api_id)
    if item is None:
        return None
    try:
        return int(item.get("RemainingQuota") or 0)
    except (TypeError, ValueError):
        return None


def fetch_hot_list(limit: int = 30) -> Snapshot:
    """Fetch the current hot list and normalize it into a Snapshot."""
    data = _api_get("/api/v1/content/hot_list", {"Limit": limit})
    if not isinstance(data, dict):
        raise ZhihuAPIError("invalid-data", "热榜返回结构异常")
    raw_items = data.get("Items") or []

    items: list[HotItem] = []
    for raw in raw_items:
        url = _clean_url(str(raw.get("Url") or ""))
        title = str(raw.get("Title") or "").strip()
        if not url or not title:
            continue
        items.append(
            HotItem(
                rank=len(items) + 1,
                id=_content_id(url),
                title=title,
                url=url,
                hot_score=None,
                answer_count=None,
                excerpt=str(raw.get("Summary") or "").strip() or None,
            )
        )

    if not items:
        raise SystemExit("官方热榜返回为空")

    return Snapshot(
        platform="zhihu",
        fetched_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        items=items,
    )


@dataclass(frozen=True)
class AnswerPage:
    answers: list[AnswerDigest]
    is_end: bool
    next_offset: int | None


def fetch_answer_page(
    question_url: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_ANSWER_LIMIT,
    attempts: int = 1,
) -> AnswerPage:
    """Fetch one page of answer summaries (max 50) with its paging state."""
    data = _api_get(
        "/api/v1/content/question_answers",
        {"QuestionUrl": question_url, "Offset": offset, "Limit": limit},
        attempts=attempts,
    )
    if not isinstance(data, dict):
        raise ZhihuAPIError("invalid-data", "回答接口返回结构异常")

    answers: list[AnswerDigest] = []
    for raw in data.get("Items") or []:
        summary = str(raw.get("Summary") or "").strip()
        url = str(raw.get("Url") or "").strip()
        if not summary or not url:
            continue
        answers.append(
            AnswerDigest(
                token=str(raw.get("ContentToken") or ""),
                url=url,
                summary=summary,
            )
        )

    paging = data.get("Paging") or {}
    next_raw = paging.get("NextOffset")
    try:
        next_offset = int(next_raw) if next_raw is not None else None
    except (TypeError, ValueError):
        next_offset = None
    return AnswerPage(
        answers=answers,
        is_end=bool(paging.get("IsEnd")),
        next_offset=next_offset,
    )
