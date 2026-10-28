"""Snapshot schema for hot-topic data.

Each daily snapshot lives at ``data/<platform>/<YYYY-MM-DD>/snapshot.json``
and looks like::

    {
      "schema_version": 1,
      "platform": "zhihu",
      "fetched_at": "2026-10-28T08:00:00+08:00",
      "items": [
        {
          "rank": 1,
          "id": "123456789",
          "title": "……",
          "url": "https://www.zhihu.com/question/123456789",
          "hot_score": 1234567,
          "answer_count": 210,
          "excerpt": "……"
        }
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SHANGHAI_TZ = timezone(timedelta(hours=8))


def today_shanghai() -> str:
    """Today's date in Asia/Shanghai (UTC+8)."""
    return datetime.now(SHANGHAI_TZ).date().isoformat()


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class HotItem:
    rank: int
    id: str
    title: str
    url: str
    hot_score: int | None = None
    answer_count: int | None = None
    excerpt: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HotItem:
        return cls(
            rank=int(raw["rank"]),
            id=str(raw["id"]),
            title=str(raw["title"]),
            url=str(raw["url"]),
            hot_score=_optional_int(raw.get("hot_score")),
            answer_count=_optional_int(raw.get("answer_count")),
            excerpt=raw.get("excerpt"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "hot_score": self.hot_score,
            "answer_count": self.answer_count,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class Snapshot:
    platform: str
    fetched_at: str
    items: list[HotItem]
    schema_version: int = SCHEMA_VERSION

    @property
    def date(self) -> str:
        """Local date of the snapshot, taken from its own UTC offset."""
        return datetime.fromisoformat(self.fetched_at).date().isoformat()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Snapshot:
        return cls(
            platform=str(raw["platform"]),
            fetched_at=str(raw["fetched_at"]),
            items=[HotItem.from_dict(item) for item in raw["items"]],
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "platform": self.platform,
            "fetched_at": self.fetched_at,
            "items": [item.to_dict() for item in self.items],
        }


def load_snapshot(path: Path) -> Snapshot:
    """Load and validate a snapshot file."""
    return Snapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_snapshot(snapshot: Snapshot, data_dir: Path) -> Path:
    """Write a snapshot to ``data/<platform>/<date>/snapshot.json``."""
    path = data_dir / snapshot.platform / snapshot.date / "snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class AnswerDigest:
    """One answer summary returned by the official question_answers API."""

    token: str
    url: str
    summary: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AnswerDigest:
        return cls(
            token=str(raw.get("token") or ""),
            url=str(raw.get("url") or ""),
            summary=str(raw.get("summary") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"token": self.token, "url": self.url, "summary": self.summary}


@dataclass(frozen=True)
class QuestionAnswers:
    """Answer summaries collected for one question."""

    question_url: str
    fetched_at: str
    answers: list[AnswerDigest]
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionAnswers:
        return cls(
            question_url=str(raw.get("question_url") or ""),
            fetched_at=str(raw.get("fetched_at") or ""),
            answers=[AnswerDigest.from_dict(item) for item in raw.get("answers") or []],
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "question_url": self.question_url,
            "fetched_at": self.fetched_at,
            "answers": [answer.to_dict() for answer in self.answers],
        }


def load_question_answers(path: Path) -> QuestionAnswers:
    """Load answer summaries from ``answers/<question_id>.json``."""
    return QuestionAnswers.from_dict(json.loads(path.read_text(encoding="utf-8")))
