"""Command line interface."""

from __future__ import annotations

import argparse
import functools
import http.server
from collections.abc import Sequence
from pathlib import Path

from .build import build_site
from .models import save_snapshot
from .zhihu import fetch_snapshot


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
    parser = argparse.ArgumentParser(
        prog="hotrobin", description="每日热点抓取、总结与浏览"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch", help="抓取知乎热榜并保存快照")
    fetch_parser.add_argument("--data-dir", type=Path, default=Path("data"))

    build_parser = sub.add_parser("build", help="把 data/ 渲染成静态站点")
    build_parser.add_argument("--data-dir", type=Path, default=Path("data"))
    build_parser.add_argument("--out", type=Path, default=Path("site"))

    serve_parser = sub.add_parser("serve", help="本地预览生成的站点")
    serve_parser.add_argument("--site-dir", type=Path, default=Path("site"))
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="局域网访问可设为 0.0.0.0"
    )
    serve_parser.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.command == "fetch":
        snapshot = fetch_snapshot()
        path = save_snapshot(snapshot, args.data_dir)
        print(f"已抓取 {len(snapshot.items)} 条，保存到 {path}")
    elif args.command == "build":
        count = build_site(args.data_dir, args.out)
        print(f"已生成 {count} 个快照页面 -> {args.out}")
    else:
        _serve(args.site_dir, args.host, args.port)


if __name__ == "__main__":
    main()
