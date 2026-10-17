# -*- coding: utf-8 -*-
"""
财报文本化生成器（结构化数据 → 可读文本片段，供 embedding / RAG 使用）

- 输入：repository 提供的宽表行（financial_core 镜像 + 报告期口径字段）
- 输出：list[dict]，每条 = 一个检索片段（chunk）
- 片段分组：
    group=overview  单期综合描述（每期必有）：规模/盈利/成长/现金流/营运/偿债/每股
    group=anomaly   单期异常关注（与上一报告期波动超阈值时追加）
    group=trend     跨期趋势（近 3 年 / 近 5 年年报），对应"近三年毛利率趋势"类问法
- 口径声明：文本显式标注报告类型与"累计、合并口径"（方案 §4.3 / 附录 A）
- 行业模板：financial 模板下毛利率、存货/应收类指标不写入文本（方案 §3）
- 数据缺失：趋势段遇缺值写"数据缺失"，不留空、不插值（方案 §6.4）
- 纯转换，不依赖 LLM、不连网络；所有计算在 Python 完成

用法（原 M3 缺失的全量向量化入口）：
    python -m app.domains.financial.textify --dry --limit 2        # 只生成并打印样例，不入向量库
    python -m app.domains.financial.textify --company 300750       # 重建单家公司（先删后写）
    python -m app.domains.financial.textify --all --reindex        # 清空重建全量（20 家）
"""
from __future__ import annotations

from loguru import logger

from app.domains.financial.scope import is_applicable, normalize_template
from app.domains.financial.repository import get_db, normalize_code

# ---------- 指标中文名 ----------

CORE_LABELS: dict[str, str] = {
    # 规模与盈利
    "revenue": "营业收入", "operate_cost": "营业成本", "gross_profit": "毛利润",
    "gross_margin": "毛利率", "net_profit": "净利润", "net_profit_parent": "归母净利润",
    "net_margin": "净利率", "roe": "净资产收益率", "roe_weighted": "加权净资产收益率",
    "roa": "总资产收益率",
    # 成长
    "revenue_yoy": "营收同比", "net_profit_yoy": "净利润同比",
    "revenue_cagr_3y": "营收三年复合增速",
    # 现金流
    "operating_cash_flow": "经营活动现金流净额",
    "investing_cash_flow": "投资活动现金流净额",
    "financing_cash_flow": "筹资活动现金流净额",
    "ocf_to_net_profit": "净现比", "free_cash_flow": "自由现金流",
    # 营运效率
    "inventory": "存货", "inventory_turnover": "存货周转率", "inventory_days": "存货周转天数",
    "receivable": "应收账款", "receivable_turnover": "应收账款周转率",
    "receivable_days": "应收账款周转天数",
    "payable_turnover": "应付账款周转率", "payable_days": "应付账款周转天数",
    "cash_conversion_cycle": "现金转换周期",
    # 偿债与结构
    "total_assets": "总资产", "total_liabilities": "总负债", "total_equity": "总权益",
    "debt_ratio": "资产负债率", "current_ratio": "流动比率",
    "quick_ratio": "速动比率", "interest_coverage": "利息保障倍数",
    # 每股/其他
    "eps": "基本每股收益", "bps": "每股净资产",
    "dividend_per_share": "每股股利", "employee_count": "员工人数",
    # 2026-09-22 扩展批次（已进核心宽表）
    "total_operating_cost": "营业总成本", "net_profit_deducted": "扣非净利润",
    "goodwill": "商誉", "ocf_per_share": "每股经营现金流",
    "roe_avg": "净资产收益率(平均)", "roic": "投入资本回报率",
    "operating_margin": "营业利润率", "effective_tax_rate": "实际税率",
    "ocf_to_revenue": "经营现金流/营收", "equity_multiplier": "权益乘数",
    "debt_to_equity": "产权比率", "cash_ratio": "现金比率",
    "quick_ratio_conservative": "保守速动比率", "total_asset_turnover": "总资产周转率",
    "current_asset_turnover": "流动资产周转率", "fixed_asset_turnover": "固定资产周转率",
    "total_assets_yoy": "总资产同比", "equity_yoy": "净资产同比",
    "operate_cost_ratio": "主营业务成本率",
}

# 分组顺序与成员（决定综合段的行文结构）
GROUPS: list[tuple[str, list[str]]] = [
    ("规模", ["revenue", "total_assets", "total_equity"]),
    ("盈利", ["gross_margin", "net_margin", "roe", "roe_weighted", "roa",
              "net_profit", "net_profit_parent", "net_profit_deducted"]),
    ("成长", ["revenue_yoy", "net_profit_yoy", "revenue_cagr_3y"]),
    ("现金流", ["operating_cash_flow", "free_cash_flow", "ocf_to_net_profit"]),
    ("营运效率", ["inventory_turnover", "inventory_days", "receivable_turnover",
                  "receivable_days", "payable_days", "cash_conversion_cycle",
                  "total_asset_turnover"]),
    ("偿债与结构", ["debt_ratio", "current_ratio", "quick_ratio", "interest_coverage",
                    "debt_to_equity", "goodwill"]),
    ("每股", ["eps", "bps", "dividend_per_share"]),
]

# 金额类（元 → 亿元展示）
_AMOUNT_CODES = {
    "revenue", "operate_cost", "gross_profit", "net_profit", "net_profit_parent",
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "free_cash_flow", "total_assets", "total_liabilities", "total_equity",
    "inventory", "receivable",
    "total_operating_cost", "net_profit_deducted", "goodwill",
}
# 比率类（小数 → %）
_RATIO_CODES = {
    "gross_margin", "net_margin", "roe", "roe_weighted", "roa",
    "revenue_yoy", "net_profit_yoy", "revenue_cagr_3y",
    "ocf_to_net_profit", "debt_ratio",
    "roe_avg", "roic", "operating_margin", "effective_tax_rate", "ocf_to_revenue",
    "cash_ratio", "total_assets_yoy", "equity_yoy", "operate_cost_ratio",
}
# 周转次数类
_TIMES_CODES = {"inventory_turnover", "receivable_turnover", "payable_turnover",
                "equity_multiplier", "debt_to_equity", "quick_ratio_conservative",
                "total_asset_turnover", "current_asset_turnover", "fixed_asset_turnover"}
# 天数类
_DAYS_CODES = {"inventory_days", "receivable_days", "payable_days", "cash_conversion_cycle"}
# 倍数类（流动/速动/利息保障）
_MULTIPLE_CODES = {"current_ratio", "quick_ratio", "interest_coverage"}
# 每股类（元/股）
_PER_SHARE_CODES = {"eps", "bps", "dividend_per_share", "ocf_per_share"}
# 人数类
_COUNT_CODES = {"employee_count"}

# period_type → 中文报告名
_PERIOD_CN = {"FY": "年报", "H1": "中报", "Q1": "一季报", "Q3": "三季报"}
_PERIOD_TYPE_ORDER = {"Q1": 0, "H1": 1, "Q3": 2, "FY": 3}

# 异常关注默认阈值（anomaly_rules 为空时的兜底；表内有规则则按规则生效，方案 §4.2）
_DEFAULT_AMOUNT_PCT = 0.50        # 金额类：相对上一报告期波动 ±50%
_DEFAULT_RATIO_PP = 0.10          # 比率类：绝对变化 10 个百分点

# 参与异常检测的核心指标
_ANOMALY_AMOUNT_CODES = ["revenue", "net_profit", "operating_cash_flow"]
_ANOMALY_RATIO_CODES = ["gross_margin", "debt_ratio", "roe"]

# 趋势段覆盖的指标
TREND_METRICS: list[str] = [
    "revenue", "net_profit", "gross_margin", "net_margin", "roe", "operating_cash_flow",
]

_MISSING = "数据缺失"


# ---------- 格式化工具 ----------

def _fmt_value(code: str, v: float | None) -> str | None:
    """按指标类型格式化数值（单位由指标决定，不按数值大小推断）

    修正点：原先"绝对值 ≥1e8 才用亿元"会让小额科目（如个别公司早期营收）在同一指标内
    忽而"元"忽而"亿元"，此处金额一律用亿元。
    """
    if v is None:
        return None
    if code in _AMOUNT_CODES:
        return f"{v / 1e8:,.2f} 亿元" if abs(v) < 1e8 else f"{v / 1e8:,.1f} 亿元"
    if code in _RATIO_CODES:
        return f"{v * 100:.2f}%"
    if code in _TIMES_CODES:
        return f"{v:.2f} 次"
    if code in _DAYS_CODES:
        return f"{v:.1f} 天"
    if code in _MULTIPLE_CODES:
        return f"{v:.2f} 倍"
    if code in _PER_SHARE_CODES:
        return f"{v:,.2f} 元/股"
    if code in _COUNT_CODES:
        return f"{v:,.0f} 人"
    logger.debug(f"指标 {code} 未声明展示类型，按裸数值输出（请补 CORE_LABELS/类型集合）")
    return f"{v:,.2f}"


def _fmt_change(code: str, change: float) -> str:
    """变化幅度：比率类用百分点，其余用百分比"""
    if code in _RATIO_CODES:
        return f"{change * 100:+.2f} 个百分点"
    return f"{change * 100:+.1f}%"


def _suffix_from_code(code: str) -> str:
    """6 位代码 → 交易所后缀（.SH/.SZ/.BJ），已有后缀则原样返回"""
    if "." in code:
        return code
    if code.startswith(("60", "68")):
        return f"{code}.SH"
    if code.startswith(("00", "30")):
        return f"{code}.SZ"
    if code.startswith(("8", "4", "92")):
        return f"{code}.BJ"
    return code


def _period_sort_key(period: str, period_type: str) -> tuple:
    """排序键：年份 → 期次（Q1→H1→Q3→FY）"""
    return (str(period)[:4], _PERIOD_TYPE_ORDER.get(period_type or "FY", 9))


def _scope_text(row: dict) -> str:
    """口径声明：累计/单季 + 合并/母公司"""
    single = "单季" if int(row.get("is_single_quarter") or 0) == 1 else "累计"
    scope = {"consolidated": "合并", "parent": "母公司"}.get(
        str(row.get("report_scope") or "consolidated"), "合并")
    return f"{single}、{scope}口径"


# ---------- 异常阈值（优先 anomaly_rules，表为空用默认值）----------

def _rule_thresholds(rules: list[dict] | None, template: str) -> dict[str, float]:
    """从 anomaly_rules 取每指标阈值（仅采用 compare=qoq + operator=abs_gt 的规则）"""
    out: dict[str, float] = {}
    for r in rules or []:
        if str(r.get("compare") or "qoq") != "qoq":
            continue
        if str(r.get("operator") or "abs_gt") != "abs_gt":
            continue
        scope = str(r.get("industry_scope") or "all")
        if scope not in ("all", template):
            continue
        code, thr = str(r.get("metric_code") or ""), r.get("threshold")
        if code and thr:
            out[code] = float(thr)
    return out


def _threshold_for(code: str, thresholds: dict[str, float]) -> float:
    if code in thresholds:
        return thresholds[code]
    return _DEFAULT_RATIO_PP if code in _RATIO_CODES else _DEFAULT_AMOUNT_PCT


# ---------- 单期文本生成 ----------

def _detect_anomalies(row: dict, prev_row: dict, template: str,
                      thresholds: dict[str, float]) -> list[str]:
    """与上一报告期比较，返回异常描述（方向安全：始终给出两期原值）

    修正点：
    1. 原实现把"与上一披露期比较"写成"同比"，语义错误 → 统一表述为"较上一报告期"
    2. 上期为负（亏损）时，原来的变化率会给出误导性描述 → 现在同时给出两期原值
    """
    out: list[str] = []
    for code in _ANOMALY_AMOUNT_CODES:
        if not is_applicable(code, template):
            continue
        cur, prev = row.get(code), prev_row.get(code)
        if cur is None or prev is None or prev == 0:
            continue
        change = (cur - prev) / abs(prev)
        if abs(change) >= _threshold_for(code, thresholds):
            out.append(
                f"{CORE_LABELS.get(code, code)} {_fmt_value(code, cur)}"
                f"（较上一报告期 {change * 100:+.1f}%，上期 {_fmt_value(code, prev)}）"
            )
    for code in _ANOMALY_RATIO_CODES:
        if not is_applicable(code, template):
            continue
        cur, prev = row.get(code), prev_row.get(code)
        if cur is None or prev is None:
            continue
        delta = cur - prev
        if abs(delta) >= _threshold_for(code, thresholds):
            out.append(
                f"{CORE_LABELS.get(code, code)} 从 {prev * 100:.2f}% 变动至 "
                f"{cur * 100:.2f}%（{_fmt_change(code, delta)}）"
            )
    return out


def textify_period(company: dict, row: dict, period_type: str = "FY",
                   prev_row: dict | None = None,
                   rules: list[dict] | None = None) -> list[dict]:
    """生成一个"公司 × 报告期"的文本片段（overview 必有，anomaly 视情况）

    Args:
        company: repository.get_company() 返回的 dict
        row: repository.list_core_rows() 返回的行 dict（宽表镜像 + 口径字段）
        period_type: 报告期类型（FY/H1/Q1/Q3）
        prev_row: 上一报告期的行 dict（无则 None）
        rules: anomaly_rules（为空则用默认阈值）

    Returns:
        [{"chunk_id", "text", "company_code", "company_name",
          "period", "period_type", "group"}, ...]  （0~2 条）
    """
    code = normalize_code(company["code"])
    name = str(company["name"])
    period = str(row.get("period") or "")
    template = normalize_template(company.get("industry_template"))
    period_cn = _PERIOD_CN.get(period_type, period_type)
    suffix = _suffix_from_code(code)
    thresholds = _rule_thresholds(rules, template)

    # ---- 综合段 ----
    group_parts: list[str] = []
    excluded: list[str] = []
    for gname, codes in GROUPS:
        parts = []
        for c in codes:
            if not is_applicable(c, template):
                if c in CORE_LABELS:
                    excluded.append(c)
                continue
            s = _fmt_value(c, row.get(c))
            if s is not None:
                parts.append(f"{CORE_LABELS.get(c, c)} {s}")
        if parts:
            group_parts.append(f"{gname}方面，{'，'.join(parts)}。")
    if not group_parts:
        logger.debug(f"[{code} {period}] 无可展示指标，跳过综合段")
        return []
    text = (f"{name}（{suffix}）{period_cn}（报告期 {period}，{_scope_text(row)}）："
            + "".join(group_parts))
    if excluded:
        text += "（注：该行业模板下毛利率、存货/应收类经营性指标不适用，已排除。）"

    chunks = [{
        "chunk_id": f"{code}_{period}_overview",
        "text": text,
        "company_code": code,
        "company_name": name,
        "period": period,
        "period_type": period_type,
        "group": "overview",
    }]

    # ---- 异常关注段 ----
    if prev_row is not None:
        anomalies = _detect_anomalies(row, prev_row, template, thresholds)
        if anomalies:
            a_text = (f"{name}（{suffix}）{period_cn}（{period}）异常关注："
                      + "；".join(anomalies) + "。")
            chunks.append({
                "chunk_id": f"{code}_{period}_anomaly",
                "text": a_text,
                "company_code": code,
                "company_name": name,
                "period": period,
                "period_type": period_type,
                "group": "anomaly",
            })
    return chunks


# ---------- 跨期趋势段 ----------

def textify_trend(company: dict, rows: list[dict],
                  years: int = 3, rules: list[dict] | None = None) -> list[dict]:
    """生成"近 N 年年报"趋势片段（回答"近三年毛利率趋势"类问题）

    Args:
        company: 公司 dict
        rows: 该公司全部宽表行 dict
        years: 窗口年数（3 / 5）

    Returns:
        0~1 条 chunk（年报不足 2 期时不生成）
    """
    fy_rows = [r for r in rows if str(r.get("period_type") or "") == "FY"]
    fy_rows.sort(key=lambda r: _period_sort_key(str(r.get("period")), "FY"))
    window = fy_rows[-years:]
    if len(window) < 2:
        logger.debug(f"[{company['code']}] 年报期数不足 {years} 期，跳过趋势段")
        return []

    template = normalize_template(company.get("industry_template"))
    code = normalize_code(company["code"])
    name = str(company["name"])
    suffix = _suffix_from_code(code)
    years_text = "、".join(str(r.get("period"))[:4] for r in window)

    clauses: list[str] = []
    for m in TREND_METRICS:
        if not is_applicable(m, template):
            continue
        pairs = [(str(r.get("period"))[:4], r.get(m)) for r in window]
        if not any(v is not None for _, v in pairs):
            continue
        series = " → ".join(
            f"{y} 年 {_fmt_value(m, v)}" if v is not None else f"{y} 年 {_MISSING}"
            for y, v in pairs
        )
        first = next((v for _, v in pairs if v is not None), None)
        last = next((v for _, v in reversed(pairs) if v is not None), None)
        delta = ""
        if first is not None and last is not None and len(pairs) >= 2:
            if m in _RATIO_CODES:
                delta = f"（区间变化 {_fmt_change(m, last - first)}）"
            elif first:
                delta = f"（区间变化 {(last - first) / abs(first) * 100:+.1f}%）"
        clauses.append(f"{CORE_LABELS.get(m, m)} {series}{delta}")

    if not clauses:
        return []
    text = (
        f"{name}（{suffix}）近 {len(window)} 个年报期主要指标趋势"
        f"（覆盖 {years_text}，年报累计、合并口径）：" + "；".join(clauses) + "。"
    )
    return [{
        "chunk_id": f"{code}_trend_{len(window)}y",
        "text": text,
        "company_code": code,
        "company_name": name,
        "period": str(window[-1].get("period")),
        "period_type": "FY",
        "group": "trend",
    }]


# ---------- 批量入口 ----------

def textify_company(code: str, db=None, include_trend: bool = True,
                    rules: list[dict] | None = None) -> list[dict]:
    """生成单家公司的全部文本片段（按时间先后），含趋势段"""
    db = db or get_db()
    company = db.get_company(code=code)
    if not company:
        logger.warning(f"公司不存在: {code}")
        return []
    rows = db.list_core_rows(company["code"])
    rows.sort(key=lambda r: _period_sort_key(str(r.get("period")), str(r.get("period_type"))))
    chunks: list[dict] = []
    prev: dict | None = None
    for row in rows:
        chunks.extend(textify_period(
            company, row, period_type=str(row.get("period_type") or "FY"),
            prev_row=prev, rules=rules,
        ))
        prev = row
    if include_trend:
        for n in (3, 5):
            chunks.extend(textify_trend(company, rows, years=n, rules=rules))
    return chunks


def textify_all(db=None, limit: int | None = None,
                companies: list[str] | None = None,
                include_trend: bool = True) -> list[dict]:
    """生成全量（或指定公司集）文本片段

    Args:
        db: Database 实例（默认单例）
        limit: 最多生成多少家公司（None=全部）
        companies: 指定公司代码列表（优先于 limit）
        include_trend: 是否生成近 3 年 / 近 5 年趋势段
    """
    db = db or get_db()
    codes = db.list_company_codes()
    if companies:
        wanted = {normalize_code(c) for c in companies}
        codes = [c for c in codes if c in wanted]
    if limit:
        codes = codes[:limit]
    rules = db.list_anomaly_rules()
    if not rules:
        logger.info("anomaly_rules 为空，异常段使用内置默认阈值（50% / 10 个百分点）")

    chunks: list[dict] = []
    for code in codes:
        chunks.extend(textify_company(code, db=db, include_trend=include_trend, rules=rules))
    logger.info(f"文本化完成：{len(codes)} 家公司 → {len(chunks)} 个片段")
    return chunks


def textify_and_index(db=None, limit: int | None = None,
                      companies: list[str] | None = None,
                      include_trend: bool = True,
                      reindex: bool = False,
                      dry: bool = False) -> int:
    """生成文本片段并写入 ChromaDB，返回写入条数

    Args:
        reindex: True 时先清空整个集合（全量重建）
        dry: True 时只生成不入库（离线检查文本质量）
    """
    chunks = textify_all(db=db, limit=limit, companies=companies, include_trend=include_trend)
    if dry:
        for c in chunks[:2]:
            logger.info(f"[dry] {c['chunk_id']} :: {c['text'][:160]}...")
        return len(chunks)
    if not chunks:
        return 0
    from app.domains.retrieval.vector import get_vector_store
    store = get_vector_store()
    if reindex:
        store.clear()
    elif companies:
        # 指定公司时先删旧片段，避免历史 chunk_id 残留（如趋势段数量变化）
        for c in companies:
            store.delete_by_company_code(normalize_code(c))
    store.add_chunks(chunks)
    return len(chunks)


if __name__ == "__main__":
    # 用法：
    #   python -m app.domains.financial.textify --dry --limit 2
    #   python -m app.domains.financial.textify --company 300750
    #   python -m app.domains.financial.textify --all --reindex
    import sys

    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(0)

    dry = "--dry" in argv
    reindex = "--reindex" in argv
    no_trend = "--no-trend" in argv
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    picks: list[str] = []
    if "--company" in argv:
        i = argv.index("--company")
        picks = [a for a in argv[i + 1:] if not a.startswith("--")]

    if picks:
        n = textify_and_index(companies=picks, include_trend=not no_trend,
                              reindex=reindex, dry=dry)
    elif "--all" in argv:
        n = textify_and_index(limit=limit, include_trend=not no_trend,
                              reindex=reindex, dry=dry)
    else:
        print(__doc__)
        raise SystemExit(0)
    print(f"片段数：{n}" + ("（dry：未写入向量库）" if dry else ""))
