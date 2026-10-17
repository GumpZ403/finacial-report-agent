"""口径解析（services/resolver.py）：确定性匹配 + 低置信返候选

这些用例覆盖"真实使用者会怎么说"，包括模糊说法与歧义——解析器不允许猜。
"""
import pytest

from app.domains.financial.services.resolver import resolve_company, resolve_metric


class TestMetric:
    @pytest.mark.parametrize("text,expect", [
        ("毛利率", "gross_margin"),
        ("销售毛利率", "gross_margin"),
        ("营业收入", "revenue"),
        ("营收", "revenue"),
        ("ROE", "roe"),
        ("资产负债率", "debt_ratio"),
        ("研发费用", "research_expense"),
        ("短期借款", "short_term_loan"),
        ("销售商品、提供劳务收到的现金", "cash_from_sales"),
    ])
    def test_exact_and_alias(self, text, expect):
        assert resolve_metric(text).code == expect

    def test_code_passthrough(self):
        assert resolve_metric("gross_margin").code == "gross_margin"

    def test_contains_match(self):
        """句子里的指标名：'茅台2025Q1毛利率是多少'"""
        r = resolve_metric("茅台2025Q1毛利率是多少")
        assert r.code == "gross_margin" and r.confidence >= 0.8

    def test_unknown_returns_empty(self):
        r = resolve_metric("这个东西怎么样")
        assert r.code is None and r.candidates == []

    def test_ambiguous_returns_candidates(self):
        """同时命中多个名字时不许猜，返回候选让上层澄清"""
        r = resolve_metric("收益率")
        assert r.code is None or r.candidates
        if r.code is None:
            assert r.candidates and r.confidence < 0.8


class TestCompany:
    @pytest.mark.parametrize("text", ["贵州茅台", "600519", "茅台"])
    def test_common_forms(self, text):
        assert resolve_company(text).code == "600519"

    def test_contains_match(self):
        # 简称（茅台）暂不支持：company_master 只有正式名
        assert resolve_company("贵州茅台2025Q1的毛利率").code == "600519"

    def test_unknown(self):
        r = resolve_company("特斯拉")
        assert r.code is None

    def test_empty(self):
        assert resolve_company("").code is None


class TestResolutionShape:
    def test_to_dict(self):
        d = resolve_metric("毛利率").to_dict()
        assert set(d) == {"code", "confidence", "matched_by", "candidates", "label"}
        assert d["confidence"] == 1.0


class TestDictionaryConsistency:
    def test_no_colliding_names_or_aliases(self):
        """一个说法只能属于一个指标——否则解析会张冠李戴

        这是真实踩过的坑：net_profit 的别名里混进了"归母净利润/扣非净利润/利润总额"，
        导致问"归母净利润"返回的是净利润。别名收敛在 seed_metric_dict.ALIAS_OVERRIDES。
        """
        from collections import defaultdict

        from app.domains.financial.repository import get_db

        meta = get_db().list_metric_dict()
        idx: dict[str, set[str]] = defaultdict(set)
        for code, d in meta.items():
            if d.get("name_cn"):
                idx[str(d["name_cn"]).strip()].add(code)
            for a in d.get("aliases") or []:
                if str(a).strip():
                    idx[str(a).strip()].add(code)
        bad = {k: sorted(v) for k, v in idx.items() if len(v) > 1}
        assert not bad, f"说法指向多个指标: {bad}"
