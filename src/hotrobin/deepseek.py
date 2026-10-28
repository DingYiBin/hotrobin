"""Minimal DeepSeek client (OpenAI-compatible chat completions).

Reads configuration from the environment:

- ``DEEPSEEK_API_KEY`` (required)
- ``DEEPSEEK_BASE_URL`` (default ``https://api.deepseek.com``)
- ``DEEPSEEK_MODEL`` (default ``deepseek-chat``)
- ``DEEPSEEK_MAX_TOKENS`` (default 8192; reasoning models count reasoning here)
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_MAX_TOKENS = 8192
REQUEST_TIMEOUT = 240


def model_name() -> str:
    return os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)


def _max_tokens() -> int:
    try:
        return int(os.environ.get("DEEPSEEK_MAX_TOKENS", DEFAULT_MAX_TOKENS))
    except ValueError:
        return DEFAULT_MAX_TOKENS


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def chat_json(system: str, user: str) -> dict[str, Any]:
    """Call the model and return its JSON output."""
    base = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY，请在 .env 中配置")

    payload = {
        "model": model_name(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": _max_tokens(),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload).encode("utf-8")

    for attempt in range(3):
        request = urllib.request.Request(
            base + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()[:300].decode("utf-8", "replace")
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise SystemExit(f"DeepSeek 请求失败：HTTP {exc.code} {raw}") from exc
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise SystemExit(f"DeepSeek 请求失败：{exc.reason}") from exc

        try:
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content") or ""
        except (KeyError, IndexError) as exc:
            raise SystemExit("DeepSeek 返回结构异常") from exc

        if not content:
            finish = choice.get("finish_reason")
            if attempt < 1:
                continue
            raise SystemExit(
                f"DeepSeek 返回空内容（finish_reason={finish}）。"
                "如为 length，可调高 DEEPSEEK_MAX_TOKENS 或改用非推理模型。"
            )

        try:
            return _parse_json(content)
        except json.JSONDecodeError:
            if attempt < 2:
                continue
            raise SystemExit(f"DeepSeek 输出不是合法 JSON：{content[:200]}")

    raise SystemExit("DeepSeek 调用失败")
