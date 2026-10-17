# -*- coding: utf-8 -*-
"""L2 数据集生成：口径解析（用户说法 → metric_code / 公司代码）

诚实的定位：这份数据集是**从指标字典的别名 + 少量句式模板**生成的，
它测的是"解析器对现有说法的覆盖度"，不是"真实用户问法的分布"。
等有了真实对话日志（`/trace` + conversations 表），应逐步替换成真实 query。

输出：scripts/eval/datasets/l2_queries.jsonl
用法：../.venv/Scripts/python.exe -m scripts.eval.build_l2_dataset
"""
from __future__ import annotations

import json
from pathlib import Path

from app.domains.financial.repository import get_db

OUT = Path(__file__).resolve().parent / "datasets" / "l2_queries.jsonl"

# 句式模板：{x} = 指标说法；刻意混入公司名/口语，检验"包含匹配"能力
METRIC_TEMPLATES = [
    "{x}",
    "{x}是多少？",
    "帮我查一下{x}",
    "贵州茅台2025Q1的{x}",
    "茅台的{x}最近三年怎么样",
]

COMPANY_PHRASES = ["{n}", "{n}的财报", "{c}", "查一下{n}", "{n}怎么样"]


def main() -> Path:
    db = get_db()
    meta = db.list_metric_dict()
    cases: list[dict] = []
    idx = 0

    for code, m in sorted(meta.items()):
        names = [m.get("name_cn") or ""] + list(m.get("aliases") or [])
        names = [n for n in names if n]
        if not names:
            continue
        for i, name in enumerate(names[:2]):
            tmpl = METRIC_TEMPLATES[(idx + i) % len(METRIC_TEMPLATES)]
            cases.append({
                "id": f"l2-m{idx:03d}",
                "kind": "metric",
                "text": tmpl.format(x=name),
                "expect": code,
                "via": name,
            })
            idx += 1

    companies = []
    for code in db.list_company_codes():
        comp = db.get_company(code=code)
        if comp:
            companies.append(comp)
    for i, comp in enumerate(companies):
        tmpl = COMPANY_PHRASES[i % len(COMPANY_PHRASES)]
        cases.append({
            "id": f"l2-c{i:03d}",
            "kind": "company",
            "text": tmpl.format(n=comp["name"], c=comp["code"]),
            "expect": comp["code"],
            "via": comp["name"],
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"L2 数据集已生成: {OUT}（{len(cases)} 条：指标 "
          f"{sum(1 for c in cases if c['kind'] == 'metric')} / 公司 "
          f"{sum(1 for c in cases if c['kind'] == 'company')}）")
    return OUT


if __name__ == "__main__":
    main()
