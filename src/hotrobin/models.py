"""Snapshot schema for hot-topic data.

Snapshots live at
``data/<platform>/<year>/<month>/<logical-day>/snapshots/<run>.json``.
A logical day starts at 04:00 Asia/Shanghai, so the 00:00–03:59 snapshots
belong to the previous day. Each snapshot looks like::

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
DEFAULT_DAY_START_HOUR = 4


def logical_day(fetched_at: str, day_start_hour: int = DEFAULT_DAY_START_HOUR) -> str:
    """Return the logical day for a timestamp (04:00 boundary by default)."""
    value = datetime.fromisoformat(fetched_at) - timedelta(hours=day_start_hour)
    return value.date().isoformat()


def day_directory(data_dir: Path, platform: str, day: str) -> Path:
    """Return ``data/<platform>/<year>/<month>/<logical-day>``."""
    return data_dir / platform / day[:4] / day[5:7] / day


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

    @property
    def run_id(self) -> str:
        """Timestamped run directory name, e.g. ``2026-10-28-214503``."""
        return datetime.fromisoformat(self.fetched_at).strftime("%Y-%m-%d-%H%M%S")

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


def save_snapshot(
    snapshot: Snapshot,
    data_dir: Path,
    *,
    day_start_hour: int = DEFAULT_DAY_START_HOUR,
) -> Path:
    """Write a snapshot under its logical day's ``snapshots/`` directory."""
    day = logical_day(snapshot.fetched_at, day_start_hour)
    base = snapshot.run_id
    snapshots_dir = day_directory(data_dir, snapshot.platform, day) / "snapshots"
    path = snapshots_dir / f"{base}.json"
    suffix = 2
    while path.exists():
        path = snapshots_dir / f"{base}-{suffix}.json"
        suffix += 1

    snapshots_dir.mkdir(parents=True, exist_ok=True)
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
    pages: int = 1
    is_end: bool = True
    next_offset: int | None = None
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionAnswers:
        return cls(
            question_url=str(raw.get("question_url") or ""),
            fetched_at=str(raw.get("fetched_at") or ""),
            answers=[AnswerDigest.from_dict(item) for item in raw.get("answers") or []],
            pages=int(raw.get("pages", 1) or 1),
            is_end=bool(raw.get("is_end", True)),
            next_offset=_optional_int(raw.get("next_offset")),
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "question_url": self.question_url,
            "fetched_at": self.fetched_at,
            "pages": self.pages,
            "is_end": self.is_end,
            "next_offset": self.next_offset,
            "answers": [answer.to_dict() for answer in self.answers],
        }


def load_question_answers(path: Path) -> QuestionAnswers:
    """Load answer summaries from ``answers/<question_id>.json``."""
    return QuestionAnswers.from_dict(json.loads(path.read_text(encoding="utf-8")))
