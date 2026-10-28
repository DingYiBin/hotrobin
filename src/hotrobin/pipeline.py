"""The end-to-end daily pipeline shared by ``daily`` and ``schedule``."""

from __future__ import annotations

from pathlib import Path

from .build import build_site
from .models import save_snapshot
from .official import collect_answers, fetch_hot_list
from .report import generate_report


def run_daily(data_dir: Path, out_dir: Path, *, force: bool = False) -> None:
    """Fetch the hot list, collect answers, build the report, render the site."""
    snapshot = fetch_hot_list()
    path = save_snapshot(snapshot, data_dir)
    print(f"已抓取 {len(snapshot.items)} 条热榜，保存到 {path}")

    fetched, skipped, failed = collect_answers(
        data_dir, date=snapshot.date, force=force
    )
    print(f"回答摘要：新增 {fetched}，跳过 {skipped}，失败 {failed}")

    report_path = generate_report(data_dir, snapshot.date, force=force)
    print(f"报告：{report_path}")

    count = build_site(data_dir, out_dir)
    print(f"已生成 {count} 个快照页面 -> {out_dir}")
