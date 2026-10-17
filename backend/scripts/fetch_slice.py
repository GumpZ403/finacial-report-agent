# -*- coding: utf-8 -*-
"""
M1 垂直切片拉取（方案 v1.1 §3 / §8）
- 20 家覆盖 金融/地产/制造/消费/医药/科技/能源 的 A 股公司
- 流程：akshare 拉取 → 原始落盘 storage/raw/<code>/ → upsert 公司/报告期/指标 → 宽表派生
  → 该家窗口清理 → L0 回算校验
- 幂等：重复运行按 (company_id, period) upsert，不产生脏数据
- 增量：某家最新期次 == 最新已披露期次、采集完整、且 7 天内拉过 → 本轮跳过（--force 强制重拉）
- 窗口：Settings.PERIOD_WINDOW_YEARS（默认 10 年）；入库裁剪 + 逐家清理越界期次
- 退出码：任一公司失败/不完整 → 1（定时任务据此判断成败）

用法：
  ..\\.venv\\Scripts\\python.exe scripts\\fetch_slice.py [--limit 20] [--force] [--no-prune]
"""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.domains.financial.dates import latest_disclosed_period
from app.domains.financial.ingest.akshare_em import get_adapter
from app.domains.financial.repository import get_db
from app.platform.settings import get_settings

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


# 增量：同一家公司多久之内不重复拉（最新期次没变的前提下）
FRESH_DAYS = 7


def _exchange_of(code: str) -> str:
    """6=上交所 / 0,3=深交所 / 4,8=北交所（此前 4、8 开头被误标成 SZSE）"""
    if code.startswith("6"):
        return "SSE"
    if code.startswith(("0", "3")):
        return "SZSE"
    return "BSE"


def _is_fresh(db, code: str, fresh_days: int = FRESH_DAYS) -> bool:
    """已是最新披露期次 + 采集完整 + 最近拉过 → 本轮可跳过（增量）

    注意三点：期次必须等于"按披露日历应已发布的最新期"（不是"库里有几期"），
    采集必须完整（partial 说明上次缺表，得重试），时间窗口防止同一轮重复跑。
    """
    try:
        rec = db.get_latest_period(code)
    except Exception:       # 查询失败就老老实实重拉，不拿增量当借口
        return False
    if not rec or rec.get("period") != latest_disclosed_period():
        return False
    if (rec.get("fetch_status") or "ok") != "ok":
        return False
    try:
        fetched = datetime.strptime(rec.get("fetched_at") or "", "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return (datetime.now() - fetched).days < fresh_days


def main(limit: int = 20, force: bool = False, prune: bool = True) -> dict:
    db = get_db()
    adapter = get_adapter()
    slice_list = SLICE[:limit]
    window = get_settings().PERIOD_WINDOW_YEARS
    logger.info(
        f"M1 切片开始: {len(slice_list)} 家公司 / 期次窗口近 {window} 年 / "
        f"最新已披露期次 {latest_disclosed_period()}"
        + ("（--force 忽略增量）" if force else "")
    )

    total_reports = total_metrics = 0
    ok_companies = skipped = 0
    pruned_reports = 0
    failures: list[dict] = []
    t0 = time.time()

    for code, name, l1, l2, tpl in slice_list:
        if not force and _is_fresh(db, code):
            skipped += 1
            logger.info(
                f"[{code}] {name}: 已是最新披露期次且 {FRESH_DAYS} 天内拉过，跳过（--force 可强制）")
            continue
        try:
            result = adapter.fetch_indicators(code)
            if not result.reports:
                failures.append({"code": code, "name": name, "reason": "三表均未取到数据"})
                logger.error(f"[{code}] {name}: 三表均未取到数据，跳过")
                continue
            # 先建档再落盘：raw_financial_payload 需要 company_id。
            # 此前顺序相反 → 首次拉取时公司尚不存在，company_id 恒为 NULL 且永不回填。
            company_id = db.upsert_company(
                code, name, industry_l1=l1, industry_l2=l2, industry_template=tpl,
                exchange=_exchange_of(code),
            )
            adapter.save_raw(code, result.reports)
            n_rep = n_met = 0
            for r in result.reports:
                rid = db.upsert_financial_period(
                    company_id, r["period"], period_type=r["period_type"],
                    is_single_quarter=r["is_single_quarter"],
                    period_end=r["period_end"], source="akshare",
                    fetched_at=result.fetched_at,
                    # 采集完整性：partial 时该期数据不全，报告必须显式标注而非当作空值
                    fetch_status=result.fetch_status,
                )
                # 单位由 metric_dict 推导（不再传 unit）；来源随指标写入，供溯源/质检
                n = db.upsert_metrics(rid, r["metrics"], sources=r.get("sources") or {})
                db.derive_financial_core(rid)
                n_met += n
                n_rep += 1
            total_reports += n_rep
            total_metrics += n_met
            if result.partial:
                failures.append({
                    "code": code, "name": name,
                    "reason": f"采集不完整 {result.fetch_status}（已写入 {n_rep} 期）",
                })
                logger.error(
                    f"[{code}] {name}: 采集不完整 {result.fetch_status}，"
                    f"已写入 {n_rep} 期但数据不全"
                )
            else:
                ok_companies += 1
                logger.info(f"[{code}] {name}: {n_rep} 期 / {n_met} 指标")
            # 窗口落地：入库本来就只写窗口内，但历史上窗口改小过/曾写过越界期，
            # 这里逐家清理（只清本案公司，避免局部运行误删别家）。raw 仍保留全历史可回放。
            if prune:
                pr = db.prune_out_of_window(company_code=code)
                if pr.get("reports"):
                    pruned_reports += pr["reports"]
                    logger.warning(
                        f"[{code}] 窗口清理：删除越界报告期 {pr['reports']} / 指标 {pr['metrics']}"
                        f"（period_end < {pr['cutoff']}）")
        except Exception as e:
            failures.append({
                "code": code, "name": name,
                "reason": f"{type(e).__name__}: {str(e)[:120]}",
            })
            logger.error(f"[{code}] {name} 拉取失败: {type(e).__name__}: {str(e)[:200]}")
            continue

    logger.info(
        f"M1 切片完成: 成功 {ok_companies} / 跳过 {skipped} / 失败 {len(failures)} / "
        f"报告期 {total_reports} / 指标 {total_metrics} / 窗口清理 {pruned_reports} 期 / "
        f"耗时 {time.time()-t0:.0f}s"
    )
    if failures:
        logger.error("失败清单: " + "; ".join(
            f"{f['code']} {f['name']}（{f['reason']}）" for f in failures))

    # L0 走数据层唯一实现（全库校验，不再对单个报告期抽样）
    from app.domains.financial.quality import run_l0
    l0 = run_l0(db=db)
    logger.info(
        f"L0（全库）: 回算 {l0['recompute_pass_rate']}% / 同比 {l0['yoy_pass_rate']}% / "
        f"单位 {l0['unit_pass_rate']}% / 长表-宽表 {l0['core_mirror_pass_rate']}% / "
        f"核心指标覆盖 {l0['coverage_rate']}%"
    )
    return {"ok": ok_companies, "skipped": skipped, "failed": failures,
            "reports": total_reports, "metrics": total_metrics,
            "pruned": pruned_reports, "l0": l0}


if __name__ == "__main__":
    lim, force, prune = 20, False, True
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--limit":
            lim = int(args[i + 1])
            i += 2
        elif a == "--force":
            force = True
            i += 1
        elif a == "--no-prune":
            prune = False
            i += 1
        else:
            raise SystemExit(f"未知参数: {a}（支持 --limit N / --force / --no-prune）")
    summary = main(lim, force=force, prune=prune)
    raise SystemExit(0 if not summary["failed"] else 1)
