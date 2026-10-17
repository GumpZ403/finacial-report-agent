"""数据层：P0 修复的落库侧

- financial_periods.fetch_status 列（采集完整性，三表部分失败必须可查）
- raw_financial_payload 按 (company_code, checksum) 去重（快照只增不改，登记也不该重复）
- 建档顺序：公司存在后登记的 raw 记录才有 company_id
"""
import types

import pytest
from sqlalchemy import text as sa_text

from app.domains.financial import repository as repo
from app.domains.financial.models import FinancialPeriod, RawFinancialPayload


@pytest.fixture
def db(tmp_path, monkeypatch):
    """独立的临时 SQLite（不碰 storage/financial.db）"""
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setattr(repo, "get_settings", lambda: types.SimpleNamespace(DATABASE_URL=url))
    return repo.Database()


class TestFetchStatusColumn:
    def test_column_exists_after_migration(self, db):
        with db.engine.connect() as conn:
            cols = {r[1] for r in conn.execute(sa_text("PRAGMA table_info(financial_periods)"))}
        assert "fetch_status" in cols

    def test_default_is_ok(self, db):
        cid = db.upsert_company("600519", "贵州茅台")
        pid = db.upsert_financial_period(cid, "2024FY", period_type="FY",
                                         period_end="2024-12-31", source="akshare")
        with db.get_session() as s:
            assert s.query(FinancialPeriod).filter_by(period_id=pid).first().fetch_status == "ok"

    def test_partial_is_persisted(self, db):
        cid = db.upsert_company("600519", "贵州茅台")
        pid = db.upsert_financial_period(cid, "2024FY", period_type="FY",
                                         period_end="2024-12-31", source="akshare",
                                         fetch_status="partial:cashflow")
        with db.get_session() as s:
            row = s.query(FinancialPeriod).filter_by(period_id=pid).first()
            assert row.fetch_status == "partial:cashflow"

    def test_status_overwritten_on_refetch(self, db):
        """重拉成功后应能把 partial 改回 ok（否则一旦失败就永远打标）"""
        cid = db.upsert_company("600519", "贵州茅台")
        pid = db.upsert_financial_period(cid, "2024FY", fetch_status="partial:cashflow")
        db.upsert_financial_period(cid, "2024FY", fetch_status="ok")
        with db.get_session() as s:
            assert s.query(FinancialPeriod).filter_by(period_id=pid).first().fetch_status == "ok"


class TestRawPayloadLedger:
    def test_dedupe_by_checksum(self, db):
        db.upsert_company("600519", "贵州茅台")
        a = db.record_raw_payload("600519", "raw/600519/1-abc.json", checksum="abc")
        b = db.record_raw_payload("600519", "raw/600519/1-abc.json", checksum="abc")
        assert a == b
        with db.get_session() as s:
            assert s.query(RawFinancialPayload).filter_by(checksum="abc").count() == 1

    def test_different_checksum_creates_row(self, db):
        db.upsert_company("600519", "贵州茅台")
        db.record_raw_payload("600519", "p1", checksum="aaa")
        db.record_raw_payload("600519", "p2", checksum="bbb")
        with db.get_session() as s:
            assert s.query(RawFinancialPayload).count() == 2

    def test_company_id_recorded_when_company_exists(self, db):
        """P0-3：先建档再落盘 → company_id 不应为 NULL"""
        cid = db.upsert_company("600519", "贵州茅台")
        rid = db.record_raw_payload("600519", "p", checksum="abc")
        with db.get_session() as s:
            row = s.query(RawFinancialPayload).filter_by(payload_id=rid).first()
            assert row.company_id == cid

    def test_empty_checksum_always_inserts(self, db):
        """老数据/手工登记没 checksum 时不做去重（避免误判为同一份）"""
        a = db.record_raw_payload("600519", "p", checksum="")
        b = db.record_raw_payload("600519", "p", checksum="")
        assert a != b
