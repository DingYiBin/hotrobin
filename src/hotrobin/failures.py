"""Failure records and optional webhook notifications.

Failures inside a run are appended to ``<run>/failures.json``. Failures that
happen before a run directory exists (a failed hot-list fetch) go to
``data/zhihu/failures/<timestamp>.json`` instead.

Set ``HOTROBIN_NOTIFY_WEBHOOK`` to send one notification per failed run.
Server酱, Slack and Discord incoming webhooks are detected from the host.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import SHANGHAI_TZ

MAX_NOTIFY_ITEMS = 8
MAX_MESSAGE_CHARS = 300


def _now() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def record_failure(
    run_dir: Path | None,
    step: str,
    message: str,
    context: dict[str, Any] | None = None,
    *,
    data_dir: Path | None = None,
) -> Path:
    """Append a failure entry and return the JSON file path."""
    entry = {
        "time": _now(),
        "step": step,
        "message": message,
        "context": context or {},
    }

    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "failures.json"
        entries = _read(path)
    else:
        base = (data_dir or Path("data")) / "zhihu" / "failures"
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d-%H%M%S")
        path = base / f"{stamp}.json"
        suffix = 2
        while path.exists():
            path = base / f"{stamp}-{suffix}.json"
            suffix += 1
        entries = []

    entries.append(entry)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_failures(run_dir: Path) -> list[dict[str, Any]]:
    return _read(run_dir / "failures.json")


def _build_message(
    run_dir: Path | None, entries: list[dict[str, Any]]
) -> tuple[str, str]:
    title = f"hotrobin 失败 {datetime.now(SHANGHAI_TZ).strftime('%m-%d %H:%M')}"
    label = run_dir.name if run_dir is not None else "抓取前失败"
    lines = [f"运行：{label}", f"失败 {len(entries)} 项："]
    for entry in entries[:MAX_NOTIFY_ITEMS]:
        context = entry.get("context") or {}
        extra = f" {json.dumps(context, ensure_ascii=False)}" if context else ""
        message = str(entry.get("message") or "")[:MAX_MESSAGE_CHARS]
        lines.append(f"- [{entry.get('step')}] {message}{extra}")
    if len(entries) > MAX_NOTIFY_ITEMS:
        lines.append(f"… 其余 {len(entries) - MAX_NOTIFY_ITEMS} 项见失败记录")
    if run_dir is not None:
        lines.append(f"详情：{run_dir / 'failures.json'}")
    return title, "\n".join(lines)


def _send(title: str, message: str) -> None:
    url = os.environ.get("HOTROBIN_NOTIFY_WEBHOOK", "").strip()
    if not url:
        print("[通知] 未配置 HOTROBIN_NOTIFY_WEBHOOK，跳过发送")
        return

    host = urllib.parse.urlsplit(url).hostname or ""
    headers = {"User-Agent": "hotrobin", "Content-Type": "application/json"}
    if "sctapi.ftqq.com" in host:
        body = urllib.parse.urlencode({"title": title, "desp": message}).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif "discord.com" in host or "discordapp.com" in host:
        body = json.dumps(
            {"content": f"{title}\n{message}"}, ensure_ascii=False
        ).encode("utf-8")
    else:
        body = json.dumps({"text": f"{title}\n{message}"}, ensure_ascii=False).encode(
            "utf-8"
        )

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        print("[通知] 失败通知已发送")
    except Exception as exc:  # notification must never break the pipeline
        print(f"[通知] 发送失败：{exc}")


def notify_failures(
    run_dir: Path | None,
    *,
    failure_path: Path | None = None,
) -> None:
    """Send one webhook notification if there are recorded failures."""
    if failure_path is not None:
        entries = _read(failure_path)
    elif run_dir is not None:
        entries = load_failures(run_dir)
    else:
        return

    if not entries:
        return
    title, message = _build_message(run_dir, entries)
    _send(title, message)
