"""Daily hot-topic aggregation, answer collection, and structure analysis.

A logical day starts at 04:00 Asia/Shanghai. Snapshots for the day live in
``<day>/snapshots/``; the summary writes ``top.json``, ``answers/``, and
``report.json`` in the same day directory.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .build import build_site
from .deepseek import chat_json, model_name
from .deploy import deploy_site
from .failures import load_failures, notify_failures, record_failure
from .models import (
    SHANGHAI_TZ,
    AnswerDigest,
    QuestionAnswers,
    Snapshot,
    day_directory,
    load_question_answers,
    load_snapshot,
    save_snapshot,
)
from .official import (
    DEFAULT_ANSWER_LIMIT,
    AnswerPage,
    ZhihuAPIError,
    fetch_answer_page,
    fetch_hot_list,
    remaining_quota,
)

TOP_LIMIT = 100
HEAT_SCORE_THRESHOLD = 1.0
MIN_ANALYZE = 30
MAX_ANALYZE = 50
DEEP_PAGES_LIMIT = 10
PAGE_LIMIT = DEFAULT_ANSWER_LIMIT
REQUEST_DELAY = 1.0
DEFERRED_RETRY_ROUNDS = 3
DEFERRED_RETRY_DELAY = 3.0
RATE_LIMIT_COOLDOWN = 60.0
FATAL_CODES = {20001, 30002, 30003}
SUMMARY_CHAR_LIMIT = 300
QUESTION_PROMPT_VERSION = 1

QUESTION_SYSTEM = """你是中文热点分析助手。输入是一个知乎热榜问题、当日榜单指标和高赞回答摘要。
只依据给定材料分析，不要补充外部事实，不要编造数据；材料不足时在 summary 中说明。
输出 JSON：
{"summary":"2-4句话概括讨论内容","key_points":["3-5条代表性观点"],"controversy":"主要分歧，没有则为null","topics":["1-3个议题标签"],"why_hot":"一句话说明为什么上榜"}"""

AGG_SYSTEM = """你是中文热点结构分析助手。输入是某个逻辑日（date）的知乎热榜 Top 100、高热题逐题分析，以及前一逻辑日（previous_day.date）的概览和议题总结。
只依据输入，不要编造。指代日期时使用输入中的具体日期，不要使用“今日/昨日/今天/昨天/当天/当日/本日”等相对表述。
请重点关注“热点结构”和“关注领域”，避免逐题流水账：
- 不要罗列单题名次升降或逐题清单；除非能说明领域或议题结构，否则不要单独点名题目。
- 用“注意力集中在 A 领域”“某类议题占比最高”“讨论焦点落在 X”这类高层概括。
- overview：4-5 句话（约 200-260 字），占主要篇幅，概括该日期热榜由哪些领域构成、讨论重心落在哪里、讨论结构有什么特点；不要逐题罗列。
- day_over_day：2-3 句话（约 80-120 字），只写相较前一逻辑日最显著的 1-3 个领域或议题迁移；用“较 <previous_day.date>……”开头；不要复述 overview，不要写单题排名、新增或退榜；没有 previous_day 时用一句话说明无法判断变化方向。
- themes：按上位领域或议题聚类，topic 用领域名而不是单个问题标题；summary 说明该领域在该日期为何受关注、讨论重心是什么；question_ids 只能使用输入中出现过的 id。
输出 JSON：
{"overview":"...","day_over_day":"...","themes":[{"topic":"领域或议题名","summary":"...","question_ids":["只能使用输入中出现过的 id"]}]}"""


def day_window(day: str, day_start_hour: int) -> tuple[str, str]:
    start = datetime.fromisoformat(day).replace(
        hour=day_start_hour, minute=0, second=0, microsecond=0, tzinfo=SHANGHAI_TZ
    )
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def load_day_snapshots(day_dir: Path) -> list[Snapshot]:
    snapshots = []
    for path in sorted((day_dir / "snapshots").glob("*.json")):
        try:
            snapshots.append(load_snapshot(path))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            print(f"  跳过无法读取的快照 {path}: {exc}")
    snapshots.sort(key=lambda snapshot: snapshot.fetched_at)
    return snapshots


def load_previous_context(
    data_dir: Path, platform: str, day: str
) -> dict[str, Any] | None:
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    path = day_directory(data_dir, platform, previous) / "report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    themes = [
        {
            "topic": str(theme.get("topic") or ""),
            "summary": str(theme.get("summary") or ""),
        }
        for theme in report.get("themes") or []
        if isinstance(theme, dict)
    ]
    return {
        "date": str(report.get("date") or previous),
        "overview": str(report.get("overview") or ""),
        "themes": themes,
    }


def load_previous_top_items(
    data_dir: Path, platform: str, day: str
) -> list[dict[str, Any]]:
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    path = day_directory(data_dir, platform, previous) / "top.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def apply_previous_top(
    top: list[dict[str, Any]], previous_items: list[dict[str, Any]]
) -> None:
    """Annotate Top100 entries against the previous day's Top100."""
    if not previous_items:
        return
    previous_by_id = {str(item.get("id")): item for item in previous_items}
    previous_rank = {
        str(item.get("id")): rank
        for rank, item in enumerate(previous_items, start=1)
    }
    for board_rank, entry in enumerate(top, start=1):
        entry["board_rank"] = board_rank
        previous = previous_by_id.get(str(entry["id"]))
        if previous is None:
            entry["status"] = "new"
            continue
        entry["status"] = "continuing"
        entry["prev_score"] = previous.get("score")
        previous_board_rank = previous_rank.get(str(entry["id"]))
        if previous_board_rank is not None:
            entry["prev_board_rank"] = previous_board_rank
            entry["board_rank_delta"] = previous_board_rank - board_rank


def load_boundary_snapshot(
    data_dir: Path,
    platform: str,
    day: str,
    day_start_hour: int,
    *,
    tolerance_minutes: int = 5,
) -> Snapshot | None:
    """Return the next day's opening snapshot at the day boundary, if any."""
    _, end = day_window(day, day_start_hour)
    end_value = datetime.fromisoformat(end)
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    next_dir = day_directory(data_dir, platform, next_day)

    candidates: list[tuple[datetime, Snapshot]] = []
    for path in sorted((next_dir / "snapshots").glob("*.json")):
        try:
            snapshot = load_snapshot(path)
            fetched = datetime.fromisoformat(snapshot.fetched_at)
        except (OSError, KeyError, TypeError, ValueError):
            continue
        if fetched <= end_value + timedelta(minutes=tolerance_minutes):
            candidates.append((fetched, snapshot))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def aggregate_snapshots(snapshots: list[Snapshot]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        for item in snapshot.items:
            entry = stats.get(item.id)
            if entry is None:
                entry = {
                    "id": item.id,
                    "title": item.title,
                    "url": item.url,
                    "excerpt": item.excerpt or "",
                    "appearances": 0,
                    "score": 0.0,
                    "best_rank": item.rank,
                    "first_seen": snapshot.fetched_at,
                    "last_seen": snapshot.fetched_at,
                    "rank_history": [],
                }
                stats[item.id] = entry
            entry["appearances"] += 1
            entry["score"] += 1.0 / max(1, item.rank)
            entry["best_rank"] = min(entry["best_rank"], item.rank)
            entry["last_seen"] = snapshot.fetched_at
            if item.title:
                entry["title"] = item.title
            if item.url:
                entry["url"] = item.url
            if item.excerpt:
                entry["excerpt"] = item.excerpt
            entry["rank_history"].append(
                {"t": snapshot.fetched_at, "rank": item.rank}
            )

    return sorted(
        stats.values(),
        key=lambda entry: (
            -entry["score"],
            -entry["appearances"],
            entry["best_rank"],
            entry["last_seen"],
        ),
    )


def build_top(
    items: list[dict[str, Any]],
    *,
    limit: int = TOP_LIMIT,
) -> list[dict[str, Any]]:
    return items[:limit]


def select_analyze_items(top: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick high-heat questions: score >= 1.0, clamped to 30-50."""
    high_heat = sum(
        1
        for entry in top
        if float(entry.get("score") or 0) >= HEAT_SCORE_THRESHOLD
    )
    count = min(MAX_ANALYZE, max(MIN_ANALYZE, high_heat))
    return top[:count]


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "title",
        "url",
        "excerpt",
        "appearances",
        "score",
        "best_rank",
        "first_seen",
        "last_seen",
        "status",
        "prev_score",
        "board_rank",
        "prev_board_rank",
        "board_rank_delta",
        "analysis",
    )
    return {key: entry[key] for key in keys if key in entry}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _answers_path(day_dir: Path, question_id: str) -> Path:
    return day_dir / "answers" / f"{question_id}.json"


def _load_answers(day_dir: Path, question_id: str) -> QuestionAnswers | None:
    path = _answers_path(day_dir, question_id)
    if not path.exists():
        return None
    try:
        return load_question_answers(path)
    except (OSError, ValueError):
        return None


def _dedupe(
    base: list[AnswerDigest], extra: list[AnswerDigest]
) -> list[AnswerDigest]:
    seen: set[str] = set()
    result: list[AnswerDigest] = []
    for answer in [*base, *extra]:
        key = answer.url or answer.token
        if key in seen:
            continue
        seen.add(key)
        result.append(answer)
    return result


def _save_answers(
    day_dir: Path,
    item: dict[str, Any],
    answers: list[AnswerDigest],
    *,
    pages: int,
    is_end: bool,
    next_offset: int | None,
) -> None:
    path = _answers_path(day_dir, item["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = QuestionAnswers(
        question_url=item["url"],
        fetched_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        answers=answers,
        pages=pages,
        is_end=is_end,
        next_offset=next_offset,
    )
    path.write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def classify_30001() -> tuple[str, int | None]:
    remaining = remaining_quota("question_discovery")
    if remaining is None:
        return "额度查询失败，无法区分每日额度与短时限流", None
    if remaining <= 0:
        return "当日回答额度已耗尽（剩余 0）", remaining
    return f"短时频率/并发限制（当日剩余 {remaining}）", remaining


def _fetch_page(
    item: dict[str, Any], offset: int
) -> "AnswerPage":
    """Fetch one page, cooling down once on a short-term 30001."""
    attempts = 0
    while True:
        try:
            return fetch_answer_page(
                item["url"], offset=offset, limit=PAGE_LIMIT, attempts=1
            )
        except ZhihuAPIError as exc:
            if exc.code != 30001:
                raise
            _, remaining = classify_30001()
            if remaining is not None and remaining > 0 and attempts < 1:
                attempts += 1
                print(
                    f"  {item['id']} 短时限流，等待 {RATE_LIMIT_COOLDOWN:g} 秒后重试 offset={offset}"
                )
                time.sleep(RATE_LIMIT_COOLDOWN)
                continue
            raise


def _record_pending(day_dir: Path, entries: list[dict], code: int | str) -> None:
    ids = sorted({entry["item"]["id"] for entry in entries})
    if not ids:
        return
    record_failure(
        day_dir,
        "answers",
        f"{len(ids)} 个问题中止时仍未完成翻页",
        {"question_ids": ids, "code": code},
    )


def _handle_api_error(
    exc: ZhihuAPIError,
    day_dir: Path,
    item: dict[str, Any],
    page_no: int,
    pending: list[dict],
) -> bool:
    """Return False for a retryable error; abort for fatal ones."""
    if exc.code == 30001:
        label, remaining = classify_30001()
        message = f"{exc}（{label}）"
        record_failure(
            day_dir,
            "answers",
            message,
            {
                "question_id": item["id"],
                "code": exc.code,
                "remaining_quota": remaining,
                "page": page_no,
            },
        )
        _record_pending(day_dir, pending, exc.code)
        raise SystemExit(message) from exc
    if exc.code in FATAL_CODES:
        record_failure(
            day_dir,
            "answers",
            str(exc),
            {"question_id": item["id"], "code": exc.code, "page": page_no},
        )
        _record_pending(day_dir, pending, exc.code)
        raise SystemExit(str(exc)) from exc
    print(f"  {item['id']} 第 {page_no} 页失败：{exc}")
    return False


def _answer_pages_needed(
    day_dir: Path, item: dict[str, Any], index: int
) -> int:
    """Quota cost for one question, accounting for already-collected answers."""
    planned_pages = 2 if index < DEEP_PAGES_LIMIT else 1
    existing = _load_answers(day_dir, item["id"])
    if existing is None:
        return planned_pages
    if existing.is_end or existing.pages >= planned_pages:
        return 0
    if index < DEEP_PAGES_LIMIT and existing.next_offset is not None:
        return 1
    return 0


def _apply_quota(
    day_dir: Path, analyze_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not analyze_items:
        return analyze_items

    costs = [
        _answer_pages_needed(day_dir, item, index)
        for index, item in enumerate(analyze_items)
    ]
    needed = sum(costs)
    if needed == 0:
        return analyze_items

    remaining = remaining_quota("question_discovery")
    if remaining is None:
        return analyze_items
    if remaining <= 0:
        message = "问题发现额度已用尽（RemainingQuota=0），跳过回答抓取"
        record_failure(
            day_dir,
            "answers",
            message,
            {"remaining_quota": remaining},
        )
        print(f"  {message}")
        return []
    if remaining >= needed:
        return analyze_items

    budget = remaining
    selected: list[dict[str, Any]] = []
    for item, cost in zip(analyze_items, costs):
        if budget < cost:
            break
        selected.append(item)
        budget -= cost
    print(f"  额度剩余 {remaining} 次，本轮只分析前 {len(selected)} 题")
    return selected


def collect_day_answers(
    day_dir: Path, analyze_items: list[dict[str, Any]]
) -> int:
    """Fetch pages for analyzed questions; return stored answer count."""
    if not analyze_items:
        return 0

    pending: list[dict] = []

    for index, item in enumerate(analyze_items):
        existing = _load_answers(day_dir, item["id"])
        if existing is not None:
            planned_pages = 2 if index < DEEP_PAGES_LIMIT else 1
            cleaned = _dedupe(existing.answers, [])[
                : planned_pages * PAGE_LIMIT
            ]
            if len(cleaned) != len(existing.answers):
                _save_answers(
                    day_dir,
                    item,
                    cleaned,
                    pages=existing.pages,
                    is_end=existing.is_end,
                    next_offset=existing.next_offset,
                )
            if existing.is_end or existing.pages >= planned_pages:
                continue
            if index < DEEP_PAGES_LIMIT and existing.next_offset is not None:
                pending.append(
                    {
                        "item": item,
                        "index": index,
                        "page": 2,
                        "offset": existing.next_offset,
                    }
                )
                continue
            continue

        try:
            page = _fetch_page(item, 0)
        except ZhihuAPIError as exc:
            if not _handle_api_error(exc, day_dir, item, 1, pending):
                pending.append(
                    {"item": item, "index": index, "page": 1, "offset": 0}
                )
            continue
        except SystemExit as exc:
            record_failure(
                day_dir, "answers", str(exc), {"question_id": item["id"], "page": 1}
            )
            _record_pending(day_dir, pending, "system-exit")
            raise

        answers = _dedupe([], page.answers)
        _save_answers(
            day_dir,
            item,
            answers,
            pages=1,
            is_end=page.is_end,
            next_offset=page.next_offset,
        )
        time.sleep(REQUEST_DELAY)

        if index < DEEP_PAGES_LIMIT and not page.is_end:
            if page.next_offset is None:
                record_failure(
                    day_dir,
                    "answers",
                    "分页信息不完整：IsEnd=false 但缺少 NextOffset",
                    {"question_id": item["id"]},
                )
                continue
            try:
                page2 = _fetch_page(item, page.next_offset)
            except ZhihuAPIError as exc:
                if not _handle_api_error(exc, day_dir, item, 2, pending):
                    pending.append(
                        {
                            "item": item,
                            "index": index,
                            "page": 2,
                            "offset": page.next_offset,
                        }
                    )
                continue
            except SystemExit as exc:
                record_failure(
                    day_dir,
                    "answers",
                    str(exc),
                    {"question_id": item["id"], "page": 2},
                )
                _record_pending(day_dir, pending, "system-exit")
                raise

            answers = _dedupe(answers, page2.answers)
            _save_answers(
                day_dir,
                item,
                answers,
                pages=2,
                is_end=page2.is_end,
                next_offset=page2.next_offset,
            )
            time.sleep(REQUEST_DELAY)

    for round_index in range(1, DEFERRED_RETRY_ROUNDS + 1):
        if not pending:
            break
        delay = DEFERRED_RETRY_DELAY * round_index
        print(
            f"  回答抓取第 {round_index}/{DEFERRED_RETRY_ROUNDS} 轮重试，"
            f"共 {len(pending)} 页，每次等待 {delay:g} 秒"
        )
        still_pending: list[dict] = []
        for position, entry in enumerate(pending):
            item = entry["item"]
            page_no = entry["page"]
            offset = entry["offset"]
            print(f"    等待 {delay:g} 秒后重试 {item['id']} 第 {page_no} 页")
            time.sleep(delay)

            try:
                page = _fetch_page(item, offset)
            except ZhihuAPIError as exc:
                remaining_entries = pending[position + 1 :] + still_pending
                if not _handle_api_error(
                    exc, day_dir, item, page_no, remaining_entries
                ):
                    still_pending.append(entry)
                continue
            except SystemExit as exc:
                record_failure(
                    day_dir,
                    "answers",
                    str(exc),
                    {"question_id": item["id"], "page": page_no},
                )
                _record_pending(
                    day_dir, pending[position + 1 :] + still_pending, "system-exit"
                )
                raise

            existing = _load_answers(day_dir, item["id"])
            base = existing.answers if existing is not None else []
            answers = _dedupe(base, page.answers)
            _save_answers(
                day_dir,
                item,
                answers,
                pages=page_no,
                is_end=page.is_end,
                next_offset=page.next_offset,
            )

            if (
                page_no == 1
                and entry["index"] < DEEP_PAGES_LIMIT
                and not page.is_end
            ):
                if page.next_offset is None:
                    record_failure(
                        day_dir,
                        "answers",
                        "分页信息不完整：IsEnd=false 但缺少 NextOffset",
                        {"question_id": item["id"]},
                    )
                else:
                    still_pending.append(
                        {
                            "item": item,
                            "index": entry["index"],
                            "page": 2,
                            "offset": page.next_offset,
                        }
                    )

        pending = still_pending

    for entry in pending:
        record_failure(
            day_dir,
            "answers",
            "回答页重试后仍失败",
            {"question_id": entry["item"]["id"], "page": entry["page"]},
        )

    total = 0
    for item in analyze_items:
        answers = _load_answers(day_dir, item["id"])
        if answers is not None:
            total += len(answers.answers)
    return total


def _normalize_analysis(data: dict[str, Any]) -> dict[str, Any]:
    key_points = data.get("key_points")
    if not isinstance(key_points, list):
        key_points = []
    topics = data.get("topics")
    if not isinstance(topics, list):
        topics = []
    controversy = data.get("controversy") or None
    return {
        "summary": str(data.get("summary") or ""),
        "key_points": [str(point) for point in key_points][:5],
        "controversy": str(controversy) if controversy else None,
        "topics": [str(topic) for topic in topics][:3],
        "why_hot": str(data.get("why_hot") or ""),
    }


def _analysis_cache_path(day_dir: Path, question_id: str) -> Path:
    return day_dir / "analyses" / f"{question_id}.json"


def _load_cached_analysis(
    day_dir: Path, question_id: str
) -> dict[str, Any] | None:
    path = _analysis_cache_path(day_dir, question_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("prompt_version") != QUESTION_PROMPT_VERSION:
        return None
    if data.get("model") != model_name():
        return None
    analysis = data.get("analysis")
    return analysis if isinstance(analysis, dict) else None


def _save_cached_analysis(
    day_dir: Path,
    item: dict[str, Any],
    analysis: dict[str, Any],
    usage: dict[str, Any],
) -> None:
    path = _analysis_cache_path(day_dir, item["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": item["id"],
        "model": model_name(),
        "prompt_version": QUESTION_PROMPT_VERSION,
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        "analysis": analysis,
        "usage": usage,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def analyze_questions(
    day_dir: Path, analyze_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    total = len(analyze_items)
    for index, item in enumerate(analyze_items, start=1):
        print(f"    逐题分析 {index}/{total}: {item['id']}")
        cached = _load_cached_analysis(day_dir, item["id"])
        if cached is not None:
            item["analysis"] = cached
            print("      命中缓存，跳过模型调用")
            continue

        answers = _load_answers(day_dir, item["id"])
        summaries = (
            [answer.summary[:SUMMARY_CHAR_LIMIT] for answer in answers.answers]
            if answers is not None
            else []
        )
        payload = {
            "id": item["id"],
            "title": item["title"],
            "url": item["url"],
            "appearances": item["appearances"],
            "best_rank": item["best_rank"],
            "score": round(item["score"], 3),
            "hot_summary": item.get("excerpt") or "",
            "answers": summaries,
        }
        try:
            response = chat_json(
                QUESTION_SYSTEM, json.dumps(payload, ensure_ascii=False)
            )
        except SystemExit as exc:
            record_failure(
                day_dir,
                "report",
                f"逐题分析失败 {item['id']}: {exc}",
                {"question_id": item["id"]},
            )
            raise
        analysis = _normalize_analysis(response.data)
        item["analysis"] = analysis
        _save_cached_analysis(day_dir, item, analysis, response.usage)
        usages.append(dict(response.usage, purpose=f"question {item['id']}"))
    return usages


def aggregate_analysis(
    day_dir: Path,
    day: str,
    *,
    top: list[dict[str, Any]],
    analyze_items: list[dict[str, Any]],
    previous_context: dict[str, Any] | None,
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    known_ids = {item["id"] for item in top}

    payload = {
        "date": day,
        "previous_day": previous_context,
        "analyses": [
            {"id": item["id"], "title": item["title"], "analysis": item.get("analysis")}
            for item in analyze_items
        ],
        "top_items": [
            {
                "id": item["id"],
                "title": item["title"],
                "score": round(item["score"], 3),
                "best_rank": item["best_rank"],
                "appearances": item["appearances"],
            }
            for item in top
        ],
    }
    try:
        response = chat_json(AGG_SYSTEM, json.dumps(payload, ensure_ascii=False))
    except SystemExit as exc:
        record_failure(day_dir, "report", f"汇总分析失败：{exc}")
        raise

    data = response.data
    themes = []
    for raw in data.get("themes") or []:
        if not isinstance(raw, dict):
            continue
        ids = [
            str(item)
            for item in raw.get("question_ids") or []
            if str(item) in known_ids
        ]
        themes.append(
            {
                "topic": str(raw.get("topic") or ""),
                "summary": str(raw.get("summary") or ""),
                "question_ids": ids,
            }
        )
    return (
        str(data.get("overview") or ""),
        str(data.get("day_over_day") or ""),
        themes,
        dict(response.usage, purpose="aggregate"),
    )


def _usage_totals(usages: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "calls": len(usages),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cache_hit_tokens": 0,
        "total_tokens": 0,
    }
    for usage in usages:
        for key in totals:
            if key != "calls":
                totals[key] += int(usage.get(key) or 0)
    return totals


def run_snapshot(data_dir: Path, *, day_start_hour: int = 4) -> Path:
    snapshot = fetch_hot_list()
    path = save_snapshot(snapshot, data_dir, day_start_hour=day_start_hour)
    print(
        f"已抓取 {len(snapshot.items)} 条热榜，保存到 {path}"
        f"（逻辑日 {path.parent.parent.name}）"
    )
    return path


def run_daily_summary(
    data_dir: Path,
    out_dir: Path,
    day: str,
    *,
    day_start_hour: int = 4,
    deploy: bool = True,
    boundary_snapshot: Path | None = None,
) -> Path:
    platform = "zhihu"
    day_dir = day_directory(data_dir, platform, day)
    step = "aggregate"

    try:
        snapshots = load_day_snapshots(day_dir)
        boundary = None
        if boundary_snapshot is not None and boundary_snapshot.exists():
            try:
                boundary = load_snapshot(boundary_snapshot)
            except (OSError, KeyError, TypeError, ValueError):
                boundary = None
        if boundary is None:
            boundary = load_boundary_snapshot(
                data_dir, platform, day, day_start_hour
            )
        if boundary is not None and all(
            snapshot.fetched_at != boundary.fetched_at for snapshot in snapshots
        ):
            snapshots.append(boundary)
            snapshots.sort(key=lambda snapshot: snapshot.fetched_at)

        if not snapshots:
            raise SystemExit(f"{day_dir} 没有快照，无法生成日报")

        items = aggregate_snapshots(snapshots)
        top = build_top(items)
        apply_previous_top(top, load_previous_top_items(data_dir, platform, day))
        previous_context = load_previous_context(data_dir, platform, day)

        step = "answers"
        analyze_items = _apply_quota(day_dir, select_analyze_items(top))
        print(f"  回答抓取：{len(analyze_items)} 题")
        answers_count = collect_day_answers(day_dir, analyze_items)
        print(f"  回答摘要：{answers_count} 条")

        step = "report"
        usages: list[dict[str, Any]] = []
        if analyze_items:
            print(f"  逐题分析：{len(analyze_items)} 题")
            usages.extend(analyze_questions(day_dir, analyze_items))
            print("  汇总分析…")
            overview, dod_summary, themes, aggregate_usage = aggregate_analysis(
                day_dir,
                day,
                top=top,
                analyze_items=analyze_items,
                previous_context=previous_context,
            )
            usages.append(aggregate_usage)
        else:
            overview = "回答摘要额度不足，本日仅保留榜单统计。"
            dod_summary = (
                "回答额度不足，未生成模型对比分析。"
                if previous_context is not None
                else "无前一逻辑日议题数据，无法对比。"
            )
            themes = []

        start, end = day_window(day, day_start_hour)
        top_doc = {
            "schema_version": 1,
            "platform": platform,
            "date": day,
            "window": {"start": start, "end": end},
            "snapshots": len(snapshots),
            "unique_questions": len(items),
            "items": [_compact(entry) for entry in top],
        }
        report = {
            "schema_version": 2,
            "platform": platform,
            "date": day,
            "window": {"start": start, "end": end},
            "generated_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
            "model": model_name(),
            "coverage": {
                "snapshots": len(snapshots),
                "unique_questions": len(items),
                "analyzed_questions": len(analyze_items),
                "answers_analyzed": answers_count,
            },
            "overview": overview,
            "day_over_day": {
                "summary": dod_summary,
                "previous_date": (previous_context or {}).get("date"),
            },
            "themes": themes,
            "usage": {"calls": usages, "totals": _usage_totals(usages)},
        }
        _write_json(day_dir / "top.json", top_doc)
        _write_json(day_dir / "report.json", report)

        step = "build"
        count = build_site(data_dir, out_dir)
        print(f"已生成 {count} 个日报页面 -> {out_dir}")

        step = "deploy"
        if deploy and deploy_site(out_dir):
            print("已发布到远端")
    except (SystemExit, Exception) as exc:
        if not load_failures(day_dir):
            record_failure(day_dir, step, str(exc))
        notify_failures(day_dir)
        raise

    notify_failures(day_dir)
    return day_dir / "report.json"
