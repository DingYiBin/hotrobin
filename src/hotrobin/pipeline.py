"""The end-to-end daily pipeline shared by ``daily`` and ``schedule``."""

from __future__ import annotations

from pathlib import Path

from .build import build_site
from .deploy import deploy_site
from .failures import load_failures, notify_failures, record_failure
from .models import save_snapshot
from .official import collect_answers, fetch_hot_list
from .report import generate_report


def run_daily(data_dir: Path, out_dir: Path) -> None:
    """Fetch the hot list, collect answers, build the report, render the site.

    Failures are recorded in the run directory (or ``data/zhihu/failures/``
    before a run exists) and one webhook notification is sent per run.
    """
    run_dir: Path | None = None
    step = "fetch"
    try:
        snapshot = fetch_hot_list()
        path = save_snapshot(snapshot, data_dir)
        run_dir = path.parent
        print(
            f"已抓取 {len(snapshot.items)} 条热榜，保存到 {path}"
            f"（run {run_dir.name}）"
        )

        step = "answers"
        fetched, skipped, failed = collect_answers(run_dir)
        print(f"回答摘要：新增 {fetched}，跳过 {skipped}，失败 {failed}")

        step = "report"
        report_path = generate_report(run_dir)
        print(f"报告：{report_path}")

        step = "build"
        count = build_site(data_dir, out_dir)
        print(f"已生成 {count} 个快照页面 -> {out_dir}")

        step = "deploy"
        if deploy_site(out_dir):
            print("已发布到远端")
    except (SystemExit, Exception) as exc:
        if run_dir is None:
            failure_path = record_failure(None, step, str(exc), data_dir=data_dir)
            notify_failures(None, failure_path=failure_path)
        else:
            if not load_failures(run_dir):
                record_failure(run_dir, step, str(exc))
            notify_failures(run_dir)
        raise

    notify_failures(run_dir)
