# -*- coding: utf-8 -*-
"""L2 口径解析评测：Top-1 / Top-3 / 低置信是否正确返回候选

三项指标：
    top1        解析结果 == 期望
    top3        期望出现在 candidates 里（低置信时不算 top1 但算 top3）
    clarify_ok  低置信时确实给出了候选（= 正确地请用户澄清，而不是硬猜一个错答案）

用法：
    ../.venv/Scripts/python.exe -m scripts.eval.run_l2
    ../.venv/Scripts/python.exe -m scripts.eval.run_l2 --write-baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domains.financial.repository import get_db
from app.domains.financial.services.resolver import resolve_company, resolve_metric

HERE = Path(__file__).resolve().parent
DATASET = HERE / "datasets" / "l2_queries.jsonl"
BASELINE = HERE / "baselines" / "baseline_l2.json"


def load_dataset() -> list[dict]:
    if not DATASET.is_file():
        raise SystemExit(f"缺少数据集 {DATASET}，先跑 build_l2_dataset")
    with DATASET.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def evaluate(limit: int | None = None, dataset: list[dict] | None = None,
             db=None) -> dict:
    db = db or get_db()
    cases = (dataset or load_dataset())
    cases = cases[:limit] if limit else cases
    stat = {"metric": {"n": 0, "top1": 0, "top3": 0, "low": 0, "low_ok": 0},
            "company": {"n": 0, "top1": 0, "top3": 0, "low": 0, "low_ok": 0}}
    misses = []
    for case in cases:
        kind = case["kind"]
        fn = resolve_metric if kind == "metric" else resolve_company
        res = fn(case["text"], db=db)
        s = stat[kind]
        s["n"] += 1
        if res.code == case["expect"]:
            s["top1"] += 1
            s["top3"] += 1
        elif case["expect"] in (res.candidates or []):
            s["top3"] += 1
        if res.confidence < 0.8:
            s["low"] += 1
            if res.candidates:
                s["low_ok"] += 1
        if res.code != case["expect"]:
            misses.append({"id": case["id"], "text": case["text"],
                           "expect": case["expect"], "got": res.code,
                           "conf": res.confidence})
    total = sum(v["n"] for v in stat.values())
    top1 = sum(v["top1"] for v in stat.values())
    top3 = sum(v["top3"] for v in stat.values())
    low = sum(v["low"] for v in stat.values())
    low_ok = sum(v["low_ok"] for v in stat.values())
    return {
        "total": total,
        "top1_rate": round(top1 / total, 4) if total else 0.0,
        "top3_rate": round(top3 / total, 4) if total else 0.0,
        "low_conf_count": low,
        "low_conf_clarify_rate": round(low_ok / low, 4) if low else 1.0,
        "by_kind": {k: {kk: (round(vv / v["n"], 4) if kk in ("top1", "top3") and v["n"] else vv)
                        for kk, vv in v.items()} for k, v in stat.items()},
        "misses": misses[:12],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    result = evaluate(args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    if args.write_baseline or base is None:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {k: result[k] for k in ("total", "top1_rate", "top3_rate")},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"基线已写入 {BASELINE}")
        return 0
    if result["top1_rate"] < base["top1_rate"] - 1e-4:
        print(f"❌ 低于基线：{result['top1_rate']} < {base['top1_rate']}")
        return 1
    print(f"✅ 达标：{result['top1_rate']} >= {base['top1_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
