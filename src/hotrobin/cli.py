"""Command line interface."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from .build import build_site
from .daily import run_daily_summary, run_snapshot
from .models import SHANGHAI_TZ, logical_day
from .schedule import parse_summary_at, run_scheduler


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs from .env without overriding the environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _day_start_hour() -> int:
    return parse_summary_at(os.environ.get("HOTROBIN_SUMMARY_AT", "04:00"))[0]


def _last_ended_day(day_start_hour: int) -> str:
    now = datetime.now(SHANGHAI_TZ).isoformat()
    current = logical_day(now, day_start_hour)
    return (date.fromisoformat(current) - timedelta(days=1)).isoformat()


def _serve(site_dir: Path, host: str, port: int) -> None:
    if not (site_dir / "index.html").exists():
        raise SystemExit(f"{site_dir}/index.html 不存在，先运行 hotrobin build")

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(site_dir)
    )
    with http.server.ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"预览地址：http://{host}:{port}/ （Ctrl+C 退出）")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv: Sequence[str] | None = None) -> None:
    sys.stdout.reconfigure(line_buffering=True)
    _load_dotenv()
    parser = argparse.ArgumentParser(
        prog="hotrobin", description="每日热点抓取、变化总结与浏览"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch", help="抓取一次热榜并保存快照")
    fetch_parser.add_argument("--data-dir", type=Path, default=Path("data"))

    summary_parser = sub.add_parser(
        "summary", help="生成某个逻辑日的热点变化日报"
    )
    summary_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    summary_parser.add_argument("--out", type=Path, default=Path("site"))
    summary_parser.add_argument(
        "--date", help="逻辑日 YYYY-MM-DD；默认最近一个已结束的逻辑日"
    )
    summary_parser.add_argument("--no-deploy", action="store_true")

    build_parser = sub.add_parser("build", help="把 data/ 渲染成静态站点")
    build_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    build_parser.add_argument("--out", type=Path, default=Path("site"))

    serve_parser = sub.add_parser("serve", help="本地预览生成的站点")
    serve_parser.add_argument("--site-dir", type=Path, default=Path("site"))
    serve_parser.add_argument(
        "--host", default="0.0.0.0", help="默认监听所有网卡；仅本机访问可设为 127.0.0.1"
    )
    serve_parser.add_argument("--port", type=int, default=20301)

    schedule_parser = sub.add_parser(
        "schedule", help="按 HOTROBIN_FETCH_MINUTES / HOTROBIN_SUMMARY_AT 定时运行"
    )
    schedule_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    schedule_parser.add_argument("--out", type=Path, default=Path("site"))

    args = parser.parse_args(argv)
    day_start_hour = _day_start_hour()

    if args.command == "fetch":
        run_snapshot(args.data_dir, day_start_hour=day_start_hour)
    elif args.command == "summary":
        day = args.date or _last_ended_day(day_start_hour)
        path = run_daily_summary(
            args.data_dir,
            args.out,
            day,
            day_start_hour=day_start_hour,
            deploy=not args.no_deploy,
        )
        print(f"日报：{path}")
    elif args.command == "build":
        count = build_site(args.data_dir, args.out)
        print(f"已生成 {count} 个日报页面 -> {args.out}")
    elif args.command == "schedule":
        run_scheduler(args.data_dir, args.out)
    else:
        _serve(args.site_dir, args.host, args.port)


if __name__ == "__main__":
    main()
