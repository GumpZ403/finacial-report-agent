"""三表勾稽异常检测

设计原则（方案 §6.4 / §5.1）：
1. **脚本规则，不是 LLM 判断**：每条规则都是确定性的比较，可复现、可单测
2. **无 evidence 不出结论**：每条异常必须带上用于判定的原始数值
3. 只报"值得看一眼"的，不报"也许可能"的：阈值写死在 THRESHOLDS 里，便于调

当前规则（都能用三表数据直接算）：
    receivable_gap  应收账款增速 − 营收增速 > 20pp   （放宽信用换收入？）
    inventory_gap   存货增速 − 营业成本增速 > 20pp   （备货积压？）
    ocf_to_profit   经营现金流 / 净利润 < 0.5 且净利润 > 0  （利润没有现金支撑？）
    yoy_jump        营收或净利同比绝对值 > 50pp        （大起大落，需要解释）
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.domains.financial.repository import get_db

THRESHOLDS = {
    "receivable_gap_pp": 20.0,
    "inventory_gap_pp": 20.0,
    "ocf_to_profit_min": 0.5,
    "yoy_jump_pp": 50.0,
}

METRIC_LABEL = {
    "receivable": "应收账款", "revenue": "营业收入",
    "inventory": "存货", "operate_cost": "营业成本",
    "operating_cash_flow": "经营现金流", "net_profit": "净利润",
}


@dataclass
class Anomaly:
    rule: str
    severity: str            # high / medium
    period: str
    message: str
    observed: float
    baseline: float
    delta: float
    unit: str = ""
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _prev_year(rows: list[dict], idx: int) -> dict | None:
    """同一 period_type 的上一年（2024FY → 2023FY）"""
    period = str(rows[idx].get("period") or "")
    if len(period) < 5 or not period[:4].isdigit():
        return None
    prev = f"{int(period[:4]) - 1}{period[4:]}"
    for r in rows:
        if str(r.get("period") or "") == prev:
            return r
    return None


def _growth(cur, prev) -> float | None:
    try:
        cur, prev = float(cur), float(prev)
    except (TypeError, ValueError):
        return None
    if prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _yoy(rows: list[dict], idx: int, metric: str) -> tuple[float, float, dict | None] | None:
    """返回 (当期值, 同比, 上一年行)"""
    cur_row = rows[idx]
    prev_row = _prev_year(rows, idx)
    if prev_row is None:
        return None
    cur = cur_row.get(metric)
    prev = prev_row.get(metric)
    if cur is None or prev is None:
        return None
    g = _growth(cur, prev)
    if g is None:
        return None
    return float(cur), g, prev_row


# ---------------------------------------------------------------- 规则

def check_receivable_gap(rows: list[dict], idx: int) -> Anomaly | None:
    rec = _yoy(rows, idx, "receivable")
    rev = _yoy(rows, idx, "revenue")
    if not rec or not rev:
        return None
    gap_pp = (rec[1] - rev[1]) * 100
    if gap_pp <= THRESHOLDS["receivable_gap_pp"]:
        return None
    period = rows[idx].get("period")
    return Anomaly(
        rule="receivable_gap", severity="high", period=str(period),
        message=(f"应收账款同比 {rec[1] * 100:+.1f}%，营收同比 {rev[1] * 100:+.1f}%，"
                 f"相差 {gap_pp:+.1f} 个百分点——收入增长可能依赖放宽信用"),
        observed=round(rec[1] * 100, 2), baseline=round(rev[1] * 100, 2),
        delta=round(gap_pp, 2), unit="pp",
        evidence=[
            {"metric": "receivable", "period": period, "value": rec[0]},
            {"metric": "revenue", "period": period, "value": rev[0]},
        ],
    )


def check_inventory_gap(rows: list[dict], idx: int) -> Anomaly | None:
    inv = _yoy(rows, idx, "inventory")
    cost = _yoy(rows, idx, "operate_cost")
    if not inv or not cost:
        return None
    gap_pp = (inv[1] - cost[1]) * 100
    if gap_pp <= THRESHOLDS["inventory_gap_pp"]:
        return None
    period = rows[idx].get("period")
    return Anomaly(
        rule="inventory_gap", severity="medium", period=str(period),
        message=(f"存货同比 {inv[1] * 100:+.1f}%，营业成本同比 {cost[1] * 100:+.1f}%，"
                 f"相差 {gap_pp:+.1f} 个百分点——库存积压或备货节奏变化"),
        observed=round(inv[1] * 100, 2), baseline=round(cost[1] * 100, 2),
        delta=round(gap_pp, 2), unit="pp",
        evidence=[
            {"metric": "inventory", "period": period, "value": inv[0]},
            {"metric": "operate_cost", "period": period, "value": cost[0]},
        ],
    )


def check_ocf_to_profit(rows: list[dict], idx: int, *, ratio: float | None = None) -> Anomaly | None:
    ratio = rows[idx].get("ocf_to_net_profit") if ratio is None else ratio
    net_profit = rows[idx].get("net_profit")
    if ratio is None or net_profit is None or float(net_profit) <= 0:
        return None
    if float(ratio) >= THRESHOLDS["ocf_to_profit_min"]:
        return None
    period = rows[idx].get("period")
    return Anomaly(
        rule="ocf_to_profit", severity="high", period=str(period),
        message=(f"净现比（经营现金流/净利润）{float(ratio):.2f} < "
                 f"{THRESHOLDS['ocf_to_profit_min']}——利润的现金支撑偏弱"),
        observed=round(float(ratio), 4), baseline=THRESHOLDS["ocf_to_profit_min"],
        delta=round(float(ratio) - THRESHOLDS["ocf_to_profit_min"], 4), unit="倍",
        evidence=[
            {"metric": "ocf_to_net_profit", "period": period, "value": float(ratio)},
            {"metric": "net_profit", "period": period, "value": float(net_profit)},
        ],
    )


def check_yoy_jump(rows: list[dict], idx: int) -> list[Anomaly]:
    out = []
    period = rows[idx].get("period")
    for metric in ("revenue_yoy", "net_profit_yoy"):
        value = rows[idx].get(metric)
        if value is None:
            continue
        pp = float(value) * 100
        if abs(pp) <= THRESHOLDS["yoy_jump_pp"]:
            continue
        out.append(Anomaly(
            rule="yoy_jump", severity="medium", period=str(period),
            message=f"{METRIC_LABEL.get(metric.replace('_yoy', ''), metric)}同比 {pp:+.1f}%，波动较大，需结合公告解释",
            observed=round(pp, 2), baseline=THRESHOLDS["yoy_jump_pp"],
            delta=round(abs(pp) - THRESHOLDS["yoy_jump_pp"], 2), unit="pp",
            evidence=[{"metric": metric, "period": period, "value": float(value)}],
        ))
    return out


RULES = [check_receivable_gap, check_inventory_gap, check_ocf_to_profit]


def detect(rows: list[dict], periods: int = 4) -> list[Anomaly]:
    """对最近 N 期逐期跑规则（纯函数，rows 按期次倒序）"""
    found: list[Anomaly] = []
    for idx in range(min(periods, len(rows))):
        for rule in RULES:
            hit = rule(rows, idx)
            if hit:
                found.append(hit)
        found.extend(check_yoy_jump(rows, idx))
    found.sort(key=lambda a: (a.period, a.severity != "high"), reverse=True)
    return found


def detect_anomalies(company: str, periods: int = 4, *, db=None) -> list[Anomaly]:
    db = db or get_db()
    comp = db.get_company(code=company) or db.get_company(name=company)
    if not comp:
        return []
    return detect(db.list_core_rows(comp["code"]), periods)
