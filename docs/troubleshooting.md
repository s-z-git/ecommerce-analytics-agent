# 环境与排错

> 这是本机开发环境的排错记录。公开仓库不要求使用下面的绝对路径。

## 1. Python 环境

项目使用 Python 3.11，独立的 conda 环境 `commerce`：

```powershell
conda create -n commerce python=3.11 -y
conda activate commerce
pip install -r requirements.txt
```

本机绝对解释器：

```text
python
```

PowerShell 里如果 `conda` 未初始化：

```powershell
& "F:\Conda\Scripts\conda.exe" shell.powershell hook | Out-String | Invoke-Expression
conda activate commerce
```

## 2. 数据目录

默认路径：

```text
data/raw/         6 个 Olist CSV
data/warehouse/   olist.duckdb
logs/             运行 trace
```

可以用环境变量覆盖：

```powershell
$env:RAW_DIR = "F:\VS Code\电商经营分析Agent项目\data\raw"
$env:DB_PATH = "F:\VS Code\电商经营分析Agent项目\data\warehouse\olist.duckdb"
```

## 3. 常见报错

### `缺少 CSV`

把 Olist 的 6 个 CSV 放到 `data/raw/`，文件名必须是：

```text
olist_orders_dataset.csv
olist_order_items_dataset.csv
olist_products_dataset.csv
olist_customers_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
```

然后运行：

```powershell
python scripts/build_db.py
```

### `数据库不存在`

先运行：

```powershell
python scripts/build_db.py
```

### `NO_API_KEY`

在项目根目录 `.env` 写：

```text
DEEPSEEK_API_KEY=你的 key
```

或在当前 PowerShell 会话里设置：

```powershell
$env:DEEPSEEK_API_KEY = "你的 key"
```

UI 侧栏会在 key 缺失时常驻提示；页面不会白屏，但提问会失败。

### 查询超时 / 内存不足

执行层限制在 `src/config.py`，可用环境变量覆盖：

```powershell
$env:SQL_TIMEOUT_SECONDS = "10"
$env:MAX_ROWS = "1000"
$env:MEMORY_LIMIT = "2GB"
```

## 3.1 UI 会话库（2026-11-13）

- `data/ui_sessions.db` 存 `sessions` + `messages(meta_json)`；删除该文件可重置全部会话。
- 侧栏的「未保存」是当前临时会话；首问后才落库，只打开页面不产生空会话。
- 页面 URL 的 `?s=<session_id>` 可恢复；换一个 id 等于新会话。
- UI 只做多轮回放；每次查询语义仍是单轮，不做上下文消解。

## 4. 公开仓库注意

- `data/raw/*.csv`、`data/warehouse/*.duckdb`、`logs/` 不入库。
- `.env` 不入库。
- 公开仓库不需要本机绝对路径；命令里的 `python` 指你的环境解释器。
- 发布快照前检查 `git ls-files` 里没有内部文档和 `templates/`。

## 5. 没有数据库时跑测试

公开仓库不包含 `data/`，直接跑 `pytest -q` 时会跳过依赖数据库的集成测试，预期是：

```text
114 passed, 11 skipped
```

要跑完整的 125 条测试，先把 6 个 CSV 放进 `data/raw/` 并运行 `python scripts/build_db.py`。

## 6. CI

GitHub Actions 会跑 `pytest -q`。公开仓库没有 `data/`，CI 预期是 114 passed / 11 skipped；只有检测到 `data/warehouse/olist.duckdb` 时才继续跑 `scripts/check_metrics.py`。
