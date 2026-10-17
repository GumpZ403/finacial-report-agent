# -*- coding: utf-8 -*-
"""
akshare 字段可用性探测（映射表核对，只读）

目的：回答"映射表左边的键名，在接口返回里到底存不存在"。
做法：真实拉取几家公司的东财摘要 + 新浪财务指标，收集**全部**字段名，与
      `app/data/datasource.py` 里的 ABSTRACT_MAP / INDICATOR_MAP 逐字比对，输出三类清单：
        1. 命中      —— 映射表的键在接口里存在
        2. 未命中    —— 映射表里有、接口里没有（死映射，应删）
        3. 未映射    —— 接口里有、我们没用（可扩展空间）
另外抽样打印关键指标的最新值，用于判断"营业成本"这类口径存疑的字段。

行为：**只读**——不写数据库、不改映射表、不落 raw 文件。
      仅在 storage/probe/ 下写一份核对报告 JSON（便于离线复查）。

用法：
    .venv/Scripts/python.exe scripts/probe_akshare_fields.py
    .venv/Scripts/python.exe scripts/probe_akshare_fields.py --codes 300750,600519 --start-year 2021
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

from app.data.datasource import (  # noqa: E402
    ABSTRACT_MAP, INDICATOR_MAP, DEFAULT_START_YEAR, _is_na,
)

# Windows 控制台默认 GBK，中文/符号直接 print 会抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_CODES = ["300750", "600519", "600036"]     # 制造 / 消费 / 金融各一家
# 口径存疑或关键的被抽样指标（只看值，不做判断）
SAMPLE_LABELS = ["营业总收入", "营业成本", "毛利率", "净利润", "归母净利润", "存货周转率"]
SAMPLE_COLUMNS = ["销售毛利率(%)", "销售净利率(%)", "总资产(元)", "存货周转率(次)", "存货周转天数(天)"]

# 拟新增映射的候选字段（用于逐项核对键名是否存在 + 取值量级是否与单位声明一致）
CANDIDATE_EM = [
    "营业成本", "扣非净利润", "商誉", "每股经营现金流", "净资产收益率_平均",
    "投入资本回报率", "营业利润率", "所得税/利润总额", "经营活动净现金/销售收入",
    "权益乘数", "产权比率", "现金比率", "保守速动比率",
    "总资产周转率", "流动资产周转率", "存货周转天数", "应收账款周转天数",
    "经营活动净现金/归属母公司的净利润",
]
CANDIDATE_SN = [
    "主营业务成本率(%)", "扣除非经常性损益后的净利润(元)", "每股经营性现金流(元)",
    "总资产周转率(次)", "固定资产周转率(次)", "总资产增长率(%)", "净资产增长率(%)",
    "股息发放率(%)", "现金比率(%)", "产权比率(%)", "利息支付倍数",
]


def _scan_eastmoney(fs, labels: list[str]) -> list[dict]:
    """东财：对候选标签取「最新一个非空值」（含分区），用于核对键名与量级"""
    if fs is None or "指标" not in fs.columns:
        return []
    date_cols = [str(c) for c in fs.columns if _looks_like_date(c)]
    if not date_cols:
        return []
    wanted = set(labels)
    found: list[dict] = []
    for _, row in fs.iterrows():
        label = str(row.get("指标", "")).strip()
        if label not in wanted:
            continue
        hit_date, hit_val = "", None
        for col in date_cols:                      # 东财列按报告期倒序（新 → 旧）
            v = row.get(col)
            if not _is_na(v):
                hit_date, hit_val = col, v
                break
        found.append({
            "label": label, "section": str(row.get("选项", "")).strip(),
            "date": hit_date, "value": None if hit_val is None else float(hit_val),
        })
    return found


def _scan_sina(fi, columns: list[str]) -> dict:
    """新浪：候选列的取值；另对所有含 %/率 的列做量级体检

    量级体检的用途：列名带 (%) 不代表取值真是百分数——本项目已实测到
    `经营现金净流量与净利润的比率(%)` 实际是"倍"。max|v| > 5 记为"疑似百分数"，
    ≤ 5 记为"疑似小数/倍"，供决定是否 ÷100。
    """
    import pandas as pd

    out: dict = {"candidates": {}, "scale_check": {}}
    if fi is None or not len(fi):
        return out
    for col in columns:
        if col not in fi.columns:
            continue
        vals = pd.to_numeric(fi[col], errors="coerce").dropna()
        if not len(vals):
            out["candidates"][col] = None
            continue
        out["candidates"][col] = {"last": float(vals.iloc[-1]), "max_abs": float(vals.abs().max())}
    for col in fi.columns:
        name = str(col)
        if not any(tag in name for tag in ("%", "率")):
            continue
        vals = pd.to_numeric(fi[col], errors="coerce").dropna()
        if not len(vals):
            out["scale_check"][name] = {"n": 0}
            continue
        mx = float(vals.abs().max())
        out["scale_check"][name] = {
            "n": int(len(vals)), "max_abs": mx,
            "verdict": "疑似百分数(需/100)" if mx > 5 else "疑似小数/倍(勿/100)",
        }
    return out


def _looks_like_date(name) -> bool:
    """'20251231' / '2025-12-31' 这类报告期列名"""
    s = str(name).strip().replace("-", "")
    return len(s) == 8 and s.isdigit()


def _sample_eastmoney(fs) -> dict:
    """东财：按分区抽关键指标在**最新一期**的值（用于判断口径，如 营业成本）"""
    if "指标" not in fs.columns:
        return {}
    date_cols = [str(c) for c in fs.columns if _looks_like_date(c)]
    if not date_cols:
        return {}
    latest = date_cols[0]          # 东财列名按报告期倒序（新 → 旧）
    by_section: dict[str, dict] = {}
    for _, row in fs.iterrows():
        label = str(row.get("指标", "")).strip()
        if label not in SAMPLE_LABELS:
            continue
        sec = str(row.get("选项", "")).strip() or "(无分区)"
        by_section.setdefault(sec, {})[label] = row.get(latest)
    return {"latest_period": latest, "by_section": by_section}


def _sample_sina(fi) -> dict:
    """新浪：抽最新一行的关键列值"""
    if fi is None or not len(fi) or "日期" not in fi.columns:
        return {}
    last = fi.iloc[-1]
    out = {"日期": str(last.get("日期"))[:10]}
    for col in SAMPLE_COLUMNS:
        if col in fi.columns:
            out[col] = last.get(col)
    return out


def _collect(datasets: list[dict], key: str) -> list[str]:
    """多份数据集的字段名并集（保持出现顺序）"""
    seen: dict[str, None] = {}
    for d in datasets:
        for item in d.get(key, []):
            seen.setdefault(item, None)
    return list(seen)


def probe(codes: list[str], start_year: str, delay: float = 0.4) -> dict:
    import akshare as ak

    em_frames: list[dict] = []
    em_errors: list[str] = []
    sn_frames: list[dict] = []
    sn_errors: list[str] = []
    samples: dict[str, dict] = {}
    value_samples: dict[str, dict] = {}
    em_scan: dict[str, list] = {}
    sn_scan: dict[str, dict] = {}

    for code in codes:
        # ---- 东财：财务摘要（宽表：行=指标，列=报告期）----
        time.sleep(delay)
        try:
            fs = ak.stock_financial_abstract(symbol=code)
            cols = [str(c) for c in fs.columns]
            labels = [str(x).strip() for x in fs["指标"].tolist()] if "指标" in fs.columns else []
            sections: dict[str, list[str]] = {}
            if "选项" in fs.columns:
                for sec, sub in fs.groupby("选项", sort=False):
                    sections[str(sec).strip()] = [str(x).strip() for x in sub["指标"].tolist()]
            em_frames.append({
                "code": code, "columns": cols, "labels": labels, "sections": sections,
                "row_count": int(len(fs)),
            })
            value_samples.setdefault(code, {})["eastmoney"] = _sample_eastmoney(fs)
            em_scan[code] = _scan_eastmoney(fs, CANDIDATE_EM)
        except Exception as e:
            em_errors.append(f"{code}: {type(e).__name__}: {str(e)[:200]}")

        # ---- 新浪：财务指标（一行一报告期）----
        time.sleep(delay)
        try:
            fi = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
            sn_frames.append({
                "code": code,
                "columns": [str(c) for c in fi.columns],
                "row_count": int(len(fi)),
                "date_min": str(fi["日期"].min())[:10] if "日期" in fi.columns and len(fi) else "",
                "date_max": str(fi["日期"].max())[:10] if "日期" in fi.columns and len(fi) else "",
            })
            value_samples.setdefault(code, {})["sina"] = _sample_sina(fi)
            sn_scan[code] = _scan_sina(fi, CANDIDATE_SN)
        except Exception as e:
            sn_errors.append(f"{code}: {type(e).__name__}: {str(e)[:200]}")

    em_labels = _collect(em_frames, "labels")
    em_columns = _collect(em_frames, "columns")
    sn_columns = _collect(sn_frames, "columns")

    # ---- 抽样取值（判断口径存疑字段用）----
    for frame, name in ((em_frames, "eastmoney"), (sn_frames, "sina")):
        for f in frame:
            samples.setdefault(f["code"], {})[name] = {
                "row_count": f["row_count"],
                **(f.get("sections") and {"sections": list(f["sections"].keys())} or {}),
            }

    em_keys = list(ABSTRACT_MAP.keys())
    sn_keys = list(INDICATOR_MAP.keys())
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "codes": codes,
        "start_year": start_year,
        "eastmoney": {
            "label_count": len(em_labels),
            "labels": em_labels,
            "columns": em_columns,
            "sections": {f["code"]: list(f["sections"].keys()) for f in em_frames},
            "labels_by_section": {f["code"]: f["sections"] for f in em_frames},
            "errors": em_errors,
        },
        "sina": {
            "column_count": len(sn_columns),
            "columns": sn_columns,
            "date_range": {f["code"]: [f["date_min"], f["date_max"]] for f in sn_frames},
            "errors": sn_errors,
        },
        "checks": {
            "eastmoney_hit": [k for k in em_keys if k in em_labels],
            "eastmoney_missing": [k for k in em_keys if k not in em_labels],
            "eastmoney_unmapped": [x for x in em_labels if x not in ABSTRACT_MAP],
            "sina_hit": [k for k in sn_keys if k in sn_columns],
            "sina_missing": [k for k in sn_keys if k not in sn_columns],
            "sina_unmapped": [x for x in sn_columns if x not in INDICATOR_MAP],
        },
        "samples": samples,
        "value_samples": value_samples,
        "scan": {"eastmoney": em_scan, "sina": sn_scan},
    }
    return report


def print_report(r: dict):
    ck = r["checks"]
    print("=" * 78)
    print(f"探测时间 {r['generated_at']} | 公司 {r['codes']} | 新浪 start_year={r['start_year']}")
    print("=" * 78)
    print(f"\n【东财 stock_financial_abstract】标签 {r['eastmoney']['label_count']} 个"
          f"（分区: {r['eastmoney']['sections']}）")
    print(f"  命中 {len(ck['eastmoney_hit'])}/{len(ck['eastmoney_hit']) + len(ck['eastmoney_missing'])}")
    if ck["eastmoney_missing"]:
        print(f"  [MISS] 映射表里有、接口没有（{len(ck['eastmoney_missing'])} 个）: {ck['eastmoney_missing']}")
    print(f"  可扩展（接口有、未映射）{len(ck['eastmoney_unmapped'])} 个: {ck['eastmoney_unmapped']}")
    if r["eastmoney"]["errors"]:
        print(f"  [WARN] 拉取失败: {r['eastmoney']['errors']}")

    print(f"\n【新浪 stock_financial_analysis_indicator】列 {r['sina']['column_count']} 个"
          f"（日期范围 {r['sina']['date_range']}）")
    print(f"  命中 {len(ck['sina_hit'])}/{len(ck['sina_hit']) + len(ck['sina_missing'])}")
    if ck["sina_missing"]:
        print(f"  [MISS] 映射表里有、接口没有（{len(ck['sina_missing'])} 个）: {ck['sina_missing']}")
    print(f"  可扩展（接口有、未映射）{len(ck['sina_unmapped'])} 个: {ck['sina_unmapped']}")
    if r["sina"]["errors"]:
        print(f"  [WARN] 拉取失败: {r['sina']['errors']}")

    print("\n【抽样】各公司行数 / 分区:")
    for code, s in r["samples"].items():
        print(f"  {code}: {s}")

    print("\n【取值抽样】最新一期的关键指标值（判断口径用）:")
    for code, s in r.get("value_samples", {}).items():
        em = s.get("eastmoney") or {}
        print(f"  {code} 东财（报告期列 {em.get('latest_period')}）:")
        for sec, labels in (em.get("by_section") or {}).items():
            print(f"      [{sec}] " + ", ".join(f"{k}={v}" for k, v in labels.items()))
        sn = s.get("sina") or {}
        if sn:
            print(f"  {code} 新浪（{sn.get('日期')}）: " +
                  ", ".join(f"{k}={v}" for k, v in sn.items() if k != "日期"))

    print("\n【候选字段核对】东财（label | 分区 | 最新非空期 | 值）:")
    for code, rows in (r.get("scan", {}).get("eastmoney") or {}).items():
        print(f"  {code}:")
        for it in rows:
            print(f"      {it['label']:<28} [{it['section']}] {it['date']} = {it['value']}")

    print("\n【候选字段核对】新浪（列 | 最新值 | max|值|）:")
    for code, sn in (r.get("scan", {}).get("sina") or {}).items():
        print(f"  {code}:")
        for col, info in (sn.get("candidates") or {}).items():
            print(f"      {col:<34} {info}")

    print("\n【量级体检】新浪所有含 %/率 的列（决定是否 ÷100）:")
    for code, sn in (r.get("scan", {}).get("sina") or {}).items():
        print(f"  {code}:")
        for col, info in sorted((sn.get("scale_check") or {}).items()):
            print(f"      {col:<36} {info}")
        break       # 量级体检取第一家公司即可


def main():
    argv = sys.argv[1:]
    codes = DEFAULT_CODES
    start_year = DEFAULT_START_YEAR
    if "--codes" in argv:
        codes = [x.strip() for x in argv[argv.index("--codes") + 1].split(",") if x.strip()]
    if "--start-year" in argv:
        start_year = argv[argv.index("--start-year") + 1]

    report = probe(codes, start_year)
    print_report(report)

    out_dir = Path(__file__).resolve().parent.parent / "storage" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 文件名精确到秒：同一分钟内重跑不会覆盖上一次的报告
    out = out_dir / f"akshare_fields_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"核对报告已写入 {out}")
    print(f"\n报告文件: {out}")
    print("提示：确认无误后，按 checks 里的 eastmoney_missing / sina_missing 删除死映射，"
          "按 unmapped 清单决定是否扩展指标集。")


if __name__ == "__main__":
    main()
