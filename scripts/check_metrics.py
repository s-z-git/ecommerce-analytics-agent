"""检查 metrics.yaml：语法、字段完整性、CTE/维度引用、编译和执行。

为什么需要这个脚本：
手写 YAML 最容易错的是缩进、漏字段、CTE 名字写错。
pytest 只会告诉你“某个测试挂了”，这个脚本直接告诉你“哪个指标、缺什么”。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import DB_PATH, METRICS_PATH  # noqa: E402
from src.exec import duck, guard  # noqa: E402
from src.plan.schema import QueryPlan, TimeRange  # noqa: E402
from src.semantic import compiler, registry  # noqa: E402

REQUIRED_COMMON = ["label", "unit", "grain", "shape", "time_field", "note"]
REQUIRED_GROUPED = ["from", "ctes", "base_filter", "additivity", "allowed_dimensions"]
VALID_SHAPES = {"grouped", "topn", "series"}
VALID_GRAINS = {"order", "order_item", "customer", "month"}


def load_yaml(path: Path) -> dict:
    """先单独解析 YAML；语法错时给出准确行号。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            print(f"YAML 语法错误：第 {mark.line + 1} 行，第 {mark.column + 1} 列")
            print(f"  {getattr(exc, 'problem', exc)}")
            print("提示：YAML 用 2 个空格缩进，不能用 Tab；同一层级缩进必须一致。")
        else:
            print(f"YAML 语法错误：{exc}")
        raise SystemExit(1)

    if not isinstance(data, dict):
        print("YAML 顶层必须是字典（key: value），现在不是。")
        raise SystemExit(1)
    return data


def check_contract(data: dict) -> list[str]:
    """只做结构和引用检查，不编译 SQL。"""
    errors: list[str] = []

    for key in ("version", "as_of_sql", "ctes", "dimensions", "metrics"):
        if key not in data:
            errors.append(f"缺少顶层字段：{key}")

    ctes = data.get("ctes") or {}
    dims = data.get("dimensions") or {}
    metrics = data.get("metrics") or {}

    if not isinstance(ctes, dict):
        errors.append("ctes 必须是字典")
    if not isinstance(dims, dict):
        errors.append("dimensions 必须是字典")
    if not isinstance(metrics, dict):
        errors.append("metrics 必须是字典")
        return errors

    if len(metrics) != 12:
        errors.append(f"指标数量是 {len(metrics)}，项目约定是 12（如果你正在临时删指标，可以先忽略）")

    for mid, metric in metrics.items():
        if not isinstance(metric, dict):
            errors.append(f"{mid}: 必须是一个字典")
            continue

        for key in REQUIRED_COMMON:
            if key not in metric:
                errors.append(f"{mid}: 缺少字段 {key}")

        shape = metric.get("shape")
        if shape != "series":
            for key in REQUIRED_GROUPED:
                if key not in metric:
                    errors.append(f"{mid}: 缺少字段 {key}")
        if shape not in VALID_SHAPES:
            errors.append(f"{mid}: shape={shape!r}，只能是 grouped / topn / series")

        for cte in metric.get("ctes") or []:
            if cte not in ctes:
                errors.append(f"{mid}: 引用了不存在的 CTE {cte!r}")

        if metric.get("grain") not in VALID_GRAINS:
            errors.append(f"{mid}: grain={metric.get('grain')!r}，只能是 {sorted(VALID_GRAINS)}")

        for dim in metric.get("allowed_dimensions") or []:
            if dim not in dims:
                errors.append(f"{mid}: 引用了不存在的维度 {dim!r}")

        allowed_filters = metric.get("allowed_filters", metric.get("allowed_dimensions", []))
        for dim in allowed_filters or []:
            if dim not in dims:
                errors.append(f"{mid}: allowed_filters 引用了不存在的维度 {dim!r}")
        if shape == "series" and allowed_filters:
            errors.append(f"{mid}: series 指标不能声明 allowed_filters")

        default_limit = metric.get("default_limit")
        if default_limit is not None and not (isinstance(default_limit, int) and 1 <= default_limit <= 1000):
            errors.append(f"{mid}: default_limit 必须是 1-1000 的整数")

        if shape == "topn" and not isinstance(metric.get("fixed_dimension"), dict):
            errors.append(f"{mid}: shape=topn 时必须有 fixed_dimension")

        if shape == "series":
            sql = metric.get("sql")
            if not sql:
                errors.append(f"{mid}: shape=series 时必须有 sql")
            else:
                for placeholder in ("{time_filter}", "{complete_month_start}", "{complete_month_end}"):
                    if placeholder not in sql:
                        errors.append(f"{mid}: series 的 sql 里必须有 {placeholder} 占位符")
            for key in ("complete_month_start", "complete_month_end"):
                if not metric.get(key):
                    errors.append(f"{mid}: shape=series 时必须有 {key}")
        elif not metric.get("expr"):
            errors.append(f"{mid}: shape={shape} 时必须有 expr")

    return errors


def sample_plan(mid: str, metric: dict) -> QueryPlan:
    """给每条指标造一个最小可执行计划。"""
    time_field = metric.get("time_field")
    time_range = None
    if time_field not in (None, "NULL"):
        if metric.get("shape") == "series":
            time_range = TimeRange(start="2017-01-01", end="2018-08-31")
        else:
            time_range = TimeRange(start="2018-01-01", end="2018-12-31")

    dims: list[str] = []
    if metric.get("shape") == "grouped" and metric.get("allowed_dimensions"):
        dims = [metric["allowed_dimensions"][0]]

    limit = 5 if metric.get("shape") == "topn" else 100
    return QueryPlan(metrics=[mid], dimensions=dims, time_range=time_range, limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 metrics.yaml 的语法和契约完整性")
    parser.add_argument("--path", default=str(METRICS_PATH), help="metrics.yaml 路径")
    args = parser.parse_args()
    path = Path(args.path).expanduser().resolve()

    data = load_yaml(path)
    errors = check_contract(data)
    metrics = data.get("metrics") or {}

    print(f"检查文件：{path}")
    print(f"指标数量：{len(metrics)}")
    print("-" * 60)

    if errors:
        for err in errors:
            print(f"[FAIL] {err}")
        print("-" * 60)
        print(f"结构检查失败：{len(errors)} 个问题。先修 YAML，再跑一次。")
        raise SystemExit(1)

    # 结构没问题后再编译 + guard + 执行
    registry.METRICS_PATH = path
    registry.load_contract.cache_clear()
    db_exists = DB_PATH.exists()
    ok_compile = 0
    ok_exec = 0
    runtime_errors: list[str] = []

    for mid, metric in metrics.items():
        try:
            plan = sample_plan(mid, metric)
            compiled = compiler.compile_plan(plan)
            guard.check_sql(compiled.sql)
            ok_compile += 1
        except Exception as exc:  # noqa: BLE001 - 这里要把错误翻译成指标级提示
            runtime_errors.append(f"{mid}: 编译/guard 失败：{type(exc).__name__}: {exc}")
            print(f"[FAIL] {mid:<18} 编译/guard 失败")
            continue

        if not db_exists:
            print(f"[OK]   {mid:<18} 编译 + guard（数据库不存在，跳过执行）")
            continue

        try:
            duck.run_sql(compiled.sql, compiled.params)
            ok_exec += 1
            print(f"[OK]   {mid:<18} 编译 + guard + 执行")
        except Exception as exc:  # noqa: BLE001
            runtime_errors.append(f"{mid}: 执行失败：{type(exc).__name__}: {exc}")
            print(f"[FAIL] {mid:<18} 执行失败")

    print("-" * 60)
    print(f"编译/guard：{ok_compile}/{len(metrics)}")
    if db_exists:
        print(f"执行：      {ok_exec}/{len(metrics)}")
    else:
        print("执行：      跳过（数据库不存在，先跑 scripts/build_db.py）")

    if runtime_errors:
        print("\n具体错误：")
        for err in runtime_errors:
            print(f"  - {err}")
        raise SystemExit(1)

    if db_exists:
        print("全部通过。可以继续改下一个口径。")
    else:
        print("编译/guard 全部通过；执行已跳过（数据库不存在）。")


if __name__ == "__main__":
    main()
