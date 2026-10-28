# hotrobin

每天定时抓取并总结社交平台热点，优先支持知乎，后续扩展到微博等平台。

## 环境

使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖，便于各方复现：

```bash
uv sync          # 创建 .venv 并安装依赖
```

Python 版本锁定在 `.python-version`（>=3.12），依赖版本由 `uv.lock` 锁定。

## 配置

复制 `.env.example` 为 `.env` 并填入：

- `ZHIHU_ACCESS_SECRET`：知乎开放平台个人中心生成，用于官方热榜和回答摘要接口
- `DEEPSEEK_API_KEY`：DeepSeek 平台 API Key，用于情绪分析
- 可选：`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`DEEPSEEK_MAX_TOKENS`

`.env` 已被 gitignore，CLI 启动时会自动读取当前目录的 `.env`。

## 使用

```bash
uv run hotrobin daily   # 热榜 -> 回答摘要 -> 情绪报告 -> 构建站点
uv run hotrobin serve   # 默认 http://127.0.0.1:20301/，局域网可用本机 IP 访问
```

分步执行：`fetch`（官方热榜）、`answers`（每题回答摘要）、`report`（DeepSeek 情绪分析）、`build`（渲染站点）。

## 定时

在 `.env` 中设置 `HOTROBIN_DAILY_AT`（24 小时制 `HH:MM`，北京时间，默认 `07:30`），然后常驻运行：

```bash
uv run hotrobin schedule
```

例如 `HOTROBIN_DAILY_AT=07:30` 表示每天 07:30 执行一次 `daily`。进程需要保持运行（nohup、tmux 或 systemd）；WSL 重启后需重新启动。

## 数据

- `data/zhihu/<日期>/snapshot.json`：热榜快照
- `data/zhihu/<日期>/answers/<问题ID>.json`：回答摘要
- `data/zhihu/<日期>/report.json`：逐题情绪分析与当日汇总，含每次模型调用的 token 用量

数据不进入 git，只保存在运行机器上。`site/` 是生成产物，同样不入库。`serve` 默认监听 `0.0.0.0`（局域网可访问），仅本机访问用 `--host 127.0.0.1`。

## 状态

已完成：官方热榜抓取、回答摘要、每日情绪报告（逐题 + 汇总）和静态站点浏览。定时调度待实现。
