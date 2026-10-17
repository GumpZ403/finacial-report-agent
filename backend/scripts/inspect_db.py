# -*- coding: utf-8 -*-
"""
数据库内容查看器：SQLite（财报）+ ChromaDB（向量，若已构建）双侧概览/检索

用法：
    .venv/Scripts/python.exe inspect_db.py                          # 全览：财报库各表统计 + Chroma 样本
    .venv/Scripts/python.exe inspect_db.py company 300750           # 查看某公司全部报告期与核心指标
    .venv/Scripts/python.exe inspect_db.py search 宁德              # 按公司名/代码检索
    .venv/Scripts/python.exe inspect_db.py sqlite                   # 只看 SQLite
    .venv/Scripts/python.exe inspect_db.py chroma                   # 只看 Chroma
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
logger.remove()

from app.data.repository import get_db


# ---------- SQLite（财报域） ----------
def show_sqlite(company: str | None = None):
    db = get_db()
    print("\n" + "=" * 60)
    print("SQLite（storage/financial.db）")
    print("=" * 60)
    with db.get_session() as s:
        from app.data.schema import (
            CompanyMaster, FinancialPeriod, FinancialMetric,
            MetricDict, FinancialCore, DataQualityLog,
        )
        companies = s.query(CompanyMaster).count()
        reports = s.query(FinancialPeriod).count()
        metrics = s.query(FinancialMetric).count()
        dicts = s.query(MetricDict).count()
        quality = s.query(DataQualityLog).count()
        print(f"company_master:      {companies} 家")
        print(f"financial_periods:   {reports} 期")
        print(f"financial_metrics:   {metrics} 条指标")
        print(f"metric_dict:         {dicts} 个指标字典")
        print(f"data_quality_log:    {quality} 条质量日志")

        if company:
            # 按代码/名称查公司
            comp = (s.query(CompanyMaster)
                    .filter((CompanyMaster.code == company) | (CompanyMaster.name.contains(company)))
                    .first())
            if not comp:
                print(f"\n未找到公司: {company}")
                return
            print(f"\n--- 公司: {comp.name}（{comp.code} / {comp.industry_l1}-{comp.industry_l2}） ---")
            reps = (s.query(FinancialPeriod)
                    .filter(FinancialPeriod.company_id == comp.company_id)
                    .order_by(FinancialPeriod.period.desc()).all())
            print(f"共 {len(reps)} 个报告期：")
            for r in reps[:12]:
                core = s.query(FinancialCore).filter_by(period_id=r.period_id).first()
                if core:
                    print(f"  [{r.period}] 营收={core.revenue:,.0f} 净利={core.net_profit:,.0f} "
                          f"毛利率={core.gross_margin*100 if core.gross_margin is not None else 0:.1f}% "
                          f"负债率={core.debt_ratio*100 if core.debt_ratio is not None else 0:.1f}%")
                else:
                    print(f"  [{r.period}]（宽表未派生）")
        else:
            print("\n--- 行业分布 ---")
            rows = (s.query(CompanyMaster.industry_l1,
                            CompanyMaster.industry_l2,
                            CompanyMaster.code, CompanyMaster.name)
                    .order_by(CompanyMaster.industry_l1, CompanyMaster.industry_l2).all())
            cur = None
            for l1, l2, code, name in rows:
                key = f"{l1}/{l2}"
                if key != cur:
                    print(f"\n  【{key}】")
                    cur = key
                print(f"    {code}  {name}")
            print("\n提示：用 `inspect_db.py company <代码或名称>` 查看单个公司明细")


# ---------- ChromaDB ----------
def show_chroma():
    try:
        from app.data.vector_db import get_vector_store
        vs = get_vector_store()
        print("\n" + "=" * 60)
        print("ChromaDB（财报向量库）")
        print("=" * 60)
        n = vs.count()
        print(f"向量总数: {n}")
        if n > 0:
            print("\n--- 前 5 条向量样本 ---")
            res = vs._collection.get(limit=5, include=["documents", "metadatas"])
            for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"]):
                m = meta or {}
                print(f"  [{cid}] {doc[:40]}")
                print(f"       company_id={m.get('company_id')} | {m.get('period')} | {m.get('metric_code')}")
    except Exception as e:
        print(f"\nChromaDB 不可用：{e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = args[0] if args else "all"
    target = args[1] if len(args) > 1 else None

    if mode == "company":
        show_sqlite(company=target)
    elif mode == "search":
        show_sqlite(company=target)
    elif mode == "chroma":
        show_chroma()
    elif mode == "sqlite":
        show_sqlite()
    else:
        show_sqlite()
        show_chroma()
    print("\n完成")
