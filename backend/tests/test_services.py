"""分析服务单测：杜邦、勾稽异常（纯函数部分用合成数据，端到端用真实库做冒烟）"""
import pytest

from app.domains.financial.services.anomaly import (
    THRESHOLDS, check_inventory_gap, check_ocf_to_profit,
    check_receivable_gap, check_yoy_jump, detect,
)
from app.domains.financial.services.dupont import compute


# ---------------------------------------------------------------- 杜邦
class TestDupont:
    ROW = {
        "period": "2024FY", "company_code": "600519", "company_name": "贵州茅台",
        "net_profit": 100.0, "revenue": 1000.0, "total_assets": 500.0,
        "total_equity": 250.0, "net_profit_parent": 95.0, "total_parent_equity": 240.0,
        "roe": 0.4,
    }

    def test_three_factors(self):
        d = compute(self.ROW)
        assert d.net_margin == pytest.approx(0.1)
        assert d.asset_turnover == pytest.approx(2.0)
        assert d.equity_multiplier == pytest.approx(2.0)
        assert d.roe == pytest.approx(0.4)          # 0.1 × 2 × 2

    def test_product_equals_roe(self):
        """三因子乘积必须严格等于 roe（同源重算的意义）"""
        d = compute(self.ROW)
        assert d.factor_check == pytest.approx(0.0, abs=1e-12)

    def test_parent_scope_reported(self):
        d = compute(self.ROW)
        assert d.roe_parent == pytest.approx(95 / 240)
        assert d.roe_reported == pytest.approx(0.4)

    def test_diff_note_when_scope_differs(self):
        row = dict(self.ROW, roe=0.30)              # 与同源 0.4 差 10pp
        d = compute(row)
        assert d.diff_vs_reported_pp == pytest.approx(10.0)
        assert d.notes and "口径差异" in d.notes[0]

    @pytest.mark.parametrize("missing", ["revenue", "total_assets", "total_equity", "net_profit"])
    def test_missing_factor_returns_none(self, missing):
        row = dict(self.ROW)
        row[missing] = None
        assert compute(row) is None


# ---------------------------------------------------------------- 勾稽异常
def _row(period="2024FY", **kw):
    base = {"period": period, "receivable": 100, "revenue": 1000, "inventory": 100,
            "operate_cost": 500, "net_profit": 100, "ocf_to_net_profit": 1.0,
            "revenue_yoy": 0.1, "net_profit_yoy": 0.1}
    base.update(kw)
    return base


class TestAnomalyRules:
    def test_receivable_gap_triggers(self):
        rows = [_row(period="2024FY", receivable=200, revenue=1100),
                _row(period="2023FY", receivable=100, revenue=1000)]
        a = check_receivable_gap(rows, 0)
        assert a and a.rule == "receivable_gap" and a.severity == "high"
        assert a.delta > THRESHOLDS["receivable_gap_pp"]
        assert len(a.evidence) == 2

    def test_receivable_gap_below_threshold(self):
        rows = [_row(period="2024FY", receivable=110, revenue=1100),
                _row(period="2023FY", receivable=100, revenue=1000)]
        assert check_receivable_gap(rows, 0) is None

    def test_inventory_gap_triggers(self):
        rows = [_row(period="2024FY", inventory=200, operate_cost=520),
                _row(period="2023FY", inventory=100, operate_cost=500)]
        a = check_inventory_gap(rows, 0)
        assert a and a.rule == "inventory_gap" and a.severity == "medium"

    def test_ocf_to_profit_triggers(self):
        rows = [_row(ocf_to_net_profit=0.2)]
        a = check_ocf_to_profit(rows, 0)
        assert a and a.rule == "ocf_to_profit"

    def test_ocf_to_profit_skipped_when_loss(self):
        """亏损公司不看净现比（负数比值没有意义）"""
        rows = [_row(net_profit=-50, ocf_to_net_profit=0.2)]
        assert check_ocf_to_profit(rows, 0) is None

    def test_yoy_jump(self):
        rows = [_row(period="2024FY", revenue_yoy=0.8, net_profit_yoy=0.05)]
        hits = check_yoy_jump(rows, 0)
        assert len(hits) == 1 and hits[0].rule == "yoy_jump"

    def test_detect_orders_and_collects(self):
        rows = [_row(period="2024FY", receivable=300, revenue=1000, ocf_to_net_profit=0.1),
                _row(period="2023FY", receivable=100, revenue=1000)]
        found = detect(rows, periods=2)
        rules = {a.rule for a in found}
        assert "receivable_gap" in rules and "ocf_to_profit" in rules

    def test_no_previous_year_is_safe(self):
        assert check_receivable_gap([_row(period="2024FY")], 0) is None


# ---------------------------------------------------------------- 真实库冒烟
class TestRealDataSmoke:
    def test_dupont_on_real_company(self):
        from app.domains.financial.services.dupont import dupont
        d = dupont("600519")
        assert d and 0 < d.roe < 1 and d.factor_check < 1e-9

    def test_anomalies_on_real_company(self):
        from app.domains.financial.services.anomaly import detect_anomalies
        assert isinstance(detect_anomalies("600519"), list)
