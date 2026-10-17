# -*- coding: utf-8 -*-
"""L3 真值集生成：从库里抽样 (公司, 指标, 期次) 并用 SQL 值作为真值

为什么用 L3 而不是先做 L1 人工标注：
- 真值可以**从库里直接算**，零人工
- 它测的是"系统取数/归一/展示是否正确"，这是最该先保住的能力
- 抽样只取 `source != 'derived'` 的行：拿派生值当真值等于自证，必须用东财原始值

输出：scripts/eval/datasets/l3_queries.jsonl
用法：../.venv/Scripts/python.exe -m scripts.eval.build_dataset --limit 200
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sqlalchemy import text as sa_text

from app.domains.financial.repository import get_db

OUT = Path(__file__).resolve().parent / "datasets" / "l3_queries.jsonl"


def build(limit: int = 200, per_company: int = 10, seed: int = 20261017) -> Path:
    random.seed(seed)
    db = get_db()
    with db.engine.connect() as conn:
        companies = [r[0] for r in conn.execute(sa_text(
            "SELECT code FROM company_master ORDER BY code"))]
        rows = conn.execute(sa_text("""
            SELECT c.code, m.metric_code, p.period, p.period_end, m.value, d.unit_type
            FROM financial_metrics m
            JOIN financial_periods p ON p.period_id = m.period_id
            JOIN company_master c   ON c.company_id = p.company_id
            JOIN metric_dict d      ON d.metric_code = m.metric_code
            WHERE m.value IS NOT NULL
              AND (m.source IS NULL OR m.source != 'derived')
        """)).all()

    by_company: dict[str, list] = {}
    for r in rows:
        by_company.setdefault(r[0], []).append(r)

    picked = []
    for code in companies:
        pool = by_company.get(code) or []
        random.shuffle(pool)
        picked.extend(pool[:per_company])
    picked = picked[:limit]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for i, (code, metric, period, period_end, value, unit_type) in enumerate(picked):
            f.write(json.dumps({
                "id": f"l3-{i:04d}",
                "company": code,
                "metric": metric,
                "period": period,
                "period_end": period_end,
                "expected": float(value),
                "unit_type": unit_type,
            }, ensure_ascii=False) + "\n")

    print(f"L3 数据集已生成: {OUT}（{len(picked)} 条 / {len(companies)} 家）")
    return OUT


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--per-company", type=int, default=10)
    args = ap.parse_args()
    build(args.limit, args.per_company)
