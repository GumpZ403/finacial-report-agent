"""采集层（ingest）离线测试：不联网、不碰真实库

覆盖 P0 修复：
- FetchResult：逐表状态 → partial / fetch_status（三表部分失败必须可见）
- save_raw：快照命名（时间戳 + checksum）与内容去重（不再覆盖同一文件）
- load_raw：取最新快照 / 兼容 v1 单文件
- raw_dir 走 settings（不再漂移到 backend/app/domains/storage/raw）
- build_reports：窗口裁剪、_YOY 换算、别名首值优先
"""
import json
import sys
import types
from datetime import timedelta

import pytest

from app.domains.financial.ingest import akshare_em as ing
from app.domains.financial.dates import years_ago
from app.domains.financial.ingest.akshare_em import (
    AkshareEMAdapter,
    FetchResult,
    _compact_ts,
    _em_symbol,
    _normalize_end,
    _period_from_end,
)

RAW_V3 = {
    "version": 3,
    "code": "600519",
    "fetched_at": "2026-10-17 16:44:30",
    "params": {"symbol": "SH600519",
               "fetch_status": {"balance": "ok", "profit": "ok", "cashflow": "ok"}},
    "em_balance": {"columns": [], "rows": []},
    "em_profit": {"columns": [], "rows": []},
    "em_cashflow": {"columns": [], "rows": []},
}


class FakeRawDB:
    """只记录 raw_financial_payload 登记调用，不落真库"""

    def __init__(self):
        self.calls: list[dict] = []

    def record_raw_payload(self, code, path, **kwargs):
        self.calls.append({"code": code, "path": path, **kwargs})
        return len(self.calls)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    fake = FakeRawDB()
    monkeypatch.setattr(ing, "get_db", lambda: fake)
    # backoff_base=0：重试不睡觉，否则每个失败用例要等 1+2 秒
    a = AkshareEMAdapter(raw_dir=tmp_path / "raw", delay=0, backoff_base=0)
    a.fake_db = fake
    return a


# --------------------------------------------------------------- 纯函数
class TestHelpers:
    @pytest.mark.parametrize("code,symbol", [
        ("600519", "SH600519"), ("000002", "SZ000002"),
        ("300750", "SZ300750"), ("830799", "BJ830799"),
    ])
    def test_em_symbol(self, code, symbol):
        assert _em_symbol(code) == symbol

    @pytest.mark.parametrize("end,period,ptype", [
        ("2024-12-31", "2024FY", "FY"), ("2024-03-31", "2024Q1", "Q1"),
        ("2024-06-30", "2024H1", "H1"), ("2024-09-30", "2024Q3", "Q3"),
        ("20241231", "2024FY", "FY"),
    ])
    def test_period_from_end(self, end, period, ptype):
        assert _period_from_end(end) == (period, ptype)

    def test_period_from_end_rejects_garbage(self):
        with pytest.raises(ValueError):
            _period_from_end("2024")

    @pytest.mark.parametrize("raw,expect", [
        ("20241231", "2024-12-31"),
        ("2024/12/31", "2024-12-31"),
        ("2024-12-31 00:00:00", "2024-12-31"),
        ("", None), (None, None), ("abc", None),
    ])
    def test_normalize_end(self, raw, expect):
        assert _normalize_end(raw) == expect

    def test_compact_ts(self):
        assert _compact_ts("2026-10-17 16:44:30") == "20261017-164430"

    def test_compact_ts_falls_back_to_now(self):
        ts = _compact_ts("")
        assert len(ts) == 15 and ts[8] == "-"


# ------------------------------------------------------- FetchResult 语义
class TestFetchResult:
    def test_all_ok(self):
        r = FetchResult("600519", [], {"balance": "ok", "profit": "ok", "cashflow": "ok"}, "t")
        assert not r.partial and r.failed == [] and r.fetch_status == "ok"

    def test_one_failed(self):
        r = FetchResult("600519", [], {"balance": "ok", "profit": "ok", "cashflow": "failed"}, "t")
        assert r.partial and r.failed == ["cashflow"]
        assert r.fetch_status == "partial:cashflow"

    def test_two_failed_keeps_stable_order(self):
        r = FetchResult("600519", [], {"balance": "failed", "profit": "ok", "cashflow": "failed"}, "t")
        assert r.fetch_status == "partial:balance,cashflow"


# ------------------------------------------------ 原始响应快照（P0-2）
class TestRawSnapshot:
    def _adapter_with_raw(self, adapter, status=None):
        adapter._last_raw = {
            **RAW_V3,
            "params": {**RAW_V3["params"],
                       "fetch_status": status or {"balance": "ok", "profit": "ok", "cashflow": "ok"}},
        }
        return adapter

    def test_snapshot_path_is_timestamp_and_checksum(self, adapter):
        self._adapter_with_raw(adapter)
        path = adapter.save_raw("600519")
        assert path.parent.name == "600519"
        assert path.name.startswith("20261017-164430-")
        assert path.name.endswith(".json.gz") and path.exists()
        # 不再写到 raw/<code>.json 这种固定路径
        assert not (adapter.raw_dir / "600519.json").exists()

    def test_same_content_reuses_snapshot(self, adapter):
        self._adapter_with_raw(adapter)
        p1 = adapter.save_raw("600519")
        p2 = adapter.save_raw("600519")
        assert p1 == p2
        assert len(list((adapter.raw_dir / "600519").glob("*.json*"))) == 1
        # 重复内容也只登记一次（去重交给 repository，这里只调一次/相同 checksum）
        checksums = {c["checksum"] for c in adapter.fake_db.calls}
        assert len(checksums) == 1

    def test_changed_content_creates_new_snapshot(self, adapter):
        self._adapter_with_raw(adapter)
        p1 = adapter.save_raw("600519")
        self._adapter_with_raw(adapter, status={"balance": "ok", "profit": "failed", "cashflow": "ok"})
        p2 = adapter.save_raw("600519")
        assert p2 != p1 and p2.exists() and p1.exists()
        assert len(list((adapter.raw_dir / "600519").glob("*.json*"))) == 2

    def test_load_raw_returns_latest_snapshot(self, adapter):
        self._adapter_with_raw(adapter)
        adapter.save_raw("600519")
        self._adapter_with_raw(adapter, status={"balance": "ok", "profit": "failed", "cashflow": "ok"})
        adapter.save_raw("600519")
        raw = adapter.load_raw("600519")
        assert raw["params"]["fetch_status"]["profit"] == "failed"

    def test_snapshot_is_gzip(self, adapter):
        """快照落盘即压缩：gzip 魔数 + 体积明显小于未压缩 JSON"""
        self._adapter_with_raw(adapter)
        path = adapter.save_raw("600519")
        raw_bytes = path.read_bytes()
        assert raw_bytes[:2] == bytes([0x1F, 0x8B])          # gzip magic
        import gzip as _gzip
        assert json.loads(_gzip.decompress(raw_bytes))["code"] == "600519"

    def test_snapshot_shrinks_vs_plain_json(self, adapter):
        """压缩比说明为什么要 gzip（真实快照 2~3MB → 十分之一）"""
        big_rows = [{"REPORT_DATE": "2024-12-31", f"COL{i}": float(i)} for i in range(200)]
        adapter._last_raw = {**RAW_V3, "em_balance": {"columns": [], "rows": big_rows}}
        path = adapter.save_raw("600519")
        import json as _json
        import gzip as _gzip
        plain = len(_json.dumps(adapter._last_raw, ensure_ascii=False, indent=1).encode("utf-8"))
        assert path.stat().st_size < plain / 3
        assert _gzip.decompress(path.read_bytes())

    def test_load_raw_roundtrip_gz(self, adapter):
        self._adapter_with_raw(adapter)
        adapter.save_raw("600519")
        raw = adapter.load_raw("600519")
        assert raw["version"] == 3
        assert raw["params"]["fetch_status"]["cashflow"] == "ok"

    def test_load_raw_legacy_single_file(self, adapter):
        adapter.raw_dir.mkdir(parents=True, exist_ok=True)
        (adapter.raw_dir / "600519.json").write_text(
            '{"version": 1, "code": "600519", "reports": []}', encoding="utf-8")
        assert adapter.load_raw("600519")["version"] == 1

    def test_load_raw_legacy_list_form(self, adapter):
        adapter.raw_dir.mkdir(parents=True, exist_ok=True)
        (adapter.raw_dir / "600519.json").write_text("[]", encoding="utf-8")
        raw = adapter.load_raw("600519")
        assert raw == {"version": 1, "code": "600519", "reports": []}

    def test_load_raw_missing(self, adapter):
        assert adapter.load_raw("600519") == {}

    def test_default_raw_dir_follows_settings(self):
        a = AkshareEMAdapter(delay=0)
        assert a.raw_dir == ing.get_settings().storage_dir / "raw"
        assert "domains" not in str(a.raw_dir)


# -------------------------------------------- 三表部分失败（P0-1 核心）
class FakeFrame:
    def __init__(self, rows):
        self._rows = rows
        self.columns = list(rows[0]) if rows else []

    def __len__(self):
        return len(self._rows)

    @property
    def shape(self):
        return (len(self._rows), len(self.columns))

    def to_dict(self, orient="records"):
        assert orient == "records"
        return [dict(r) for r in self._rows]


BAL_ROW = {"REPORT_DATE": "2024-12-31", "TOTAL_ASSETS": 5e8}
PROFIT_ROW = {"REPORT_DATE": "2024-12-31", "TOTAL_OPERATE_INCOME": 1e9}
CASH_ROW = {"REPORT_DATE": "2024-12-31", "NETCASH_OPERATE": 2e8}


def _fake_ak(*, balance=True, profit=True, cashflow=True):
    def endpoint(flag, row):
        if flag:
            return lambda symbol: FakeFrame([row])

        def boom(symbol):
            raise RuntimeError("网络抖动")

        return boom

    return types.SimpleNamespace(
        stock_balance_sheet_by_report_em=endpoint(balance, BAL_ROW),
        stock_profit_sheet_by_report_em=endpoint(profit, PROFIT_ROW),
        stock_cash_flow_sheet_by_report_em=endpoint(cashflow, CASH_ROW),
    )


class TestFetchStatus:
    def test_all_ok(self, adapter, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", _fake_ak())
        r = adapter.fetch_indicators("600519")
        assert isinstance(r, FetchResult)
        assert r.status == {"balance": "ok", "profit": "ok", "cashflow": "ok"}
        assert not r.partial and r.fetch_status == "ok"
        assert r.reports and r.reports[0]["period"] == "2024FY"

    def test_one_statement_failing_is_visible(self, adapter, monkeypatch):
        """P0-1：现金流量表挂了，必须有 partial 标记，而不是静默少一堆科目"""
        monkeypatch.setitem(sys.modules, "akshare", _fake_ak(cashflow=False))
        r = adapter.fetch_indicators("600519")
        assert r.status["cashflow"] == "failed"
        assert r.partial and r.fetch_status == "partial:cashflow"
        # 仍然归一化拿到的两张表（不丢可用数据）
        assert r.reports and "total_assets" in r.reports[0]["metrics"]
        # 状态随快照落盘 → 回放时能复现"这份快照本来就不全"
        assert adapter._last_raw["params"]["fetch_status"]["cashflow"] == "failed"

    def test_empty_frame_counts_as_failed(self, adapter, monkeypatch):
        ak = _fake_ak()
        ak.stock_profit_sheet_by_report_em = lambda symbol: FakeFrame([])
        monkeypatch.setitem(sys.modules, "akshare", ak)
        r = adapter.fetch_indicators("600519")
        assert r.status["profit"] == "failed" and r.partial

    def test_all_failed_yields_no_reports(self, adapter, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", _fake_ak(balance=False, profit=False, cashflow=False))
        r = adapter.fetch_indicators("600519")
        assert r.reports == []
        assert r.fetch_status == "partial:balance,profit,cashflow"


# ------------------------------------------------- 重试与退避（P1-4）
class TestRetry:
    def _ak_with(self, endpoint_name, fn):
        ak = _fake_ak()
        setattr(ak, endpoint_name, fn)
        return ak

    def test_retries_then_succeeds(self, adapter, monkeypatch):
        calls = {"n": 0}

        def flaky(symbol):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("网络抖动")
            return FakeFrame([PROFIT_ROW])

        monkeypatch.setitem(sys.modules, "akshare",
                            self._ak_with("stock_profit_sheet_by_report_em", flaky))
        r = adapter.fetch_indicators("600519")
        assert r.status["profit"] == "ok"
        assert calls["n"] == 3

    def test_permanent_error_not_retried(self, adapter, monkeypatch):
        """接口签名变了（TypeError）重试没用，应立刻放弃"""
        calls = {"n": 0}

        def broken(symbol):
            calls["n"] += 1
            raise TypeError("unexpected keyword argument")

        monkeypatch.setitem(sys.modules, "akshare",
                            self._ak_with("stock_profit_sheet_by_report_em", broken))
        r = adapter.fetch_indicators("600519")
        assert r.status["profit"] == "failed"
        assert calls["n"] == 1

    def test_gives_up_after_max_attempts(self, adapter, monkeypatch):
        calls = {"n": 0}

        def always_bad(symbol):
            calls["n"] += 1
            raise ConnectionError("一直失败")

        monkeypatch.setitem(sys.modules, "akshare",
                            self._ak_with("stock_cash_flow_sheet_by_report_em", always_bad))
        r = adapter.fetch_indicators("600519")
        assert r.status["cashflow"] == "failed"
        assert r.partial and calls["n"] == adapter.max_attempts

    def test_empty_frame_is_not_retried(self, adapter, monkeypatch):
        """空表不是异常，按失败处理但不必重试"""
        calls = {"n": 0}

        def empty(symbol):
            calls["n"] += 1
            return FakeFrame([])

        monkeypatch.setitem(sys.modules, "akshare",
                            self._ak_with("stock_profit_sheet_by_report_em", empty))
        r = adapter.fetch_indicators("600519")
        assert r.status["profit"] == "failed" and calls["n"] == 1


# --------------------------------------------- 归一化与窗口（原有行为锁定）
class TestBuildReports:
    def _profit_payload(self, periods, value=100.0):
        return {"columns": ["REPORT_DATE", "TOTAL_OPERATE_INCOME"],
                "rows": [{"REPORT_DATE": p, "TOTAL_OPERATE_INCOME": value} for p in periods]}

    def test_empty_inputs(self, adapter):
        assert adapter.build_reports(None, None, None) == ([], [])

    def test_window_drops_old_periods(self, adapter):
        payload = self._profit_payload(["2000-12-31", "2024-12-31", "2025-12-31"])
        reports, _ = adapter.build_reports(profit=payload)
        assert [r["period"] for r in reports] == ["2024FY", "2025FY"]

    def test_window_can_be_disabled(self, adapter):
        payload = self._profit_payload(["2000-12-31", "2024-12-31"])
        reports, _ = adapter.build_reports(profit=payload, window_years=0)
        assert [r["period"] for r in reports] == ["2000FY", "2024FY"]

    def test_yoy_column_is_percent_to_decimal(self, adapter):
        code = ing.EM_PROFIT_MAP["TOTAL_OPERATE_INCOME_YOY"]
        payload = {"rows": [{"REPORT_DATE": "2024-12-31",
                             "TOTAL_OPERATE_INCOME": 100.0,
                             "TOTAL_OPERATE_INCOME_YOY": 25.0}]}
        reports, _ = adapter.build_reports(profit=payload)
        assert reports[0]["metrics"]["revenue"] == 100.0
        assert reports[0]["metrics"][code] == pytest.approx(0.25)

    def test_alias_first_value_wins(self, adapter):
        """工商 TOTAL_OPERATE_INCOME 优先于银行 OPERATE_INCOME（两者都映射 revenue）"""
        payload = {"rows": [{"REPORT_DATE": "2024-12-31",
                             "TOTAL_OPERATE_INCOME": 100.0,
                             "OPERATE_INCOME": 999.0}]}
        reports, _ = adapter.build_reports(profit=payload)
        assert reports[0]["metrics"]["revenue"] == 100.0

    def test_na_and_garbage_values_skipped(self, adapter):
        payload = {"rows": [{"REPORT_DATE": "2024-12-31",
                             "TOTAL_OPERATE_INCOME": None,
                             "OPERATE_COST": "--",
                             "NETPROFIT": 50.0}]}
        reports, _ = adapter.build_reports(profit=payload)
        metrics = reports[0]["metrics"]
        assert "revenue" not in metrics and "operate_cost" not in metrics
        assert metrics["net_profit"] == 50.0

    def test_window_uses_dates_not_calendar_years(self, adapter):
        """窗口按 period_end 日期判定：恰好落在边界的那一期要保留，早一天才丢"""
        cutoff = years_ago(10)
        inside = cutoff.isoformat()
        outside = (cutoff - timedelta(days=1)).isoformat()
        payload = self._profit_payload([outside, inside])
        reports, _ = adapter.build_reports(profit=payload)
        assert [r["period_end"] for r in reports] == [inside]

    def test_unparseable_period_skipped(self, adapter):
        payload = {"rows": [{"REPORT_DATE": "坏数据", "TOTAL_OPERATE_INCOME": 1.0},
                            {"REPORT_DATE": "2024-12-31", "TOTAL_OPERATE_INCOME": 2.0}]}
        reports, _ = adapter.build_reports(profit=payload)
        assert [r["period"] for r in reports] == ["2024FY"]


# ------------------------------------------------------- 离线回放（P0-1 续）
class FakeReplayDB:
    """只记录复习调用，不落真库"""

    def __init__(self, company=None):
        self.company = company
        self.periods: list[tuple[str, dict]] = []
        self.metrics = 0

    def get_company(self, code=None):
        return self.company

    def upsert_financial_period(self, company_id, period, **kwargs):
        self.periods.append((period, kwargs))
        return len(self.periods)

    def upsert_metrics(self, period_id, metrics, **kwargs):
        self.metrics += len(metrics)
        return len(metrics)

    def derive_financial_core(self, period_id):
        return 0


class TestReplay:
    def _raw_with(self, status, rows):
        return {
            "version": 3, "code": "600519", "fetched_at": "2026-10-17 16:44:30",
            "params": {"symbol": "SH600519", "fetch_status": status},
            "em_balance": {"rows": []},
            "em_profit": {"rows": rows},
            "em_cashflow": {"rows": []},
        }

    def test_partial_snapshot_replays_as_partial(self, adapter, monkeypatch):
        fake = FakeReplayDB(company={"company_id": 1})
        monkeypatch.setattr(ing, "get_db", lambda: fake)
        raw = self._raw_with(
            {"balance": "ok", "profit": "ok", "cashflow": "failed"},
            [{"REPORT_DATE": "2024-12-31", "TOTAL_OPERATE_INCOME": 1e9}],
        )
        out = adapter.replay("600519", raw=raw)
        assert out["reports"] == 1
        assert fake.periods[0][1]["fetch_status"] == "partial:cashflow"

    def test_legacy_snapshot_without_status_treated_as_ok(self, adapter, monkeypatch):
        fake = FakeReplayDB(company={"company_id": 1})
        monkeypatch.setattr(ing, "get_db", lambda: fake)
        raw = self._raw_with(None, [{"REPORT_DATE": "2024-12-31", "TOTAL_OPERATE_INCOME": 1e9}])
        raw["params"].pop("fetch_status")
        adapter.replay("600519", raw=raw)
        assert fake.periods[0][1]["fetch_status"] == "ok"

    def test_v1_snapshot_refuses(self, adapter):
        with pytest.raises(ValueError, match="v1"):
            adapter.replay("600519", raw={"version": 1, "reports": []})

    def test_missing_company_refuses(self, adapter, monkeypatch):
        monkeypatch.setattr(ing, "get_db", lambda: FakeReplayDB(company=None))
        raw = self._raw_with({"balance": "ok", "profit": "ok", "cashflow": "ok"},
                             [{"REPORT_DATE": "2024-12-31", "TOTAL_OPERATE_INCOME": 1e9}])
        with pytest.raises(ValueError, match="未建档"):
            adapter.replay("600519", raw=raw)
