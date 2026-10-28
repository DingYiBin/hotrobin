# hotrobin

每天定时抓取并总结社交平台热点，优先支持知乎，后续扩展到微博等平台。

## 环境

使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖，便于各方复现：

```bash
uv sync          # 创建 .venv 并安装依赖
```

Python 版本锁定在 `.python-version`（>=3.12），依赖版本由 `uv.lock` 锁定。

## 使用

```bash
uv run hotrobin fetch   # 抓取知乎热榜 -> data/
uv run hotrobin build   # 渲染 data/ -> site/
uv run hotrobin serve   # 打开 http://127.0.0.1:8000/
```

抓取快照保存在 `data/<平台>/<日期>/snapshot.json`，不进入 git，只保存在运行机器上。字段定义见 `src/hotrobin/models.py`。

`site/` 是生成产物，同样不入库。局域网访问：`uv run hotrobin serve --host 0.0.0.0`。

## 状态

项目初始化阶段：已完成知乎热榜抓取和静态站点浏览，定时调度与总结待实现。
