"""Built-in daily scheduler configured through ``HOTROBIN_DAILY_AT``.

The value uses 24-hour ``HH:MM`` in Asia/Shanghai, for example ``07:30``.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .models import SHANGHAI_TZ
from .pipeline import run_daily

DEFAULT_DAILY_AT = "07:30"
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_daily_at(value: str) -> tuple[int, int]:
    match = TIME_RE.match(value.strip())
    if not match:
        raise SystemExit(
            f"HOTROBIN_DAILY_AT 格式应为 HH:MM（24 小时制，如 07:30），当前为：{value!r}"
        )
    return int(match.group(1)), int(match.group(2))


def next_run(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now:
        target += timedelta(days=1)
    return target


def run_scheduler(data_dir: Path, out_dir: Path, *, force: bool = False) -> None:
    # Make progress visible when stdout is redirected to a log file (nohup/systemd).
    sys.stdout.reconfigure(line_buffering=True)
    hour, minute = parse_daily_at(os.environ.get("HOTROBIN_DAILY_AT", DEFAULT_DAILY_AT))
    print(
        f"定时任务已启动：每天 {hour:02d}:{minute:02d}（Asia/Shanghai）运行 daily，"
        "Ctrl+C 退出"
    )

    while True:
        target = next_run(datetime.now(SHANGHAI_TZ), hour, minute)
        print(f"下次运行：{target.isoformat(timespec='minutes')}")

        while True:
            remaining = (target - datetime.now(SHANGHAI_TZ)).total_seconds()
            if remaining <= 0:
                break
            # Wake up periodically so that clock changes and host sleep are handled.
            time.sleep(min(remaining, 30))

        try:
            run_daily(data_dir, out_dir, force=force)
        except SystemExit as exc:
            print(f"本轮失败（SystemExit）：{exc}")
        except Exception as exc:  # keep the scheduler alive across failures
            print(f"本轮失败：{exc!r}")
        time.sleep(1)
