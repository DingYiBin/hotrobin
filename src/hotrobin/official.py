"""Client for Zhihu's official open-platform API (developer.zhihu.com).

Endpoints used:

- ``GET /api/v1/content/hot_list?Limit=30``
- ``GET /api/v1/content/question_answers?QuestionUrl=...&Limit=50``

Every request needs ``Authorization: Bearer <secret>`` plus a seconds-level
``X-Request-Timestamp`` header; responses use ``{Code, Message, Data}``.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    SHANGHAI_TZ,
    AnswerDigest,
    HotItem,
    QuestionAnswers,
    Snapshot,
    load_snapshot,
)

BASE_URL = "https://developer.zhihu.com"
QUESTION_RE = re.compile(r"/question/(\d+)")
ARTICLE_RE = re.compile(r"/p/(\d+)")
DEFAULT_ANSWER_LIMIT = 50
DEFAULT_MAX_QUESTIONS = 30
REQUEST_DELAY = 0.3


class ZhihuAPIError(RuntimeError):
    def __init__(self, code: int | str, message: str) -> None:
        super().__init__(f"知乎 API 错误 {code}: {message}")
        self.code = code


def _secret() -> str:
    secret = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
    if not secret:
        raise SystemExit("缺少 ZHIHU_ACCESS_SECRET，请在 .env 中配置")
    return secret


def _api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    last_error: Exception | None = None
    for attempt in range(3):
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
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                last_error = exc
                continue
            raise ZhihuAPIError(exc.code, raw) from exc
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                last_error = exc
                continue
            raise SystemExit(f"请求知乎 API 失败：{exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ZhihuAPIError("invalid-json", "响应不是合法 JSON") from exc

        code = body.get("Code")
        if code == 0:
            data = body.get("Data")
            return data if isinstance(data, dict) else {}

        message = str(body.get("Message") or "")
        if code == 30001 and attempt < 2:
            time.sleep(3 * (attempt + 1))
            last_error = ZhihuAPIError(code, message)
            continue
        raise ZhihuAPIError(code, message)

    raise ZhihuAPIError("retry-exhausted", str(last_error))


def _clean_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _content_id(url: str) -> str:
    match = QUESTION_RE.search(url) or ARTICLE_RE.search(url)
    return match.group(1) if match else url


def fetch_hot_list(limit: int = 30) -> Snapshot:
    """Fetch the current hot list and normalize it into a Snapshot."""
    data = _api_get("/api/v1/content/hot_list", {"Limit": limit})
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


def fetch_question_answers(question_url: str, limit: int = DEFAULT_ANSWER_LIMIT) -> list[AnswerDigest]:
    """Fetch one page of answer summaries for a question (max 50)."""
    data = _api_get(
        "/api/v1/content/question_answers",
        {"QuestionUrl": question_url, "Offset": 0, "Limit": limit},
    )

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
    return answers


def collect_answers(
    data_dir: Path,
    *,
    date: str,
    limit: int = DEFAULT_ANSWER_LIMIT,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    force: bool = False,
) -> tuple[int, int, int]:
    """Collect answer summaries for today's hot list questions.

    Returns ``(fetched, skipped, failed)``.
    """
    snapshot_path = data_dir / "zhihu" / date / "snapshot.json"
    if not snapshot_path.exists():
        raise SystemExit(f"找不到 {snapshot_path}，先运行 hotrobin fetch")

    snapshot = load_snapshot(snapshot_path)
    questions = [item for item in snapshot.items if QUESTION_RE.search(item.url)]
    questions = questions[:max_questions]
    if not questions:
        raise SystemExit("今日热榜没有可抓取的问题")

    answer_dir = snapshot_path.parent / "answers"
    answer_dir.mkdir(parents=True, exist_ok=True)

    fetched = skipped = failed = 0
    for index, item in enumerate(questions, start=1):
        path = answer_dir / f"{item.id}.json"
        if path.exists() and not force:
            skipped += 1
            continue

        try:
            answers = fetch_question_answers(item.url, limit=limit)
        except ZhihuAPIError as exc:
            if exc.code in (20001, 30001, 30002):
                raise SystemExit(str(exc)) from exc
            print(f"  [{index}/{len(questions)}] 抓取失败 {item.id}: {exc}")
            failed += 1
            continue

        payload = QuestionAnswers(
            question_url=item.url,
            fetched_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
            answers=answers,
        )
        path.write_text(
            json.dumps(payload.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        fetched += 1
        print(f"  [{index}/{len(questions)}] {item.id}: {len(answers)} 条摘要")
        time.sleep(REQUEST_DELAY)

    return fetched, skipped, failed
