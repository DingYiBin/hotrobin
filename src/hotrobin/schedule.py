"""Interval hot-list snapshots plus a daily summary at 04:00.

Environment:

- ``HOTROBIN_FETCH_MINUTES``: snapshot interval, default 30 minutes.
- ``HOTROBIN_SUMMARY_AT``: daily summary time, default ``04:00``. This time
  also defines the logical day boundary.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .daily import run_daily_summary, run_snapshot
from .failures import record_failure
from .models import SHANGHAI_TZ, day_directory, logical_day

DEFAULT_FETCH_MINUTES = 30
DEFAULT_SUMMARY_AT = "04:00"
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_summary_at(value: str) -> tuple[int, int]:
    match = TIME_RE.match(value.strip())
    if not match:
        raise SystemExit(
            f"HOTROBIN_SUMMARY_AT 格式应为 HH:MM（24 小时制），当前为：{value!r}"
        )
    return int(match.group(1)), int(match.group(2))


def parse_fetch_minutes(value: str) -> int:
    try:
        minutes = int(value)
    except ValueError as exc:
        raise SystemExit(f"HOTROBIN_FETCH_MINUTES 应为整数，当前为：{value!r}") from exc
    if minutes < 1:
        raise SystemExit("HOTROBIN_FETCH_MINUTES 至少为 1")
    return minutes


def next_summary(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def next_fetch_tick(
    now: datetime, hour: int, minute: int, interval_minutes: int
) -> datetime:
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if base > now:
        base -= timedelta(days=1)
    step = interval_minutes * 60
    elapsed = (now - base).total_seconds()
    return base + timedelta(seconds=(int(elapsed // step) + 1) * step)


def last_ended_day(now: datetime, day_start_hour: int) -> str:
    current = logical_day(now.isoformat(), day_start_hour)
    return (date.fromisoformat(current) - timedelta(days=1)).isoformat()


def _ancestor_pids() -> set[int]:
    ancestors: set[int] = set()
    pid = os.getpid()
    while pid > 1:
        ancestors.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            fields = stat.rsplit(")", 1)[1].strip().split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return ancestors


def _is_scheduler_cmdline(parts: list[str]) -> bool:
    if not parts or "python" not in Path(parts[0]).name.lower():
        return False
    return any(
        Path(part).name == "hotrobin" and parts[index + 1] == "schedule"
        for index, part in enumerate(parts[:-1])
    )


def find_other_schedulers() -> list[tuple[int, str]]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []

    excluded = _ancestor_pids()
    current_dir = Path.cwd().resolve()
    found: list[tuple[int, str]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in excluded:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            cwd = Path(os.readlink(entry / "cwd")).resolve()
        except OSError:
            continue
        parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if cwd != current_dir or not _is_scheduler_cmdline(parts):
            continue
        found.append((pid, " ".join(parts)))
    return found


def _run_snapshot(data_dir: Path, day_start_hour: int) -> Path | None:
    print(f"[{datetime.now(SHANGHAI_TZ).isoformat(timespec='minutes')}] 抓取热榜")
    try:
        return run_snapshot(data_dir, day_start_hour=day_start_hour)
    except (SystemExit, Exception) as exc:
        record_failure(None, "snapshot", str(exc), data_dir=data_dir)
        print(f"抓取失败：{exc}")
        return None


def _run_summary(
    data_dir: Path,
    out_dir: Path,
    day: str,
    day_start_hour: int,
    *,
    boundary_path: Path | None = None,
) -> None:
    print(f"[{datetime.now(SHANGHAI_TZ).isoformat(timespec='minutes')}] 生成 {day} 日报")
    try:
        run_daily_summary(
            data_dir,
            out_dir,
            day,
            day_start_hour=day_start_hour,
            boundary_path=boundary_path,
        )
    except (SystemExit, Exception) as exc:
        print(f"日报生成失败（{day}）：{exc}")


def run_scheduler(data_dir: Path, out_dir: Path) -> None:
    sys.stdout.reconfigure(line_buffering=True)

    others = find_other_schedulers()
    if others:
        print("警告：检测到其他 hotrobin schedule 进程，可能导致重复抓取和发布：")
        for pid, cmdline in others:
            print(f"  PID {pid}: {cmdline[:160]}")
        print('  请只保留一个；多余的可执行 pkill -f "hotrobin schedule" 后重启。')

    summary_hour, summary_minute = parse_summary_at(
        os.environ.get("HOTROBIN_SUMMARY_AT", DEFAULT_SUMMARY_AT)
    )
    interval = parse_fetch_minutes(
        os.environ.get("HOTROBIN_FETCH_MINUTES", str(DEFAULT_FETCH_MINUTES))
    )
    print(
        f"定时任务已启动：每 {interval} 分钟抓取热榜；"
        f"每天 {summary_hour:02d}:{summary_minute:02d}（Asia/Shanghai）"
        "生成上一逻辑日日报并发布"
    )
    if (24 * 60) // interval + 1 > 100:
        print("提示：当前间隔每天抓取超过 100 次，可能超出热榜免费额度。")

    now = datetime.now(SHANGHAI_TZ)
    missed_day = last_ended_day(now, summary_hour)
    missed_dir = day_directory(data_dir, "zhihu", missed_day)
    if (missed_dir / "snapshots").is_dir() and not (missed_dir / "report.json").exists():
        print(f"补做漏掉的日报：{missed_day}")
        _run_summary(data_dir, out_dir, missed_day, summary_hour)

    while True:
        now = datetime.now(SHANGHAI_TZ)
        summary_at = next_summary(now, summary_hour, summary_minute)
        fetch_at = next_fetch_tick(
            now, summary_hour, summary_minute, interval
        )
        target = min(summary_at, fetch_at)
        print(
            f"下次任务：{target.isoformat(timespec='minutes')} "
            f"（{'日报+快照' if summary_at <= fetch_at else '快照'}）"
        )

        while True:
            remaining = (target - datetime.now(SHANGHAI_TZ)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 30))

        current = datetime.now(SHANGHAI_TZ)
        if summary_at <= fetch_at:
            # The boundary snapshot belongs to the new day and is also used
            # as the closing point of the day being summarized.
            boundary = _run_snapshot(data_dir, summary_hour)
            day = last_ended_day(current, summary_hour)
            _run_summary(
                data_dir, out_dir, day, summary_hour, boundary_path=boundary
            )
        else:
            _run_snapshot(data_dir, summary_hour)
        time.sleep(1)
