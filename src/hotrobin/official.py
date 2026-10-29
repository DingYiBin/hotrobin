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

from .failures import record_failure
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
DEFERRED_RETRY_ROUNDS = 3
DEFERRED_RETRY_DELAY = 3.0
FATAL_CODES = {20001, 30002, 30003}


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


def _classify_30001() -> tuple[str, int | None]:
    remaining = remaining_quota("question_discovery")
    if remaining is None:
        return "额度查询失败，无法区分每日额度与短时限流", None
    if remaining <= 0:
        return "当日回答额度已耗尽（剩余 0）", remaining
    return f"短时频率/并发限制（当日剩余 {remaining}）", remaining


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


def fetch_question_answers(
    question_url: str, limit: int = DEFAULT_ANSWER_LIMIT, *, attempts: int = 1
) -> list[AnswerDigest]:
    """Fetch one page of answer summaries for a question (max 50)."""
    data = _api_get(
        "/api/v1/content/question_answers",
        {"QuestionUrl": question_url, "Offset": 0, "Limit": limit},
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
    return answers


def _save_answers(answer_dir: Path, item: HotItem, answers: list[AnswerDigest]) -> None:
    payload = QuestionAnswers(
        question_url=item.url,
        fetched_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        answers=answers,
    )
    path = answer_dir / f"{item.id}.json"
    path.write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_pending_abort(
    run_dir: Path, pending: list[HotItem], code: int | str
) -> None:
    if not pending:
        return
    record_failure(
        run_dir,
        "answers",
        f"{len(pending)} 个问题中止时仍未完成重试",
        {"question_ids": [item.id for item in pending], "code": code},
    )


def collect_answers(
    run_dir: Path,
    *,
    limit: int = DEFAULT_ANSWER_LIMIT,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
) -> tuple[int, int, int]:
    """Collect answer summaries for one hot-list run.

    Returns ``(fetched, skipped, failed)``.
    """
    snapshot_path = run_dir / "snapshot.json"
    if not snapshot_path.exists():
        raise SystemExit(f"找不到 {snapshot_path}，先运行 hotrobin fetch")

    snapshot = load_snapshot(snapshot_path)
    questions = [item for item in snapshot.items if QUESTION_RE.search(item.url)]
    questions = questions[:max_questions]
    if not questions:
        raise SystemExit("本次热榜没有可抓取的问题")

    answer_dir = run_dir / "answers"
    answer_dir.mkdir(parents=True, exist_ok=True)

    skipped = 0
    pending_questions: list[HotItem] = []
    for item in questions:
        if (answer_dir / f"{item.id}.json").exists():
            skipped += 1
        else:
            pending_questions.append(item)

    if not pending_questions:
        return 0, skipped, 0

    remaining = remaining_quota("question_discovery")
    if remaining is not None:
        if remaining <= 0:
            message = "问题发现额度已用尽（RemainingQuota=0），跳过本轮回答抓取"
            record_failure(
                run_dir,
                "answers",
                message,
                {"api_id": "question_discovery", "remaining_quota": remaining},
            )
            print(f"  {message}")
            return 0, skipped, 0
        if remaining < len(pending_questions):
            print(
                f"  问题发现额度剩余 {remaining} 次，本轮只抓前 {remaining} 题"
            )
            pending_questions = pending_questions[:remaining]

    questions = pending_questions
    fetched = failed = 0
    pending: list[dict[str, Any]] = []

    for index, item in enumerate(questions, start=1):
        try:
            answers = fetch_question_answers(item.url, limit=limit, attempts=1)
        except ZhihuAPIError as exc:
            if exc.code == 30001:
                label, remaining = _classify_30001()
                message = f"{exc}（{label}）"
                record_failure(
                    run_dir,
                    "answers",
                    message,
                    {
                        "question_id": item.id,
                        "code": exc.code,
                        "remaining_quota": remaining,
                    },
                )
                _record_pending_abort(
                    run_dir, [entry["item"] for entry in pending], exc.code
                )
                raise SystemExit(message) from exc
            if exc.code in FATAL_CODES:
                record_failure(
                    run_dir,
                    "answers",
                    str(exc),
                    {"question_id": item.id, "code": exc.code},
                )
                _record_pending_abort(
                    run_dir, [entry["item"] for entry in pending], exc.code
                )
                raise SystemExit(str(exc)) from exc
            pending.append({"item": item, "code": exc.code, "message": str(exc)})
            print(
                f"  [{index}/{len(questions)}] {item.id}: {exc.code}，加入重试队列"
            )
            time.sleep(REQUEST_DELAY)
            continue
        except SystemExit as exc:
            record_failure(run_dir, "answers", str(exc), {"question_id": item.id})
            _record_pending_abort(
                run_dir, [entry["item"] for entry in pending], "system-exit"
            )
            raise

        _save_answers(answer_dir, item, answers)
        fetched += 1
        print(f"  [{index}/{len(questions)}] {item.id}: {len(answers)} 条摘要")
        time.sleep(REQUEST_DELAY)

    for round_index in range(1, DEFERRED_RETRY_ROUNDS + 1):
        if not pending:
            break
        delay = DEFERRED_RETRY_DELAY * round_index
        print(
            f"  第 {round_index}/{DEFERRED_RETRY_ROUNDS} 轮重试，"
            f"共 {len(pending)} 项，每次尝试前等待 {delay:g} 秒"
        )
        still_pending: list[dict[str, Any]] = []
        for position, entry in enumerate(pending):
            item = entry["item"]
            print(f"    等待 {delay:g} 秒后重试 {item.id}")
            time.sleep(delay)

            try:
                answers = fetch_question_answers(item.url, limit=limit, attempts=1)
            except ZhihuAPIError as exc:
                if exc.code == 30001:
                    label, remaining = _classify_30001()
                    message = f"{exc}（{label}）"
                    record_failure(
                        run_dir,
                        "answers",
                        message,
                        {
                            "question_id": item.id,
                            "code": exc.code,
                            "remaining_quota": remaining,
                            "round": round_index,
                        },
                    )
                    remaining_items = [
                        pending_item["item"]
                        for pending_item in pending[position + 1 :]
                    ] + [pending_item["item"] for pending_item in still_pending]
                    _record_pending_abort(run_dir, remaining_items, exc.code)
                    raise SystemExit(message) from exc
                if exc.code in FATAL_CODES:
                    record_failure(
                        run_dir,
                        "answers",
                        str(exc),
                        {
                            "question_id": item.id,
                            "code": exc.code,
                            "round": round_index,
                        },
                    )
                    remaining = [
                        pending_item["item"]
                        for pending_item in pending[position + 1 :]
                    ] + [pending_item["item"] for pending_item in still_pending]
                    _record_pending_abort(run_dir, remaining, exc.code)
                    raise SystemExit(str(exc)) from exc

                still_pending.append(
                    {"item": item, "code": exc.code, "message": str(exc)}
                )
                print(f"    仍失败 {item.id}: {exc}")
                continue
            except SystemExit as exc:
                record_failure(
                    run_dir,
                    "answers",
                    str(exc),
                    {"question_id": item.id, "round": round_index},
                )
                remaining = [
                    pending_item["item"] for pending_item in pending[position + 1 :]
                ] + [pending_item["item"] for pending_item in still_pending]
                _record_pending_abort(run_dir, remaining, "system-exit")
                raise

            _save_answers(answer_dir, item, answers)
            fetched += 1
            print(f"    重试成功 {item.id}: {len(answers)} 条摘要")

        pending = still_pending

    for entry in pending:
        record_failure(
            run_dir,
            "answers",
            entry["message"],
            {
                "question_id": entry["item"].id,
                "code": entry["code"],
                "rounds": DEFERRED_RETRY_ROUNDS,
            },
        )
        failed += 1
    if pending:
        print(f"  重试结束，仍有 {len(pending)} 项失败")

    return fetched, skipped, failed
