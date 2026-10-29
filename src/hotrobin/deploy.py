"""Publish the generated site with a configured command (e.g. Wrangler)."""

from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

from .models import SHANGHAI_TZ

DEPLOY_TIMEOUT = 300


def deploy_site(site_dir: Path) -> bool:
    """Run ``HOTROBIN_DEPLOY_CMD`` if configured.

    ``{site}`` is replaced with the absolute site directory and ``{time}``
    with the current Asia/Shanghai timestamp. Returns ``True`` if a command
    ran successfully, ``False`` if deployment is not configured.
    """
    template = os.environ.get("HOTROBIN_DEPLOY_CMD", "").strip()
    if not template:
        return False

    command = template.replace("{site}", str(site_dir.resolve()))
    command = command.replace(
        "{time}", datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
    )
    parts = shlex.split(command)
    if not parts:
        return False

    print(f"发布站点：{parts[0]} …")
    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=DEPLOY_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"发布命令不存在：{parts[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"发布超时（{DEPLOY_TIMEOUT} 秒）") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(
            f"发布失败（退出码 {result.returncode}）：{detail[-500:]}"
        )

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if lines:
        print(f"发布完成：{lines[-1]}")
    else:
        print("发布完成")
    return True
