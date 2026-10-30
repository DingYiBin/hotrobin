# hotrobin

每天定时抓取知乎热榜，按 **04:00 日界线**采样，生成每日热点变化日报，并发布到 Cloudflare Worker。

## 环境

使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖：

```bash
uv sync
```

Python 版本锁定在 `.python-version`（>=3.12），依赖版本由 `uv.lock` 锁定。

## 配置

复制 `.env.example` 为 `.env` 并填入：

- `ZHIHU_ACCESS_SECRET`：知乎开放平台个人中心生成
- `DEEPSEEK_API_KEY`：DeepSeek API Key
- `HOTROBIN_FETCH_MINUTES`：热榜抓取间隔，默认 30 分钟
- `HOTROBIN_SUMMARY_AT`：每日日报时间，默认 `04:00`，同时定义逻辑日界线
- `HOTROBIN_DEPLOY_CMD`：构建后发布命令，可选
- `HOTROBIN_NOTIFY_WEBHOOK`：失败通知 webhook，可选

`.env` 已被 gitignore，CLI 启动时自动读取。

## 使用

```bash
uv run hotrobin schedule   # 常驻：间隔抓快照 + 每天 04:00 生成日报并发布
uv run hotrobin serve      # 本地预览 site/
```

分步执行：

```bash
uv run hotrobin fetch                  # 抓一次热榜快照（不分析、不发布）
uv run hotrobin summary                # 生成最近一个已结束逻辑日的日报
uv run hotrobin summary --date 2026-10-29
uv run hotrobin build                  # 重新渲染站点
```

## 定时与逻辑日

逻辑日 D = `[D 04:00, D+1 04:00)`：

- 白天每 `HOTROBIN_FETCH_MINUTES` 分钟抓一次热榜，只存快照
- 每天 `HOTROBIN_SUMMARY_AT` 生成**上一个逻辑日**的日报，构建并发布一次
- 04:00 的边界快照只抓一次，同时作为上一日的收尾点和新一日的起点；日报会包含它
- 启动时如果最近一个逻辑日漏做，会自动补一次
- 启动时会检测同项目的其他 `hotrobin schedule` 进程并打印警告

例如 `04:00` 的日报处理的是 `10-29 04:00` 到 `10-30 04:00` 的数据，日期记为 `2026-10-29`。

## 日报生成

1. 汇总逻辑日内所有快照（含次日 04:00 的边界快照），按问题 ID 去重
2. 评分 `Σ(1/rank)`，选出 Top 100
3. 回答抓取：
   - Top 1–10：抓第 1、2 页（每题最多 100 条摘要）
   - Top 11–30：抓第 1 页（每题最多 50 条摘要）
   - Top 31–100：不抓回答，只记录榜单指标
4. Top 30 逐题分析，再生成当日概览和议题
5. 计算相较昨日的分数/名次升降，并让模型总结日间变化
6. 产出新增 / 持续 / 退榜热点、较昨日变化、Top 100 表格和热点变化趋势

回答页失败时只重试对应页；额度不足时自动缩减分析题数，额度为 0 时仍保留榜单统计。

## 数据

```
data/zhihu/2026/10/2026-10-29/
├── snapshots/2026-10-29-0430.json
├── ...
├── top.json
├── seen.json
├── answers/<问题ID>.json
└── report.json
```

数据不进入 git，只保存在运行机器上。`site/` 是生成产物，同样不入库。

## 站点

- `site/index.html`：日报列表
- `site/zhihu/<年>/<月>/<逻辑日>.html`：日报
- `site/zhihu/trend.html`：新增/持续/退榜趋势与跨天持续话题

## 发布

`.env` 示例：

```bash
HOTROBIN_DEPLOY_CMD="wrangler deploy --name hotrobin --assets {site} --compatibility-date 2026-10-29 --keep-vars"
```

`{site}` 会替换为 `site/` 的绝对路径。发布失败会写入 `failures.json` 并触发通知。

## 失败处理

- 快照抓取失败：记录到 `data/zhihu/failures/`，只打印日志，不发送通知
- 日报失败：记录到逻辑日目录的 `failures.json`，每轮发送一次通知
- 回答抓取：致命码 `20001` / `30002` / `30003` 立即中止；`30001` 会查额度区分「当日额度耗尽」和「短时限流」后中止
- 失败页最多重试 3 轮，每轮等待时间递增；只重试失败的那一页

支持 Server酱、Slack、Discord incoming webhook；其他地址按 `{"text": "..."}` 发送。

## 状态

已完成：间隔快照、逻辑日、Top 100、分页回答抓取、热点变化日报、趋势页、定时调度、自动发布与失败通知。
