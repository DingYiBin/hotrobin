"""Build daily sentiment reports from the hot list and answer summaries."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .deepseek import chat_json, model_name
from .failures import record_failure
from .models import (
    SHANGHAI_TZ,
    Snapshot,
    load_question_answers,
    load_snapshot,
)

SCHEMA_VERSION = 1
PROMPT_VERSION = 1
SENTIMENTS = ("正面", "负面", "中性", "两极")
DEFAULT_BATCH_SIZE = 5
DEFAULT_ANSWERS_PER_QUESTION = 50
SUMMARY_CHAR_LIMIT = 300

BATCH_SYSTEM = """你是中文舆情分析助手。输入是知乎热榜中的若干问题，以及每个问题下高赞回答的摘要（摘要可能不完整）。
只依据给定材料分析，不要补充外部事实，不要编造数据；材料不足时在 summary 中说明。
对每个问题输出：
- id：与输入一致
- sentiment：只能是 正面 / 负面 / 中性 / 两极 之一，表示回答讨论的主要情绪基调
- tone：20 字以内的基调描述
- summary：2-3 句话概括主要观点与讨论走向
- key_viewpoints：3-5 条代表性观点，每条一句话
- controversy：主要分歧点；没有明显分歧时为 null
- topics：1-3 个议题标签
只输出 JSON，格式：{"questions":[{"id":"...","sentiment":"...","tone":"...","summary":"...","key_viewpoints":["..."],"controversy":null,"topics":["..."]}]}"""

AGG_SYSTEM = """你是中文舆情分析助手。输入是今日知乎热榜各问题的舆情分析结果。
只依据输入汇总，不要编造。
输出：
- overall_tone：今日整体舆论基调，30 字以内
- overall_summary：3-5 句话的当日总结
- themes：2-6 个议题，每个包含 topic、sentiment（正面/负面/中性/两极）、summary、question_ids（只使用输入中出现的问题 id）
- controversies：0-3 个主要争议，每个包含 title、summary、question_ids
只输出 JSON，格式：{"overall_tone":"...","overall_summary":"...","themes":[{"topic":"...","sentiment":"...","summary":"...","question_ids":["..."]}],"controversies":[{"title":"...","summary":"...","question_ids":["..."]}]}"""


def _load_answer_summaries(answers_dir: Path, question_id: str, limit: int) -> list[str]:
    path = answers_dir / f"{question_id}.json"
    if not path.exists():
        return []
    collected = load_question_answers(path)
    summaries = []
    for answer in collected.answers[:limit]:
        text = answer.summary.strip()
        if text:
            summaries.append(text[:SUMMARY_CHAR_LIMIT])
    return summaries


def _bundles(
    snapshot: Snapshot, answers_dir: Path, answers_per_question: int
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "url": item.url,
            "summary": item.excerpt or "",
            "answers": _load_answer_summaries(answers_dir, item.id, answers_per_question),
        }
        for item in snapshot.items
    ]


def _normalize_question(raw: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    sentiment = raw.get("sentiment") if raw.get("sentiment") in SENTIMENTS else "中性"
    viewpoints = raw.get("key_viewpoints")
    if not isinstance(viewpoints, list):
        viewpoints = []
    topics = raw.get("topics")
    if not isinstance(topics, list):
        topics = []
    controversy = raw.get("controversy") or None
    return {
        "id": bundle["id"],
        "title": bundle["title"],
        "url": bundle["url"],
        "sentiment": sentiment,
        "tone": str(raw.get("tone") or ""),
        "summary": str(raw.get("summary") or ""),
        "key_viewpoints": [str(point) for point in viewpoints][:5],
        "controversy": str(controversy) if controversy else None,
        "topics": [str(topic) for topic in topics][:3],
        "answers_analyzed": len(bundle["answers"]),
    }


def _analyze_batch(
    batch: list[dict[str, Any]], purpose: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = [
        {
            "id": bundle["id"],
            "title": bundle["title"],
            "summary": bundle["summary"],
            "answers": bundle["answers"],
        }
        for bundle in batch
    ]
    response = chat_json(BATCH_SYSTEM, json.dumps({"questions": payload}, ensure_ascii=False))
    data = response.data
    raw_results = data.get("questions")
    if not isinstance(raw_results, list):
        raise ValueError("模型输出缺少 questions 数组")

    by_id = {bundle["id"]: bundle for bundle in batch}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        question_id = str(raw.get("id") or "")
        bundle = by_id.get(question_id)
        if bundle is None or question_id in seen:
            continue
        seen.add(question_id)
        results.append(_normalize_question(raw, bundle))

    missing = [bundle["id"] for bundle in batch if bundle["id"] not in seen]
    if missing:
        print(f"  模型漏掉：{', '.join(missing)}")
    return results, dict(response.usage, purpose=purpose)


def _aggregate(
    results: list[dict[str, Any]], known_ids: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = [
        {
            "id": result["id"],
            "title": result["title"],
            "sentiment": result["sentiment"],
            "summary": result["summary"],
            "topics": result["topics"],
        }
        for result in results
    ]
    response = chat_json(AGG_SYSTEM, json.dumps({"questions": payload}, ensure_ascii=False))
    data = response.data

    themes = []
    for raw in data.get("themes") or []:
        if not isinstance(raw, dict):
            continue
        ids = [str(item) for item in raw.get("question_ids") or [] if str(item) in known_ids]
        sentiment = raw.get("sentiment") if raw.get("sentiment") in SENTIMENTS else "中性"
        themes.append(
            {
                "topic": str(raw.get("topic") or ""),
                "sentiment": sentiment,
                "summary": str(raw.get("summary") or ""),
                "question_ids": ids,
            }
        )

    controversies = []
    for raw in data.get("controversies") or []:
        if not isinstance(raw, dict):
            continue
        ids = [str(item) for item in raw.get("question_ids") or [] if str(item) in known_ids]
        controversies.append(
            {
                "title": str(raw.get("title") or ""),
                "summary": str(raw.get("summary") or ""),
                "question_ids": ids,
            }
        )

    overall = {
        "tone": str(data.get("overall_tone") or ""),
        "summary": str(data.get("overall_summary") or ""),
        "themes": themes,
        "controversies": controversies,
    }
    return overall, dict(response.usage, purpose="aggregate")


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


def _sentiment_summary(results: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, float]]:
    counts = Counter(result["sentiment"] for result in results)
    total = sum(counts.values()) or 1
    ordered = {sentiment: counts.get(sentiment, 0) for sentiment in SENTIMENTS}
    ratios = {sentiment: round(count / total, 3) for sentiment, count in ordered.items()}
    return ordered, ratios


def generate_report(
    run_dir: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    answers_per_question: int = DEFAULT_ANSWERS_PER_QUESTION,
) -> Path:
    """Create ``<run_dir>/report.json`` and return its path."""
    snapshot_path = run_dir / "snapshot.json"
    if not snapshot_path.exists():
        raise SystemExit(f"找不到 {snapshot_path}，先运行 hotrobin fetch")

    snapshot = load_snapshot(snapshot_path)
    bundles = _bundles(snapshot, run_dir / "answers", answers_per_question)

    results: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    for start in range(0, len(bundles), batch_size):
        batch = bundles[start : start + batch_size]
        batch_label = f"questions {start + 1}-{start + len(batch)}"
        print(f"  分析 {start + 1}-{start + len(batch)} / {len(bundles)}")
        try:
            batch_results, usage = _analyze_batch(batch, batch_label)
        except (KeyError, TypeError, ValueError) as exc:
            record_failure(
                run_dir, "report", f"{batch_label}: {exc}", {"batch": batch_label}
            )
            print(f"  这批分析失败：{exc}")
            continue
        except SystemExit as exc:
            record_failure(run_dir, "report", str(exc), {"batch": batch_label})
            raise
        results.extend(batch_results)
        usages.append(usage)

    if not results:
        record_failure(run_dir, "report", "没有生成任何逐题分析")
        raise SystemExit("没有生成任何逐题分析，请检查 DeepSeek 配置或输出")

    known_ids = {result["id"] for result in results}
    try:
        overall, aggregate_usage = _aggregate(results, known_ids)
    except SystemExit as exc:
        record_failure(run_dir, "report", str(exc), {"batch": "aggregate"})
        raise
    except (KeyError, TypeError, ValueError) as exc:
        record_failure(run_dir, "report", f"汇总失败：{exc}", {"batch": "aggregate"})
        raise SystemExit(f"汇总失败：{exc}") from exc
    usages.append(aggregate_usage)
    usage_totals = _usage_totals(usages)
    counts, ratios = _sentiment_summary(results)

    report = {
        "schema_version": SCHEMA_VERSION,
        "platform": "zhihu",
        "date": snapshot.date,
        "run_at": snapshot.fetched_at,
        "model": model_name(),
        "prompt_version": PROMPT_VERSION,
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        "coverage": {
            "questions": len(bundles),
            "questions_analyzed": len(results),
            "answers_analyzed": sum(result["answers_analyzed"] for result in results),
        },
        "usage": {
            "calls": usages,
            "totals": usage_totals,
        },
        "overall": {
            "tone": overall["tone"],
            "summary": overall["summary"],
            "sentiment_counts": counts,
            "sentiment_ratios": ratios,
            "method": "按题目数量等权（官方热榜不提供热度数值）",
        },
        "themes": overall["themes"],
        "controversies": overall["controversies"],
        "questions": results,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"  tokens：输入 {usage_totals['prompt_tokens']:,} / "
        f"输出 {usage_totals['completion_tokens']:,}"
        f"（其中推理 {usage_totals['reasoning_tokens']:,}），"
        f"共 {usage_totals['calls']} 次调用"
    )
    return report_path
