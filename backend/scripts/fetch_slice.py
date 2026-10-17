# -*- coding: utf-8 -*-
"""
M1 垂直切片拉取（方案 v1.1 §3 / §8）
- 20 家覆盖 金融/地产/制造/消费/医药/科技/能源 的 A 股公司
- 流程：akshare 拉取 → 原始落盘 storage/raw/ → upsert 公司/报告期/指标 → 宽表派生 → L0 回算校验
- 幂等：重复运行按 (company_id, period) upsert，不产生脏数据
用法：.venv/Scripts/python.exe scripts/fetch_slice.py [--limit 20]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.domains.financial.ingest.akshare_em import get_adapter
from app.domains.financial.repository import get_db

# (code, name, industry_l1, industry_l2, industry_template)
SLICE = [
    # 金融
    ("600036", "招商银行", "金融", "股份制银行", "financial"),
    ("601318", "中国平安", "金融", "保险", "financial"),
    ("601166", "兴业银行", "金融", "股份制银行", "financial"),
    # 地产
    ("000002", "万科A", "地产", "房地产开发", "general"),
    ("600048", "保利发展", "地产", "房地产开发", "general"),
    # 制造
    ("300750", "宁德时代", "制造", "电池", "general"),
    ("000333", "美的集团", "制造", "家电", "general"),
    ("002594", "比亚迪", "制造", "乘用车", "general"),
    # 消费
    ("600519", "贵州茅台", "消费", "白酒", "general"),
    ("000858", "五粮液", "消费", "白酒", "general"),
    ("600887", "伊利股份", "消费", "乳制品", "general"),
    # 医药
    ("600276", "恒瑞医药", "医药", "化学制药", "general"),
    ("300760", "迈瑞医疗", "医药", "医疗器械", "general"),
    # 科技
    ("002415", "海康威视", "科技", "安防设备", "general"),
    ("002230", "科大讯飞", "科技", "AI软件", "general"),
    # 能源
    ("601899", "紫金矿业", "能源", "有色金属", "general"),
    ("600900", "长江电力", "能源", "电力", "general"),
    ("601012", "隆基绿能", "能源", "光伏", "general"),
    # 其他
    ("601888", "中国中免", "消费", "旅游零售", "general"),
    ("603288", "海天味业", "消费", "调味品", "general"),
]


def main(limit: int = 20):
    db = get_db()
    adapter = get_adapter()
    slice_list = SLICE[:limit]
    logger.info(f"M1 切片开始: {len(slice_list)} 家公司")

    total_reports = total_metrics = 0
    company_rows = 0
    t0 = time.time()

    for code, name, l1, l2, tpl in slice_list:
        try:
            reports = adapter.fetch_indicators(code)
            if not reports:
                logger.warning(f"[{code}] 无数据，跳过")
                continue
            adapter.save_raw(code, reports)
            company_id = db.upsert_company(
                code, name, industry_l1=l1, industry_l2=l2, industry_template=tpl,
                exchange="SSE" if code.startswith("6") else "SZSE",
            )
            company_rows += 1
            n_rep = n_met = 0
            for r in reports:
                rid = db.upsert_financial_period(
                    company_id, r["period"], period_type=r["period_type"],
                    is_single_quarter=r["is_single_quarter"],
                    period_end=r["period_end"], source="akshare",
                    fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                # 单位由 metric_dict 推导（不再传 unit）；来源随指标写入，供溯源/质检
                n = db.upsert_metrics(rid, r["metrics"], sources=r.get("sources") or {})
                db.derive_financial_core(rid)
                n_met += n
                n_rep += 1
            total_reports += n_rep
            total_metrics += n_met
            logger.info(f"[{code}] {name}: {n_rep} 期 / {n_met} 指标")
        except Exception as e:
            logger.error(f"[{code}] {name} 拉取失败: {type(e).__name__}: {str(e)[:200]}")
            continue

    logger.info(
        f"M1 切片完成: 公司 {company_rows} / 报告期 {total_reports} / 指标 {total_metrics} / "
        f"耗时 {time.time()-t0:.0f}s"
    )

    # L0 走数据层唯一实现（全库校验，不再对单个报告期抽样）
    from app.domains.financial.quality import run_l0
    l0 = run_l0(db=db)
    logger.info(
        f"L0（全库）: 回算 {l0['recompute_pass_rate']}% / 同比 {l0['yoy_pass_rate']}% / "
        f"单位 {l0['unit_pass_rate']}% / 长表-宽表 {l0['core_mirror_pass_rate']}% / "
        f"核心指标覆盖 {l0['coverage_rate']}%"
    )


if __name__ == "__main__":
    lim = 20
    if len(sys.argv) > 1 and sys.argv[1] == "--limit":
        lim = int(sys.argv[2])
    main(lim)
