"""取数与溯源：把"某公司某指标某期次的值"包成带来源的结构化对象

为什么单独成服务（而不是写在工具里）：
- 工具是给 LLM 用的入口，服务是给代码用的能力；评测（L3）、后续杜邦/异常都要用它
- 一个数字必须自带 `period / unit / display / estimated / source`，
  否则回答与报告都无法溯源，只能靠模型"记得"
- 单位归一与展示形态统一走 `display.fmt_value`（唯一实现）
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.domains.financial.display import fmt_value
from app.domains.financial.repository import get_db


@dataclass
class Quote:
    """一个可溯源的数字"""

    company: str          # 6 位代码
    name: str             # 公司简称
    metric: str           # metric_code
    label: str            # 中文名
    period: str           # 2025Q1 / 2024FY
    period_end: str       # 2025-03-31
    value: float          # 库内原始值（比率是小数、金额是元）
    unit_type: str        # amount / ratio / times / days / per_share / count
    display: str          # 展示形态（92.11% / 514.43 亿元）
    estimated: bool       # True=系统按公式派生，不是公司直接披露
    source: str           # eastmoney / derived / ...

    def to_dict(self) -> dict:
        return asdict(self)


def _find_company(db, key: str) -> dict | None:
    """6 位代码或中文简称都能定位（名称走精确匹配，避免张冠李戴）"""
    key = (key or "").strip()
    if not key:
        return None
    if re.fullmatch(r"\d{6}", key):
        return db.get_company(code=key)
    return db.get_company(name=key)


def _pick_period(rows: list[dict], period: str | None) -> dict | None:
    """period 精确匹配 → 只给年份则取该年最新一期 → 缺省取最近一期"""
    if not rows:
        return None
    if period:
        want = str(period).strip().upper()
        for r in rows:
            if str(r.get("period") or "").upper() == want:
                return r
        if want.isdigit():
            for r in rows:
                if str(r.get("period") or "").startswith(want):
                    return r
        return None
    return rows[0]


def _build(db, comp: dict, row: dict, metric: str, meta: dict) -> Quote:
    """宽表行 + 长表元信息 → Quote（source / is_estimated 只有长表有）"""
    period_id = row.get("period_id")
    detail = {}
    if period_id is not None:
        detail = (db.get_metrics(period_id) or {}).get(metric) or {}
    unit_type = meta.get("unit_type") or ""
    value = row.get(metric)
    return Quote(
        company=comp["code"],
        name=comp.get("name") or "",
        metric=metric,
        label=meta.get("name_cn") or metric,
        period=str(row.get("period") or ""),
        period_end=str(row.get("period_end") or ""),
        value=float(value),
        unit_type=unit_type,
        display=fmt_value(value, unit_type),
        estimated=bool(detail.get("is_estimated")),
        source=str(detail.get("source") or ""),
    )


def quote(company: str, metric: str, period: str | None = None,
          *, db=None) -> Quote | None:
    """取一个指标的值；查不到返回 None（"无数据"与"不支持"由调用方区分）"""
    db = db or get_db()
    comp = _find_company(db, company)
    if not comp:
        return None
    row = _pick_period(db.list_core_rows(comp["code"]), period)
    if row is None or row.get(metric) is None:
        return None
    meta = db.list_metric_dict().get(metric) or {}
    return _build(db, comp, row, metric, meta)


def series(company: str, metric: str, periods: int = 4, *, db=None) -> list[Quote]:
    """最近 N 期（按期次倒序），供趋势问题使用"""
    db = db or get_db()
    comp = _find_company(db, company)
    if not comp:
        return []
    meta = db.list_metric_dict().get(metric) or {}
    out: list[Quote] = []
    for row in db.list_core_rows(comp["code"]):
        if len(out) >= periods:
            break
        if row.get(metric) is None:
            continue
        out.append(_build(db, comp, row, metric, meta))
    return out


def quote_many(company: str, metrics: list[str], period: str | None = None,
               *, db=None) -> list[Quote]:
    """一次取多个指标（同一期次）"""
    db = db or get_db()
    comp = _find_company(db, company)
    if not comp:
        return []
    rows = db.list_core_rows(comp["code"])
    row = _pick_period(rows, period)
    if row is None:
        return []
    dicts = db.list_metric_dict()
    return [_build(db, comp, row, m, dicts.get(m) or {})
            for m in metrics if row.get(m) is not None]
