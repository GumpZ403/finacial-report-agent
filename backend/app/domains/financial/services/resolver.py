"""口径解析：把用户说法映射到 metric_code / 公司代码（确定性，不用 LLM）

为什么不用 LLM 做这件事：
- 词典匹配是确定的、可单测的、零 token；LLM 每次结果可能不同，无法做门禁
- 低置信时**返回候选而不是猜**（方案 §5.1 硬要求），LLM 才有机会反问澄清
- 这是 L2 评测的被测对象：同一批 query 反复跑，Top-1 应该是同一个数

匹配策略（confidence 定义）：
    1.0  精确命中中文名 / 别名 / 6 位代码
    0.8  唯一包含匹配（"茅台毛利率" → 毛利率）
    0.5  多个候选（不猜，返回 candidates 让上层澄清）
    0.0  没找到
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domains.financial.repository import get_db

_PUNCT = re.compile(r"[\s，。！？、,.!?（）()【】\[\]：:；;“”\"'的]+")


@dataclass
class Resolution:
    """一次解析的结果"""

    code: str | None = None
    confidence: float = 0.0
    matched_by: str = ""            # exact / alias / contains / code / none
    candidates: list[str] = field(default_factory=list)   # 低置信时的候选项
    label: str = ""                 # 命中的中文名（便于日志与澄清文案）

    @property
    def ok(self) -> bool:
        return self.code is not None and self.confidence >= 0.8

    def to_dict(self) -> dict:
        return {
            "code": self.code, "confidence": round(self.confidence, 2),
            "matched_by": self.matched_by, "candidates": self.candidates,
            "label": self.label,
        }


def _norm(text: str) -> str:
    return _PUNCT.sub("", (text or "").strip().lower())


def resolve_metric(text: str, *, db=None) -> Resolution:
    """指标说法 → metric_code"""
    db = db or get_db()
    raw = (text or "").strip()
    if not raw:
        return Resolution()
    target = _norm(raw)
    meta = db.list_metric_dict()

    # 1) code 直查
    if raw in meta:
        return Resolution(raw, 1.0, "code", label=meta[raw].get("name_cn") or raw)

    # 2) 精确命中中文名 / 别名
    exact: list[str] = []
    for code, m in meta.items():
        names = [m.get("name_cn") or ""] + list(m.get("aliases") or [])
        if any(_norm(n) == target for n in names if n):
            exact.append(code)
    if len(exact) == 1:
        return Resolution(exact[0], 1.0, "exact",
                          label=meta[exact[0]].get("name_cn") or exact[0])
    if len(exact) > 1:
        return Resolution(None, 0.5, "ambiguous", candidates=sorted(exact)[:5])

    # 3) 包含匹配（取最长的名字，避免"净资产收益率"被"收益率"抢走）
    hits: list[tuple[int, str]] = []
    for code, m in meta.items():
        names = [m.get("name_cn") or ""] + list(m.get("aliases") or [])
        for n in names:
            nn = _norm(n)
            if nn and (nn in target or target in nn):
                hits.append((len(nn), code))
    if hits:
        hits.sort(reverse=True)
        best_len, best_code = hits[0]
        same = [c for length, c in hits if length == best_len]
        if len(set(same)) == 1:
            return Resolution(best_code, 0.8, "contains",
                              label=meta[best_code].get("name_cn") or best_code)
        return Resolution(None, 0.5, "ambiguous", candidates=sorted(set(same))[:5])

    return Resolution()


def resolve_company(text: str, *, db=None) -> Resolution:
    """公司说法 → 6 位代码"""
    db = db or get_db()
    raw = (text or "").strip()
    if not raw:
        return Resolution()
    target = _norm(raw)

    m = re.search(r"\d{6}", raw)
    if m and db.get_company(code=m.group(0)):
        return Resolution(m.group(0), 1.0, "code", label=m.group(0))

    comps = []
    for code in db.list_company_codes():
        comp = db.get_company(code=code)
        if comp:
            comps.append(comp)

    exact = [c for c in comps if _norm(c.get("name") or "") == target]
    if len(exact) == 1:
        return Resolution(exact[0]["code"], 1.0, "exact", label=exact[0]["name"])

    hits = [c for c in comps if _norm(c.get("name") or "") and
            (_norm(c["name"]) in target or target in _norm(c["name"]))]
    if len(hits) == 1:
        return Resolution(hits[0]["code"], 0.8, "contains", label=hits[0]["name"])
    if len(hits) > 1:
        # 取名字最长（最具体）的那个；仍有并列则返回候选
        hits.sort(key=lambda c: len(c["name"]), reverse=True)
        if len(hits[0]["name"]) > len(hits[1]["name"]):
            return Resolution(hits[0]["code"], 0.8, "contains", label=hits[0]["name"])
        return Resolution(None, 0.5, "ambiguous",
                          candidates=[c["code"] for c in hits[:5]])
    return Resolution()
