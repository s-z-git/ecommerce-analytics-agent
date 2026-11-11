# 设计蓝图 — 电商经营分析 Agent（受约束 NL2SQL）

> 更新：2026-11-08 ｜ 目标：可复现的受约束 NL2SQL 应用 ｜ 数据：Olist 公开电商数据集
> 本文件是设计底稿。改设计先改这里。

---

## 0. 一句话

用中文问电商经营问题，系统输出**受约束的查询计划**，由**指标契约**渲染成只读 SQL，在 DuckDB 上执行并给出结果、口径说明与失败原因。

**核心不是"让 LLM 写 SQL"，而是"用确定性工程约束不确定的模型输出"。**

> 产品名保留「电商经营分析 Agent」，但它不是自主多 Agent 系统，而是受约束的 NL2SQL + 语义层应用。

---

## 1. 目标与非目标

### 目标（P0）
1. 12 个指标、6 张表、30 条评测题（22 answer + 3 clarify + 5 unsupported）能跑通并给出正确结果
2. 危险请求（DDL / 读文件 / 超范围）由 guard 单元用例 + 端到端注入用例两层验证
3. 歧义问题能追问而不是瞎猜
4. 全链路有 trace：plan → SQL → 行数 → 耗时 → 错误码

### 非目标（明确不做）
多轮长对话、上传任意 CSV、多租户/登录、复杂窗口函数与 CTE 大杂烩、因果归因、预测/推荐、多 Agent、向量库查 schema、公网部署、微调、K8s/微服务/Kafka/ES。

---

## 2. 数据

Olist 巴西电商公开数据集（2016-09-04 ~ 2018-10-17，BRL）。**不是跨境电商**——没有海关、多币种、国家仓。对外说明只能写"指标契约可迁移到跨境电商场景"。

| 表 | 关键字段 | 陷阱 |
|---|---|---|
| orders | order_id, customer_id, status, purchase_ts, delivered_ts, estimated_ts | 状态含 canceled / unavailable / 未送达 |
| order_items | order_id, item_id, product_id, seller_id, price, freight | **一对多**，直接 join 会放大金额 |
| products | product_id, category | 品类为葡语，需译名表；有 null |
| customers | customer_id, **customer_unique_id**, state | `customer_id` 每单都变，复购必须用 unique_id |
| order_payments | order_id, payment_type, payment_value | **一对多**（一单多支付方式） |
| order_reviews | order_id, review_score | **一对多**（可能多评） |

**两条铁律**
1. 子表先聚合到 `order_id` 粒度，再和 orders join（`ctes` 里的预聚合 CTE）
2. 时间锚点 `as_of = max(order_purchase_timestamp)`，**不用 today()**

---

## 3. 链路

```
用户问题
  → [1] 意图解析：出 QueryPlan(JSON) 或 clarify / unsupported
  → [2] 契约校验：指标存在？维度允许？时间在范围内？
  → [3] 渲染 SQL：指标契约 + 维度 + 过滤 + 时间 → SQL（唯一出口）
  → [4] 静态校验：sqlglot 解析 + 语句类型白名单 + 函数白名单
  → [5] 执行：DuckDB 只读连接 + 行数/超时/内存上限
  → [6] 结果检查：空结果 / 负数 / 行数异常 → 分类返回
  → [7] 输出：表格 + 口径卡片 + trace
```

五个节点，手写状态机（不用 LangGraph，理由见 §8）。

> 严格说“唯一出口”有一个例外：`gmv_mom` 的 SQL 在 `metrics.yaml` 里硬编码（LAG + date spine 补月，通用拼装表达不了）。代价是改口径要改 SQL。这破坏了“唯一出口”的严格说法，作为已知设计债主动列出。

---

## 4. 五个核心设计

### 4.1 QueryPlan（唯一输入契约）

```python
QueryPlan(
  metrics: ["gmv"],
  dimensions: ["state"],
  filters: [Filter(field="state", op="=", value="SP")],
  time_range: TimeRange(start="2018-01-01", end="2018-06-30", granularity="month"),
  order_by: [OrderBy(field="gmv", desc=True)],
  limit: 10,
)
```

**里层不允许出现任何 SQL 字符串字段。** 这是整个安全模型的前提。

### 4.2 指标契约（不只是 SQL 模板）

每条指标声明：`grain`（输出粒度）、`time_field`、`base_filter`、`ctes`（子表预聚合 CTE）、`expr`、`additivity`、`allowed_dimensions`、`allowed_filters`、`clarifications`。

**"语义层"的价值在 grain / 预聚合 / 口径统一**，不在于"存了一条 SQL"。

### 4.3 grain 与防重复计数

GMV 用 `SUM(order_items.price)`；若和 payments、reviews 同时 join，行数会被放大 N 倍。
解法：每个子表先在 CTE 里聚合到 `order_id`，主查询只 join 聚合结果。

### 4.4 时间锚点

数据止于 2018-10-17。用户说"上个月"时按 2026 年算会得到空结果——这是**错误**，不是"没有数据"。
`as_of` 锚定数据最大日期，UI 与回答里显式写"数据截至 2018-10-17"。

### 4.5 安全威胁模型（不是"防 SQL 注入"）

LLM 不写 SQL，所以不存在传统注入。真实威胁：

| 威胁 | 防护 |
|---|---|
| prompt injection 让模型输出危险计划 | plan 里只有结构化字段，白名单校验 |
| 越权读文件（`read_csv` / `ATTACH` / `COPY`） | 函数白名单 + DuckDB `read_only=True` |
| 资源耗尽（笛卡尔积、超大结果） | 强制 LIMIT、`memory_limit`、超时 |
| 数据外泄 | 列白名单；MCP 的 query 只收自然语言 |

**对外表述用 "defense-in-depth"，不用 "injection-proof"。**

---

## 5. 十二个指标

| # | 指标 | 优先级 | 口径要点 |
|---|---|---|---|
| 1 | GMV | P0 | `SUM(items.price)`，不含运费，排除 canceled/unavailable |
| 2 | 订单数 | P0 | `COUNT(DISTINCT order_id)`，与 GMV 同一过滤集合 |
| 3 | 客单价 | P0 | GMV ÷ 订单数，分子分母同集合 |
| 4 | 取消率 | P0 | canceled ÷ **全部订单**（分母与 GMV 不同，必须写明） |
| 5 | 延迟率 | P0 | 实际送达 > 预计送达 ÷ **已送达** |
| 6 | 平均配送天数 | P0 | 送达 − 下单，仅已送达，排除负值 |
| 7 | 品类收入 TopN | P0 | 收入口径同 GMV；null 归"未分类"；并列规则固定 |
| 8 | 卖家收入 TopN | P0 | 同上，维度 seller_id |
| 9 | 复购率 | P0 | ≥2 单的 `customer_unique_id` ÷ 总数 |
| 10 | GMV 环比 | P0 | 只取完整自然月；基期为 0 时说明不可比 |
| 11 | 平均评分 | P1 | 评价粒度；一单多评先按订单取均值 |
| 12 | 支付方式占比 | P1 | 按金额占比；一单多支付先聚合 |

**2026-11-05 定稿口径（实测）**：

- GMV **不含运费**（含运费会多约 16.64%）
- 取消率：分子只算 canceled，分母全部订单（0.63%；canceled + unavailable 为 1.24%）
- 延迟率：分母 = 已送达订单（8.11%）
- 复购率：按 `customer_unique_id`（有有效订单客户 3.04%；全客户 3.12%）
- 支付占比：按支付金额（信用卡 78.47%、boleto 17.96%）

**口径差异本身就是设计重点**：取消率分母 ≠ GMV 分母 ≠ 延迟率分母。

---

## 6. 评测

- **30 条功能集**：22 条功能题（单指标 5 / 分组 5 / TopN 4 / 比率 4 / 环比与多期 4）+ 3 条歧义题（期望澄清）+ 5 条不支持题（期望拒答），与 `eval/questions.functional.jsonl` 当前内容一致
- **8 条 guard 单元用例 + 5 条端到端注入用例**：单元层测 `guard.check_sql()`；端到端层假设 LLM 已沦陷，测恶意 QueryPlan 走完整链路
- **12 条 acceptance set**（用户手写，含 gold SQL，开发期不看）——文件 `eval/questions.holdout.jsonl` 是早期命名，**不是 blind holdout**，不宣称泛化
- **当前实测**：功能集 3 轮 90/90、0 翻转；acceptance 12/12；5 个代表问题 5/5；单测 125 条覆盖契约校验 / SQL 编译 / guard 拦截 / HTTP 状态码映射四层；题集经另一模型独立审计（22 条 gold SQL 与编译器 22/22 一致）
- 判分：**执行结果等价**（忽略行列顺序、浮点容差、NULL 规则），不比 SQL 字符串
- 指标：功能通过率 / 澄清命中率 / 拦截率（全量 trace P95 4.0s / 896 条；30 题功能集 P95 3.7s / 30 条，见 docs/延迟与成本.md）

## 6.1 已知边界

- **单指标**：一次只查一个指标族，跨指标对比需要拆成多次提问。
- **只有 month / state 两个维度**：不支持按品类、卖家、支付方式等任意维度分组（TopN 指标自带固定分组）。
- **不支持预测 / 归因**：只做历史数据的确定性聚合，不做“下个月会怎样”或因果分析。
- **30 条功能集是自建回归题**，不是 blind holdout；12 条 acceptance set 也是手写验收题，不宣称泛化。
- **`gmv_mom` 是硬编码 SQL**：LAG + date spine 补月，通用拼装表达不了；改口径要改 SQL。
- **无多轮追问**：当前只处理单轮问题，不支持“那取消率呢”这类上下文消解。
- **数据截止 2018-10-17**：所有“最近/本月”都锚定这个日期，不随运行当天变化。

---

## 6.2 工程化质量

- `eval/run_eval.py` 在任意一轮失败时返回非 0，避免 CI 把失败当成功。
- `scripts/trace_stats.py` 支持 `--since/--until`（含带时区 ISO 输入）和 `--json`；`malformed` 明确为全文件计数。
- `summary.py` 对 `payment_share` / `cancel_rate` 出百分比图表，类别用横向条形，月份用折线；不画饼图。
- pipeline 结果携带 `additivity` 元数据，Top1 占比优先从 `metrics.yaml` 推导，不依赖新增硬编码集合。
- 计划解析失败也把已消耗的 `usage` 写入 trace。
- GitHub Actions：`pytest -q` 全量跑；有 DB 时再跑 `scripts/check_metrics.py`。

---

## 7. 里程碑与熔断

> 2026-11-09 实测：M1/M2 完成（5 问 5/5、acceptance 12/12、功能 3 轮 90/90、pytest 125）；MCP / Docker / UI / demo 脚本完成；剩发布前检查与演示录制。

| 日期 | 目标 | 未达标动作 |
|---|---|---|
| 11/6–11/8 | 数据入库 + 数据字典 + 12 条口径定稿 | 砍到 8 指标 |
| 11/10 | 5 个代表问题 5/5 通过 | 砍 P1 指标 |
| 11/12 | 8 条 guard 单元 + 5 条端到端注入 + 功能题 ≥16/22 | 砍 MCP |
| 11/14 | 功能题 ≥20/22 + Docker 只读挂载 + 3 分钟 demo | 砍 UI polish |
| 11/17 | 冻结：发布前检查 + 演示录制 | — |

砍的顺序：P1 指标 → MCP → UI polish。测试、README、demo 永不砍。

---


## 8. 已知取舍

| 取舍 | 理由 |
|---|---|
| 不用 LangGraph | 五步固定流程，手写状态机可控可调试；框架会隐藏控制流 |
| 不用向量库查 schema | 6 表 40 列，关键词+契约直查足够；向量召回会引入额外错误源 |
| 不让 LLM 写 SQL | 不可测、不可控；且本项目核心就是"约束模型" |
| 不做多轮 | 单轮 + 一次澄清已能覆盖 90% 场景；多轮会显著拉高评测复杂度 |
| 不部署公网 | 密钥/并发/冷启动两天起步，本轮不做 |


