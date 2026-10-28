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
uv run hotrobin daily   # 新建一次运行：热榜 -> 回答摘要 -> 情绪报告 -> 构建站点
uv run hotrobin serve   # 默认 http://127.0.0.1:20301/，局域网可用本机 IP 访问
```

每次 `fetch` / `daily` 都会新建一个按时间命名的运行目录（如 `2026-10-28-214503`），不同运行之间互不覆盖。分步执行：

```bash
uv run hotrobin fetch                 # 新建运行并抓取热榜
uv run hotrobin answers [--run ID]    # 抓取回答摘要，默认最新运行
uv run hotrobin report [--run ID]     # 生成情绪报告，默认最新运行
uv run hotrobin build                 # 渲染全部运行
```

## 定时

在 `.env` 中设置 `HOTROBIN_DAILY_AT`（24 小时制 `HH:MM`，北京时间，默认 `07:30`），多个时间用逗号分隔：

```bash
HOTROBIN_DAILY_AT=08:00,20:00
```

然后常驻运行：

```bash
uv run hotrobin schedule
```

每次触发都会新建一次运行。注意回答摘要免费额度为每日 100 次，一次完整运行约 30 次，建议每天不超过 3 次。进程需要保持运行（nohup、tmux 或 systemd）；WSL 重启后需重新启动。

## 失败处理

- 运行中的失败记录在运行目录的 `failures.json`；抓取热榜前就失败则记录到 `data/zhihu/failures/<时间>.json`
- 配置 `HOTROBIN_NOTIFY_WEBHOOK` 后，每轮有失败会发送一条通知（支持 Server酱、Slack/Discord incoming webhook；其他地址按 `{"text": "..."}` 发送）
- 不会自动重试；需要时可对同一运行重跑 `answers` / `report`

## 数据

每次运行一个独立目录，例如 `data/zhihu/2026-10-28-214503/`：

- `snapshot.json`：热榜快照
- `answers/<问题ID>.json`：回答摘要
- `report.json`：逐题情绪分析与当日汇总，含每次模型调用的 token 用量

数据不进入 git，只保存在运行机器上。`site/` 是生成产物，同样不入库。`serve` 默认监听 `0.0.0.0`（局域网可访问），仅本机访问用 `--host 127.0.0.1`。

## 状态

已完成：官方热榜抓取、回答摘要、情绪报告（逐题 + 汇总）、定时调度和静态站点浏览。
