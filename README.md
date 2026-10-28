# hotrobin

每天定时抓取并总结社交平台热点，优先支持知乎，后续扩展到微博等平台。

## 环境

使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖，便于各方复现：

```bash
uv sync          # 创建 .venv 并安装依赖
uv run hotrobin  # 运行
```

Python 版本锁定在 `.python-version`（>=3.12），依赖版本由 `uv.lock` 锁定。

## 状态

项目初始化阶段，当前仅包含基础脚手架。
