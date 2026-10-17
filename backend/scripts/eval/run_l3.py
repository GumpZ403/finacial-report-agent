# -*- coding: utf-8 -*-
"""L3 结果集比对：逐条走服务层取数，与真值比对

为什么比"结果"而不是比"SQL 文本"：
- 同一条问题可以有多种正确查法，比 SQL 文本是比实现细节
- 比结果才是用户真正在意的：数字对不对、单位对不对、期次对不对

指标：
    pass_rate   数值完全一致（相对误差 < 1e-6）的比例
    missing     服务层取不到的比例（应为 0，>0 说明映射/宽表有问题）
    unit_ok     展示形态非空且单位类型正确的比例

用法：
    ../.venv/Scripts/python.exe -m scripts.eval.run_l3                 # 跑一遍
    ../.venv/Scripts/python.exe -m scripts.eval.run_l3 --write-baseline # 写基线
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domains.financial.services.quote import quote

HERE = Path(__file__).resolve().parent
DATASET = HERE / "datasets" / "l3_queries.jsonl"
BASELINE = HERE / "baselines" / "baseline_v1.json"


def load_dataset() -> list[dict]:
    if not DATASET.is_file():
        raise SystemExit(f"缺少数据集 {DATASET}，先跑 build_dataset")
    with DATASET.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_baseline() -> dict | None:
    if not BASELINE.is_file():
        return None
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale < rel


def evaluate(limit: int | None = None, dataset: list[dict] | None = None,
             db=None) -> dict:
    cases = (dataset or load_dataset())[:limit] if limit else (dataset or load_dataset())
    passed = failed = missing = unit_ok = 0
    failures: list[dict] = []
    for case in cases:
        q = quote(case["company"], case["metric"], case["period"], db=db)
        if q is None:
            missing += 1
            failures.append({"id": case["id"], "reason": "missing"})
            continue
        if _close(q.value, case["expected"]):
            passed += 1
        else:
            failed += 1
            failures.append({"id": case["id"], "reason": "value",
                             "expected": case["expected"], "got": q.value})
        if q.display and q.unit_type == (case.get("unit_type") or q.unit_type):
            unit_ok += 1
    total = len(cases)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "missing": missing,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "unit_ok_rate": round(unit_ok / total, 4) if total else 0.0,
        "failures": failures[:10],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    result = evaluate(args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    base = load_baseline()
    if args.write_baseline or base is None:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {k: result[k] for k in ("total", "pass_rate", "unit_ok_rate")},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"基线已写入 {BASELINE}")
        return 0

    if result["pass_rate"] < base["pass_rate"] - 1e-4:
        print(f"❌ 低于基线：{result['pass_rate']} < {base['pass_rate']}")
        return 1
    print(f"✅ 达标：{result['pass_rate']} >= {base['pass_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
