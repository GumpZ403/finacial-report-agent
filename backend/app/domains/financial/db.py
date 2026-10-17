"""数据库引擎与结构迁移（存储管道，不含业务读写）

- 引擎构建 / 会话工厂
- 幂等迁移：补列（SQLite 不能给既有表加约束）/ 补索引 / 重建约束冲突的表 / 清理遗留空表

业务读写一律在 repository.py；本模块不 import repository，避免环。
新增列或索引只改这里的 REQUIRED_COLUMNS / REQUIRED_INDEXES。
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.domains.financial.models import Base

# ---------- 迁移清单 ----------

REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "financial_periods": {"fetch_status": "VARCHAR(80) DEFAULT 'ok'"},
    "financial_metrics": {"source": "VARCHAR(20) DEFAULT ''",
                         "updated_at": "VARCHAR(30) DEFAULT ''"},
    "financial_core": {
        "net_profit_parent": "FLOAT",
        "roe_weighted": "FLOAT",
        "inventory_days": "FLOAT",
        "receivable_days": "FLOAT",
        "payable_days": "FLOAT",
        # 2026-09-22 扩展批次（用户决定进核心宽表）
        "total_operating_cost": "FLOAT",
        "net_profit_deducted": "FLOAT",
        "goodwill": "FLOAT",
        "ocf_per_share": "FLOAT",
        "roe_avg": "FLOAT",
        "roic": "FLOAT",
        "operating_margin": "FLOAT",
        "effective_tax_rate": "FLOAT",
        "ocf_to_revenue": "FLOAT",
        "equity_multiplier": "FLOAT",
        "debt_to_equity": "FLOAT",
        "cash_ratio": "FLOAT",
        "quick_ratio_conservative": "FLOAT",
        "total_asset_turnover": "FLOAT",
        "current_asset_turnover": "FLOAT",
        "fixed_asset_turnover": "FLOAT",
        "total_assets_yoy": "FLOAT",
        "equity_yoy": "FLOAT",
        # 2026-09-26 ③ formula 派生支撑科目（东财三表直给；列名==metric_code）
        "total_current_assets": "FLOAT",
        "total_current_liab": "FLOAT",
        "total_parent_equity": "FLOAT",
        "operating_profit": "FLOAT",
        "income_tax": "FLOAT",
        "total_profit": "FLOAT",
        "finance_expense": "FLOAT",
        "sale_expense": "FLOAT",
        "manage_expense": "FLOAT",
        "monetary_funds": "FLOAT",
        "accounts_payable": "FLOAT",
        "fixed_asset": "FLOAT",
        "capex": "FLOAT",
    },
    "raw_financial_payload": {"company_code": "VARCHAR(20) DEFAULT ''"},
}

REQUIRED_INDEXES: dict[str, str] = {
    "ix_metrics_metric_code":
        "CREATE INDEX IF NOT EXISTS ix_metrics_metric_code ON financial_metrics(metric_code)",
    "ix_metrics_source":
        "CREATE INDEX IF NOT EXISTS ix_metrics_source ON financial_metrics(source)",
}

# ---------- 原始响应登记表（重建用）----------

LEGACY_TABLES = ("news_records", "tags", "news_tags")

# raw_financial_payload 的目标 DDL（SQLite 无法修改已有列的约束，需要重建表时使用）
_RAW_TABLE_DDL = """
CREATE TABLE raw_financial_payload (
    payload_id   INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER,
    company_code VARCHAR(20) DEFAULT '',
    source       VARCHAR(20) DEFAULT 'akshare',
    api_name     VARCHAR(100) DEFAULT '',
    params       JSON DEFAULT '{}',
    payload_path VARCHAR(300) DEFAULT '',
    checksum     VARCHAR(64) DEFAULT '',
    fetched_at   VARCHAR(30) DEFAULT ''
)
"""
_RAW_TABLE_COLUMNS = (
    "payload_id", "company_id", "company_code", "source", "api_name",
    "params", "payload_path", "checksum", "fetched_at",
)


# ---------- 管道函数 ----------

def build_engine(database_url: str) -> Engine:
    """按连接串建引擎（sqlite 需要放开线程检查，FastAPI 是多线程的）"""
    return create_engine(
        database_url,
        echo=False,
        connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
    )


def make_session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False)


def migrate(engine: Engine) -> dict:
    """幂等迁移：补列 → 补索引 → 清理遗留空表"""
    result: dict[str, list[str]] = {
        "added_columns": [], "created_indexes": [],
        "dropped_tables": [], "rebuilt_tables": [], "data_fixes": [],
    }
    with engine.begin() as conn:
        for table, columns in REQUIRED_COLUMNS.items():
            exists = conn.execute(sa_text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
            ), {"t": table}).first()
            if not exists:
                continue
            have = {row[1] for row in conn.execute(sa_text(f"PRAGMA table_info({table})"))}
            for col, ddl in columns.items():
                if col in have:
                    continue
                conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                result["added_columns"].append(f"{table}.{col}")
        for name, ddl in REQUIRED_INDEXES.items():
            conn.execute(sa_text(ddl))
            result["created_indexes"].append(name)
        # 约束迁移：v1 建表时 raw_financial_payload.company_id 为 NOT NULL，
        # 而落盘发生在公司建档之前 → 需要重建该表（SQLite 不能改已有列约束）
        exists = conn.execute(sa_text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_financial_payload'"
        )).first()
        if exists:
            info = {r[1]: r for r in
                    conn.execute(sa_text("PRAGMA table_info(raw_financial_payload)"))}
            company_id_col = info.get("company_id")
            if company_id_col is not None and company_id_col[3] == 1:   # notnull=1
                keep = [c for c in _RAW_TABLE_COLUMNS if c in info]
                conn.execute(sa_text(
                    "ALTER TABLE raw_financial_payload RENAME TO raw_financial_payload_old"))
                conn.execute(sa_text(_RAW_TABLE_DDL))
                cols = ", ".join(keep)
                conn.execute(sa_text(
                    f"INSERT INTO raw_financial_payload ({cols}) "
                    f"SELECT {cols} FROM raw_financial_payload_old"))
                conn.execute(sa_text("DROP TABLE raw_financial_payload_old"))
                result["rebuilt_tables"].append("raw_financial_payload")
        # 2026-09-26 数据源切换东财三表后，原新浪专用口径修正逻辑（operate_cost→
        # total_operating_cost / 删 derived gross_profit）已移除：东财 OPERATE_COST
        # 是真营业成本，gross_profit 由公式派生为正确值，不应再被启动时删除。
        for table in LEGACY_TABLES:
            row = conn.execute(sa_text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
            ), {"t": table}).first()
            if not row:
                continue
            n = conn.execute(sa_text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
            if n == 0:
                conn.execute(sa_text(f"DROP TABLE {table}"))
                result["dropped_tables"].append(table)
            else:
                logger.warning(f"遗留表 {table} 非空（{n} 行），保留不删")
    if (result["added_columns"] or result["dropped_tables"]
            or result["rebuilt_tables"] or any(
                not x.endswith(": 0") for x in result["data_fixes"])):
        logger.info(
            f"数据库迁移：补列 {len(result['added_columns'])} / "
            f"补索引 {len(result['created_indexes'])} / "
            f"重建表 {result['rebuilt_tables']} / 清理遗留表 {result['dropped_tables']}"
        )
    return result


def init_tables(engine: Engine) -> dict:
    """建表（幂等）+ 迁移既有库"""
    Base.metadata.create_all(engine)
    return migrate(engine)

# ---- M0 扩展：分析必需的三表明细科目（与 models.FinancialCore 列一一对应）----
_CORE_EXTRA_COLUMNS: dict[str, str] = {
    "interest_bearing_debt": "FLOAT",
    "short_term_loan": "FLOAT",
    "long_term_loan": "FLOAT",
    "bonds_payable": "FLOAT",
    "lease_liab": "FLOAT",
    "noncurrent_liab_due_1y": "FLOAT",
    "notes_payable": "FLOAT",
    "contract_liab": "FLOAT",
    "advance_receipts": "FLOAT",
    "notes_receivable": "FLOAT",
    "prepayments": "FLOAT",
    "construction_in_progress": "FLOAT",
    "intangible_asset": "FLOAT",
    "right_of_use_asset": "FLOAT",
    "long_term_equity_invest": "FLOAT",
    "total_non_current_assets": "FLOAT",
    "total_non_current_liab": "FLOAT",
    "minority_interest": "FLOAT",
    "deferred_income": "FLOAT",
    "research_expense": "FLOAT",
    "tax_surcharge": "FLOAT",
    "investment_income": "FLOAT",
    "fair_value_change": "FLOAT",
    "asset_impairment": "FLOAT",
    "credit_impairment": "FLOAT",
    "other_income": "FLOAT",
    "non_operating_income": "FLOAT",
    "non_operating_expense": "FLOAT",
    "cash_from_sales": "FLOAT",
    "cash_for_goods": "FLOAT",
    "cash_for_staff": "FLOAT",
    "cash_for_tax": "FLOAT",
    "cash_from_borrowing": "FLOAT",
    "cash_for_debt_repay": "FLOAT",
    "cash_for_dividend": "FLOAT",
    "cash_begin": "FLOAT",
    "cash_end": "FLOAT",
}
REQUIRED_COLUMNS["financial_core"].update(_CORE_EXTRA_COLUMNS)
