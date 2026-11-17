# CHANGELOG

## 2026-11-16 — B5 双维多序列图（month + state）

- 后端本已支持 `month + state` 双维分组；B5 只改 UI，不改 compiler / metrics / QueryPlan。
- `summary.chart_spec` 新增 `multi_line`：时间维 + 类别维，按序列最大值取 Top8，caption 写明「前 8 / 共 N」；一维仍走原折线，三维/无时间维不画。
- `app.py` 显式增加 `multi_line` 渲染分支（此前会落入 `else` 被当成单序列折线）。
- 修复的真实 bug：B5 之前双维结果被画成锯齿折线（100 个点/6 个唯一 x）。
- 测试：137 → **142 passed**（有 DB）；无 DB **130 passed / 12 skipped**。2026-11-17 B4b 加 2 条测试后当前为 **144 passed**（无 DB **132 passed / 12 skipped**）。
- 不支持：多指标对比、category/seller 通用维度。

## 2026-11-16 — B4a 品类中文名 + compile_plan 独立维度校验

- 新增 `src/semantic/category_zh.yaml`：73 个品类码 → 中文名；`summary`/`kpi_cards` 展示层翻译，原始 `rows` 不变。
- `compile_plan` 增加独立守卫：`shape in (topn, series)` 且指定 dimensions → `DIMENSION_NOT_ALLOWED`；series 指定 filter → `FILTER_NOT_ALLOWED`。生产链先 validate 再 compile，本次是 defense-in-depth。
- 测试：129 → **137 passed**（有 DB）；无 DB **125 passed / 12 skipped**（新增 1 条品类码集合校验需要真实库）。
- 未做：TopN×时间叠加、通用 dimension、多指标。

## 2026-11-16 — 30 条冻结回归集（B3）

- 新增 `eval/questions.blind30.jsonl`：30 条（22 answer + 3 clarify + 5 unsupported），与 functional / holdout / dev 三套题**零语义重叠**；gold SQL 全部在 DuckDB 实跑验证非空；sha256 随题集提交（`2b1dd57`），跑前冻结。
- 结果：**两次运行 28/30、29/30**。**不是 blind holdout**（出题人见过 v1，v1 为 29/30 后修过 1 条出题缺陷），不与功能集 90/90 混算。
- 已知失败 2 条：① b12 单边时间范围「……之前」——`gold` 含题面未声明的下界（`>=2018-01-01`），题面字面义与全量结果逐位相同，属**题目/gold 歧义**，非模型错；② b07 跨多月区间问总量时按月分组——「2017 年下半年的客单价」在单值与按月分组间摇摆（**模型规划不稳定**，累计 10 次运行 9 次正确、1 次按月），不改为通过。
- 发现数据边界：**2018-09 全库仅 1 单**（SP），2018-09 之后按州提问基本为空。已写入 README 已知边界。
- **b07 归因依据**：两次运行之间 `src/`、`eval/` 的 diff 不含任何影响 aov 的改动（`metrics.yaml` 未改，新增的编译器守卫只作用于 topn/series，aov 是 grouped）；5 次重复里 `time_range` 5/5 相同、AOV 公式 5/5 相同，唯一变量是 `dimensions`（4 次 `[]`、1 次 `['month']`）→ 计划生成层不稳定。gold 的 `SUM(oi.price)` 与契约的 `SUM(i.gmv)` 因单表前聚合而数值等价（按月逐位吻合），不构成 gold 缺陷。

## 2026-11-15 — 真实 DuckDB 引擎集成测试（迷你库）

- 新增 `tests/test_db_integration.py` 4 条：tmp DuckDB 建 orders/order_items，跑编译器 SQL + 执行层，覆盖 gmv / orders / cancel_rate / DB_NOT_FOUND。
- 测试总数 125 → 129；无 DB 环境 114/11 → 118 passed / 11 skipped。

## 2026-11-13 — UI 改造：会话 + 业务 KPI + 状态卡

- 会话：`data/ui_sessions.db` 的 `sessions` + `messages(meta_json)`；首问才落库，`?s=` 刷新恢复，多轮消息回放。
- 结果：口径行 → 结论 → 3 个业务 KPI → 图 → 明细 / 口径 SQL；KPI 去掉「行数」，图最多 10 类并说明其余类已计入总量。
- 澄清按钮化（GMV / 支付金额 / 卖家收入）；unsupported / no_data / error 统一状态卡。
- 未配置 `DEEPSEEK_API_KEY` 时侧栏常驻提示。
- 测试：`pytest tests/ -q` 125 passed。

## 2026-11-09

- **架构审计修复**：`run_eval` 失败返回非 0；`trace_stats` 支持带时区输入并补单测；`payment_share` / `cancel_rate` 增加百分比图表；结果携带 `additivity` 元数据；计划解析失败也记录 token usage；新增 GitHub Actions CI。
- **CI 跨平台修复**：test_config_paths 按平台使用对应绝对路径，Ubuntu Actions 转绿。

## 2026-11-08

- **P0 UI 产品化**：首屏示例、Q→A、结论/KPI/图表/口径徽章、五状态、结果 session_state 持久化。
- **P1 延迟与成本**：trace 记录 usage / llm_ms / sql_ms；新增 `scripts/trace_stats.py` 和 `docs/延迟与成本.md`。
- **P2 端到端注入测试**：8 条 guard 单元 + 5 条端到端注入；新增 `docs/安全模型.md`。
- **P3 稳定性评测**：`eval/run_eval.py --repeat`；30 题 × 3 轮 90/90，0 翻转；新增 `docs/稳定性报告.md`。
- **P4 声明校准**：首屏改为受约束 NL2SQL + 语义层；补齐 acceptance 说明、四层测试覆盖、已知边界和 `gmv_mom` 设计债。
- **P5 发布收尾**：README 首屏、架构图、4 张 UI 截图、公开快照。
- **文档一致性校正**：统一测试与验收口径、acceptance 命名；README 增加公开仓库链接。

## 2026-11-05

- M1/M2 完成：12 条指标契约、6 张表建库、QueryPlan 状态机、FastAPI、Streamlit、MCP、Docker、demo 脚本。
- 功能集 30/30，acceptance 12/12，5 个代表问题 5/5。
