"""杜邦分解：ROE = 净利率 × 总资产周转率 × 权益乘数

口径纪律（方案 §5.1）：
1. **必须同源重算三因子再相乘**，不要拿报表 ROE 与三个因子混着用
2. 期末口径（不做平均余额），并把"与报表/归母口径的差异"显式报出来
3. 差异 > 1 个百分点要在结论里标注，不能悄悄抹平

纯计算 + 一次读库，结果可单测（给定宽表行即可复算）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.domains.financial.repository import get_db

DIFF_WARN_PP = 1.0     # 与报表 ROE 相差超过 1 个百分点就提示口径差异


@dataclass
class Dupont:
    company: str
    name: str
    period: str
    net_margin: float            # 净利润 / 营收
    asset_turnover: float        # 营收 / 总资产
    equity_multiplier: float     # 总资产 / 总权益
    roe: float                   # 三因子乘积（同源口径）
    roe_parent: float | None     # 归母口径：归母净利 / 归母权益
    roe_reported: float | None   # 宽表 roe
    factor_check: float          # |乘积 - roe|，应约等于 0
    diff_vs_reported_pp: float | None   # 与宽表 roe 的差（百分点）
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def compute(row: dict) -> Dupont | None:
    """由一条宽表行算出杜邦三因子；缺因子返回 None（不猜、不补）"""
    net_profit = row.get("net_profit")
    revenue = row.get("revenue")
    assets = row.get("total_assets")
    equity = row.get("total_equity")
    if not revenue or not assets or not equity:
        return None
    if net_profit is None:
        return None

    net_margin = net_profit / revenue
    turnover = revenue / assets
    multiplier = assets / equity
    roe = net_margin * turnover * multiplier
    notes: list[str] = []

    roe_parent = None
    if row.get("net_profit_parent") is not None and row.get("total_parent_equity"):
        roe_parent = row["net_profit_parent"] / row["total_parent_equity"]

    roe_reported = row.get("roe")
    diff_pp = None
    if roe_reported is not None:
        diff_pp = round((roe - roe_reported) * 100, 2)
        if abs(diff_pp) > DIFF_WARN_PP:
            notes.append(
                f"同源重算 ROE 与宽表 ROE 相差 {diff_pp:+.2f} 个百分点"
                f"（口径差异：前者用总权益，后者用归母权益）")

    return Dupont(
        company=row.get("company_code") or "",
        name=row.get("company_name") or "",
        period=str(row.get("period") or ""),
        net_margin=round(net_margin, 6),
        asset_turnover=round(turnover, 6),
        equity_multiplier=round(multiplier, 6),
        roe=round(roe, 6),
        roe_parent=None if roe_parent is None else round(roe_parent, 6),
        roe_reported=None if roe_reported is None else round(float(roe_reported), 6),
        factor_check=round(abs(net_margin * turnover * multiplier - roe), 12),
        diff_vs_reported_pp=diff_pp,
        notes=notes,
    )


def dupont(company: str, period: str | None = None, *, db=None) -> Dupont | None:
    """取某公司某期（缺省最近一期）的杜邦分解"""
    db = db or get_db()
    comp = db.get_company(code=company) or db.get_company(name=company)
    if not comp:
        return None
    rows = db.list_core_rows(comp["code"])
    if not rows:
        return None
    want = (period or "").strip().upper()
    row = next((r for r in rows if str(r.get("period") or "").upper() == want), None) if want else rows[0]
    if row is None:
        return None
    row = dict(row)
    row["company_code"] = comp["code"]
    row["company_name"] = comp.get("name") or ""
    return compute(row)
