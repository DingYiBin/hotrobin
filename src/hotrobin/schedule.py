"""Built-in daily scheduler configured through ``HOTROBIN_DAILY_AT``.

The value is one or more 24-hour ``HH:MM`` times in Asia/Shanghai separated
by commas, for example ``07:30`` or ``08:00,20:00``.
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
FULL_RUN_ANSWER_CALLS = 30
FREE_DAILY_ANSWER_CALLS = 100


def parse_daily_times(value: str) -> list[tuple[int, int]]:
    times: list[tuple[int, int]] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        match = TIME_RE.match(part)
        if not match:
            raise SystemExit(
                "HOTROBIN_DAILY_AT 格式应为 HH:MM（24 小时制，多个时间用逗号分隔），"
                f"当前为：{value!r}"
            )
        times.append((int(match.group(1)), int(match.group(2))))
    if not times:
        raise SystemExit("HOTROBIN_DAILY_AT 不能为空")
    return sorted(set(times))


def next_run(now: datetime, times: list[tuple[int, int]]) -> datetime:
    candidates = []
    for hour, minute in times:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1)
        candidates.append(target)
    return min(candidates)


def run_scheduler(data_dir: Path, out_dir: Path) -> None:
    # Make progress visible when stdout is redirected to a log file (nohup/systemd).
    sys.stdout.reconfigure(line_buffering=True)
    times = parse_daily_times(os.environ.get("HOTROBIN_DAILY_AT", DEFAULT_DAILY_AT))
    pretty = "、".join(f"{hour:02d}:{minute:02d}" for hour, minute in times)
    print(f"定时任务已启动：每天 {pretty}（Asia/Shanghai）运行 daily，Ctrl+C 退出")

    max_runs = FREE_DAILY_ANSWER_CALLS // FULL_RUN_ANSWER_CALLS
    if len(times) > max_runs:
        print(
            f"提示：每次完整运行约消耗 {FULL_RUN_ANSWER_CALLS} 次回答摘要额度，"
            f"每日免费额度 {FREE_DAILY_ANSWER_CALLS} 次，建议每天不超过 {max_runs} 次。"
        )

    while True:
        target = next_run(datetime.now(SHANGHAI_TZ), times)
        print(f"下次运行：{target.isoformat(timespec='minutes')}")

        while True:
            remaining = (target - datetime.now(SHANGHAI_TZ)).total_seconds()
            if remaining <= 0:
                break
            # Wake up periodically so that clock changes and host sleep are handled.
            time.sleep(min(remaining, 30))

        try:
            run_daily(data_dir, out_dir)
        except SystemExit as exc:
            print(f"本轮失败（SystemExit）：{exc}")
        except Exception as exc:  # keep the scheduler alive across failures
            print(f"本轮失败：{exc!r}")
        time.sleep(1)
