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

`--run` 可以用完整路径（如 `2026/10/2026-10-28-214503`）或只写运行名。

## 定时

在 `.env` 中设置 `HOTROBIN_DAILY_AT`（24 小时制 `HH:MM`，北京时间，默认 `07:30`），多个时间用逗号分隔：

```bash
HOTROBIN_DAILY_AT=08:00,20:00
```

然后常驻运行：

```bash
uv run hotrobin schedule
```

每次触发都会新建一次运行。注意回答摘要免费额度为每日 100 次，一次完整运行约 30 次，建议每天不超过 3 次。进程需要保持运行（nohup、tmux 或 systemd）；WSL 重启后需重新启动。启动时会检查同项目的其他 `hotrobin schedule` 进程，发现重复会打印警告（不会自动退出）。

## 发布

配置 `HOTROBIN_DEPLOY_CMD` 后，`daily` / `schedule` 在构建完成后会自动发布：

```bash
HOTROBIN_DEPLOY_CMD="wrangler deploy --name hotrobin --assets {site} --compatibility-date 2026-10-29 --keep-vars"
```

`{site}` 会替换为 `site/` 的绝对路径。发布失败会写入 `failures.json` 并触发通知；本地站点不受影响。发布后页面公网可访问。

## 趋势

`build` 会生成 `site/zhihu/trend.html`：

- 每个 `report.json` 作为时间轴上的一个点，同一天多次运行就是多个点，按真实时间间隔排列
- 上方是四种情绪的折线图，下方是 100% 堆叠面积图
- 相邻点间隔超过 36 小时会断线，不插值
- 首页有「查看情绪趋势」入口

## 失败处理

- 运行中的失败记录在运行目录的 `failures.json`；抓取热榜前就失败则记录到 `data/zhihu/failures/<时间>.json`
- 抓取回答前会查询 `question_discovery` 剩余额度：不足时只抓前 N 题；为 0 时跳过回答抓取并记录原因
- 抓取回答时，主循环不区分错误类型：除致命码外，失败都进入重试队列，并继续抓其他题
- 命中 `20001` / `30002` / `30003` 时立即中止本轮，不重试；`30001` 会再查一次额度，区分「当日额度耗尽」和「短时频率/并发限制」后中止
- 延后阶段最多 3 轮：每轮对每个待重试题先等待（3 秒 × 轮次）再尝试；3 轮后仍失败才记录
- 配置 `HOTROBIN_NOTIFY_WEBHOOK` 后，每轮有失败会发送一条通知（支持 Server酱、Slack/Discord incoming webhook；其他地址按 `{"text": "..."}` 发送）
- 需要时可对同一运行重跑 `answers` / `report`

## 数据

每次运行一个独立目录，按年/月归档，例如 `data/zhihu/2026/10/2026-10-28-214503/`：

- `snapshot.json`：热榜快照
- `answers/<问题ID>.json`：回答摘要
- `report.json`：逐题情绪分析与当日汇总，含每次模型调用的 token 用量

生成的站点结构：

- `site/index.html`：首页
- `site/zhihu/<年>/<月>/<运行>.html`：热点页
- `site/zhihu/<年>/<月>/<运行>-report.html`：情绪日报

数据不进入 git，只保存在运行机器上。`site/` 是生成产物，同样不入库。`serve` 默认监听 `0.0.0.0`（局域网可访问），仅本机访问用 `--host 127.0.0.1`。

## 状态

已完成：官方热榜抓取、回答摘要、情绪报告（逐题 + 汇总）、定时调度和静态站点浏览。
