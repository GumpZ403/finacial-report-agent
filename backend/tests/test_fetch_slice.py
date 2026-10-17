"""fetch_slice 增量判定（_is_fresh）：什么时候可以跳过一家公司

跳过条件是三个同时成立：期次 == 最新已披露期次、上次采集完整、最近 FRESH_DAYS 天内拉过。
任何一条不满足都必须重拉 —— 少拉一次比多拉一次贵得多。
"""
from datetime import datetime, timedelta

import pytest

from scripts import fetch_slice


class FakeDB:
    def __init__(self, rec=None, boom=None):
        self.rec = rec
        self.boom = boom
        self.asked: list[str] = []

    def get_latest_period(self, code):
        self.asked.append(code)
        if self.boom:
            raise self.boom
        return self.rec


@pytest.fixture(autouse=True)
def _fixed_calendar(monkeypatch):
    """固定"最新已披露期次"，避免测试随真实日期漂移"""
    monkeypatch.setattr(fetch_slice, "latest_disclosed_period", lambda: "2025Q3")


def _rec(period="2025Q3", status="ok", fetched=None):
    return {
        "period": period,
        "period_end": "2025-09-30",
        "fetch_status": status,
        "fetched_at": fetched or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class TestIsFresh:
    def test_nothing_in_db(self):
        assert fetch_slice._is_fresh(FakeDB(None), "600519") is False

    def test_up_to_date_and_complete_and_recent(self):
        assert fetch_slice._is_fresh(FakeDB(_rec()), "600519") is True

    def test_older_period_needs_refetch(self):
        assert fetch_slice._is_fresh(FakeDB(_rec(period="2025H1")), "600519") is False

    def test_partial_needs_refetch(self):
        assert fetch_slice._is_fresh(
            FakeDB(_rec(status="partial:cashflow")), "600519") is False

    def test_stale_timestamp_needs_refetch(self):
        old = (datetime.now() - timedelta(days=fetch_slice.FRESH_DAYS + 1))
        assert fetch_slice._is_fresh(
            FakeDB(_rec(fetched=old.strftime("%Y-%m-%d %H:%M:%S"))), "600519") is False

    def test_unparseable_timestamp_needs_refetch(self):
        assert fetch_slice._is_fresh(FakeDB(_rec(fetched="坏时间")), "600519") is False

    def test_db_error_falls_back_to_refetch(self):
        assert fetch_slice._is_fresh(FakeDB(boom=RuntimeError("db locked")), "600519") is False
