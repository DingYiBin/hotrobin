"""Daily hot-topic aggregation, answer collection, and change analysis.

A logical day starts at 04:00 Asia/Shanghai. Snapshots for the day live in
``<day>/snapshots/``; the summary writes ``top.json``, ``seen.json``,
``answers/``, and ``report.json`` in the same day directory.
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
    ZhihuAPIError,
    fetch_answer_page,
    fetch_hot_list,
    remaining_quota,
)

TOP_LIMIT = 100
ANALYZE_LIMIT = 30
DEEP_PAGES_LIMIT = 10
PAGE_LIMIT = DEFAULT_ANSWER_LIMIT
REQUEST_DELAY = 0.3
DEFERRED_RETRY_ROUNDS = 3
DEFERRED_RETRY_DELAY = 3.0
FATAL_CODES = {20001, 30002, 30003}
SUMMARY_CHAR_LIMIT = 300

QUESTION_SYSTEM = """你是中文热点分析助手。输入是一个知乎热榜问题、当日榜单指标和高赞回答摘要。
只依据给定材料分析，不要补充外部事实，不要编造数据；材料不足时在 summary 中说明。
输出 JSON：
{"summary":"2-4句话概括讨论内容","key_points":["3-5条代表性观点"],"controversy":"主要分歧，没有则为null","topics":["1-3个议题标签"],"why_hot":"一句话说明为什么上榜"}"""

AGG_SYSTEM = """你是中文热点变化分析助手。输入是某个逻辑日的知乎热榜统计、新增/持续/退榜题目和 Top30 逐题分析。
只依据输入，不要编造。
输出 JSON：
{"overview":"3-5句话总结当天热点变化","themes":[{"topic":"议题名","summary":"概述","question_ids":["只能使用输入中出现过的 id"]}]}"""


def day_window(day: str, day_start_hour: int) -> tuple[str, str]:
    start = datetime.fromisoformat(day).replace(
        hour=day_start_hour, minute=0, second=0, microsecond=0, tzinfo=SHANGHAI_TZ
    )
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def previous_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def load_day_snapshots(day_dir: Path) -> list[Snapshot]:
    snapshots = []
    for path in sorted((day_dir / "snapshots").glob("*.json")):
        try:
            snapshots.append(load_snapshot(path))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            print(f"  跳过无法读取的快照 {path}: {exc}")
    snapshots.sort(key=lambda snapshot: snapshot.fetched_at)
    return snapshots


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


def load_previous_items(data_dir: Path, platform: str, day: str) -> dict[str, dict]:
    prev = previous_day(day)
    prev_dir = day_directory(data_dir, platform, prev)
    seen_path = prev_dir / "seen.json"
    if seen_path.exists():
        try:
            payload = json.loads(seen_path.read_text(encoding="utf-8"))
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list):
                return {
                    str(item["id"]): item
                    for item in items
                    if isinstance(item, dict) and item.get("id") is not None
                }
        except (OSError, ValueError):
            pass

    snapshots = load_day_snapshots(prev_dir)
    if snapshots:
        return {entry["id"]: entry for entry in aggregate_snapshots(snapshots)}
    return {}


def build_top(
    items: list[dict[str, Any]],
    previous: dict[str, dict],
    *,
    limit: int = TOP_LIMIT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_ids = {entry["id"] for entry in items}

    for entry in items:
        prev = previous.get(entry["id"])
        entry["status"] = "new" if prev is None else "continuing"
        if prev is not None:
            entry["prev_score"] = prev.get("score")
            entry["prev_best_rank"] = prev.get("best_rank")
            entry["prev_appearances"] = prev.get("appearances")

    top = items[:limit]

    retired = [
        dict(entry, status="retired")
        for entry in previous.values()
        if entry.get("id") not in current_ids
    ]
    retired.sort(
        key=lambda entry: (
            -float(entry.get("score") or 0),
            int(entry.get("best_rank") or 999),
        )
    )
    return top, retired


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
        "prev_best_rank",
        "prev_appearances",
        "analysis",
    )
    return {key: entry[key] for key in keys if key in entry}


def _seen(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "title",
        "url",
        "appearances",
        "score",
        "best_rank",
        "first_seen",
        "last_seen",
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
        key = answer.token or answer.url
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


def _apply_quota(
    day_dir: Path, analyze_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not analyze_items:
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

    needed = sum(2 if index < DEEP_PAGES_LIMIT else 1 for index in range(len(analyze_items)))
    if remaining >= needed:
        return analyze_items

    budget = remaining
    selected: list[dict[str, Any]] = []
    for index, item in enumerate(analyze_items):
        cost = 2 if index < DEEP_PAGES_LIMIT else 1
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
            if existing.is_end:
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
            page = fetch_answer_page(
                item["url"], offset=0, limit=PAGE_LIMIT, attempts=1
            )
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
                page2 = fetch_answer_page(
                    item["url"],
                    offset=page.next_offset,
                    limit=PAGE_LIMIT,
                    attempts=1,
                )
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
                page = fetch_answer_page(
                    item["url"], offset=offset, limit=PAGE_LIMIT, attempts=1
                )
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


def analyze_questions(
    day_dir: Path, analyze_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    for item in analyze_items:
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
        item["analysis"] = _normalize_analysis(response.data)
        usages.append(dict(response.usage, purpose=f"question {item['id']}"))
    return usages


def aggregate_analysis(
    day_dir: Path,
    day: str,
    *,
    counts: dict[str, int],
    top: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    continuing: list[dict[str, Any]],
    retired: list[dict[str, Any]],
    analyze_items: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    known_ids = {item["id"] for item in top}
    payload = {
        "date": day,
        "counts": counts,
        "new_topics": [_compact(item) for item in new_items[:20]],
        "continuing_topics": [_compact(item) for item in continuing[:20]],
        "retired_topics": [_compact(item) for item in retired],
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
        previous = load_previous_items(data_dir, platform, day)
        top, retired_all = build_top(items, previous)
        new_items = [entry for entry in top if entry["status"] == "new"]
        continuing = [entry for entry in top if entry["status"] == "continuing"]
        retired = retired_all[:20]
        counts = {
            "snapshots": len(snapshots),
            "unique_questions": len(items),
            "new": len(new_items),
            "continuing": len(continuing),
            "retired": len(retired_all),
        }

        step = "answers"
        analyze_items = _apply_quota(day_dir, top[:ANALYZE_LIMIT])
        answers_count = collect_day_answers(day_dir, analyze_items)

        step = "report"
        usages: list[dict[str, Any]] = []
        if analyze_items:
            usages.extend(analyze_questions(day_dir, analyze_items))
            overview, themes, aggregate_usage = aggregate_analysis(
                day_dir,
                day,
                counts=counts,
                top=top,
                new_items=new_items,
                continuing=continuing,
                retired=retired,
                analyze_items=analyze_items,
            )
            usages.append(aggregate_usage)
        else:
            overview = "回答摘要额度不足，本日仅保留榜单统计。"
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
        seen_doc = {
            "date": day,
            "items": [_seen(entry) for entry in items],
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
            "counts": counts,
            "overview": overview,
            "new_topics": [_compact(entry) for entry in new_items[:20]],
            "continuing_topics": [_compact(entry) for entry in continuing[:20]],
            "retired_topics": [_compact(entry) for entry in retired],
            "themes": themes,
            "usage": {"calls": usages, "totals": _usage_totals(usages)},
        }
        _write_json(day_dir / "top.json", top_doc)
        _write_json(day_dir / "seen.json", seen_doc)
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
