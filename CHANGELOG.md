# CHANGELOG

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
