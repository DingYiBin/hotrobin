"""Command line interface."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
from collections.abc import Sequence
from pathlib import Path

from .build import build_site
from .failures import load_failures, notify_failures, record_failure
from .models import latest_run_dir, save_snapshot
from .official import collect_answers, fetch_hot_list
from .pipeline import run_daily
from .report import generate_report
from .schedule import run_scheduler


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


def _resolve_run(data_dir: Path, run: str | None) -> Path:
    base = data_dir / "zhihu"
    if run:
        candidate = base / run
        if (candidate / "snapshot.json").exists():
            return candidate
        matches = sorted(base.glob(f"*/*/{run}/snapshot.json"))
        if len(matches) == 1:
            return matches[0].parent
        if len(matches) > 1:
            raise SystemExit(f"找到多个同名运行，请用 年/月/运行名 指定：{run}")
        raise SystemExit(f"找不到运行目录：{run}")
    return latest_run_dir(data_dir)


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
    _load_dotenv()
    parser = argparse.ArgumentParser(
        prog="hotrobin", description="每日热点抓取、总结与浏览"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch", help="抓取官方知乎热榜并新建一次运行")
    fetch_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    fetch_parser.add_argument("--limit", type=int, default=30, help="热榜条数，最大 30")

    answers_parser = sub.add_parser("answers", help="抓取某次运行中问题的回答摘要")
    answers_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    answers_parser.add_argument(
        "--run", help="运行名或 年/月/运行名，如 2026/10/2026-10-28-214503；默认最新"
    )
    answers_parser.add_argument("--limit", type=int, default=50, help="每个问题的摘要数，最大 50")
    answers_parser.add_argument("--max-questions", type=int, default=30)

    report_parser = sub.add_parser("report", help="为某次运行生成情绪分析报告")
    report_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    report_parser.add_argument(
        "--run", help="运行名或 年/月/运行名；默认最新"
    )
    report_parser.add_argument("--batch-size", type=int, default=5)
    report_parser.add_argument("--answers-per-question", type=int, default=20)

    build_parser = sub.add_parser("build", help="把 data/ 渲染成静态站点")
    build_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    build_parser.add_argument("--out", type=Path, default=Path("site"))

    serve_parser = sub.add_parser("serve", help="本地预览生成的站点")
    serve_parser.add_argument("--site-dir", type=Path, default=Path("site"))
    serve_parser.add_argument(
        "--host", default="0.0.0.0", help="默认监听所有网卡；仅本机访问可设为 127.0.0.1"
    )
    serve_parser.add_argument("--port", type=int, default=20301)

    daily_parser = sub.add_parser(
        "daily", help="新建一次运行：抓取 -> 回答摘要 -> 情绪报告 -> 构建站点"
    )
    daily_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    daily_parser.add_argument("--out", type=Path, default=Path("site"))

    schedule_parser = sub.add_parser(
        "schedule", help="按 .env 的 HOTROBIN_DAILY_AT 每天定时新建一次运行"
    )
    schedule_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    schedule_parser.add_argument("--out", type=Path, default=Path("site"))

    args = parser.parse_args(argv)

    if args.command == "fetch":
        try:
            snapshot = fetch_hot_list(limit=args.limit)
            path = save_snapshot(snapshot, args.data_dir)
            print(
                f"已抓取 {len(snapshot.items)} 条热榜，保存到 {path}"
                f"（run {path.parent.name}）"
            )
        except (SystemExit, Exception) as exc:
            failure_path = record_failure(
                None, "fetch", str(exc), data_dir=args.data_dir
            )
            notify_failures(None, failure_path=failure_path)
            raise
    elif args.command == "answers":
        run_dir = _resolve_run(args.data_dir, args.run)
        try:
            fetched, skipped, failed = collect_answers(
                run_dir,
                limit=args.limit,
                max_questions=args.max_questions,
            )
            print(
                f"回答摘要（{run_dir.name}）：新增 {fetched}，"
                f"跳过 {skipped}，失败 {failed}"
            )
            notify_failures(run_dir)
        except (SystemExit, Exception) as exc:
            if not load_failures(run_dir):
                record_failure(run_dir, "answers", str(exc))
            notify_failures(run_dir)
            raise
    elif args.command == "report":
        run_dir = _resolve_run(args.data_dir, args.run)
        try:
            path = generate_report(
                run_dir,
                batch_size=args.batch_size,
                answers_per_question=args.answers_per_question,
            )
            print(f"报告：{path}")
            notify_failures(run_dir)
        except (SystemExit, Exception) as exc:
            if not load_failures(run_dir):
                record_failure(run_dir, "report", str(exc))
            notify_failures(run_dir)
            raise
    elif args.command == "build":
        count = build_site(args.data_dir, args.out)
        print(f"已生成 {count} 个快照页面 -> {args.out}")
    elif args.command == "daily":
        run_daily(args.data_dir, args.out)
    elif args.command == "schedule":
        run_scheduler(args.data_dir, args.out)
    else:
        _serve(args.site_dir, args.host, args.port)


if __name__ == "__main__":
    main()
