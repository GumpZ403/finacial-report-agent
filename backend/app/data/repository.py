"""
数据访问层（Repository）

- `Database` 管理器：引擎 / 会话 / 建表 + 幂等迁移（补列、补索引、清理遗留表）
- 财报 CRUD：公司、报告期、指标长表、核心宽表镜像、原始响应登记、质量日志
- 口径仲裁（方案 v1.1 §4.3.6）：**源值优先，回算只做校验不覆盖，派生物仅填补空缺**
- 所有计算在 Python/SQL 完成（不交给 LLM）

分层约定：数据访问只走本模块；textify / quality 等数据层内部模块也通过这里取数。
"""
from __future__ import annotations

import ast
import operator
from datetime import datetime

from loguru import logger
from sqlalchemy import create_engine, func, text as sa_text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings
from app.data.metric_scope import normalize_template
from app.data.schema import (
    Base,
    CompanyMaster, FinancialPeriod, FinancialMetric, FinancialCore,
    MetricDict, AnomalyRule, RawFinancialPayload, DataQualityLog,
)

# ---------- 口径常量（方案 §4.2 / §4.3）----------

# 单位由 metric_dict.unit_type 推导：金额归一为元，比率存小数故标 ratio 而非 %
UNIT_BY_TYPE: dict[str, str] = {
    "amount": "CNY",
    "ratio": "ratio",
    "times": "times",
    "days": "days",
    "per_share": "per_share",
    "count": "count",
}
UNIT_UNKNOWN = "unknown"

# 取值来源
SOURCE_EM = "eastmoney"                 # 东方财富三表（资产负债表/利润表/现金流量表）
SOURCE_SINA_SUMMARY = "sina_summary"   # 新浪财务摘要（已退役，常量保留防历史引用报错）
SOURCE_SINA_GUIDE = "sina_guide"       # 新浪财务指标（已退役，常量保留防历史引用报错）
SOURCE_DERIVED = "derived"
SOURCE_MANUAL = "manual"

# 遗留（舆情版）空表：迁移时清理，避免 describe_schema / NL2SQL 看到无关表
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


def normalize_code(code: str | int | None) -> str:
    """股票代码归一化：去后缀/空白 → 6 位裸代码（库内唯一口径）

    '300750.SZ' / '300750' / 300750 → '300750'
    """
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)
    return s


# ---------- 派生公式求值（方案 §5.1 formula_engine 的数据层实现）----------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_UNSAFE_NAMES = {"eval", "exec", "open", "compile", "__import__", "globals", "locals"}
_SAFE_CALLS = {"abs": abs, "min": min, "max": max, "round": round}


class _FormulaEvaluator(ast.NodeVisitor):
    """只允许四则运算与白名单函数的公式求值（不用裸 eval）"""

    def __init__(self, values: dict[str, float | None]):
        self.values = values

    def eval(self, expr: str):
        tree = ast.parse(expr, mode="eval")
        return self.visit(tree.body)

    def visit_Constant(self, node: ast.Constant):  # type: ignore[override]
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"公式含非法常量: {node.value!r}")

    def visit_Name(self, node: ast.Name):  # type: ignore[override]
        if node.id in _UNSAFE_NAMES:
            raise ValueError(f"公式含禁用名称: {node.id}")
        value = self.values.get(node.id)
        if value is None:
            raise KeyError(node.id)
        return float(value)

    def visit_BinOp(self, node: ast.BinOp):  # type: ignore[override]
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"公式含不支持运算符: {type(node.op).__name__}")
        return op(self.visit(node.left), self.visit(node.right))

    def visit_UnaryOp(self, node: ast.UnaryOp):  # type: ignore[override]
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"公式含不支持一元运算符: {type(node.op).__name__}")
        return op(self.visit(node.operand))

    def visit_Call(self, node: ast.Call):  # type: ignore[override]
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLS:
            raise ValueError("公式只允许 abs/min/max/round")
        return _SAFE_CALLS[node.func.id](*[self.visit(a) for a in node.args])

    def generic_visit(self, node):  # type: ignore[override]
        raise ValueError(f"公式含不支持语法: {type(node).__name__}")


def safe_eval_formula(expr: str, values: dict[str, float | None]) -> float | None:
    """按 metric_dict.formula 求值；依赖缺失/除零/非法语法 → None（不写占位值）"""
    if not expr or not expr.strip():
        return None
    try:
        val = _FormulaEvaluator(values).eval(expr)
    except KeyError:
        return None                                   # 依赖指标缺失
    except ZeroDivisionError:
        return None                                   # 分母为 0
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        logger.warning(f"派生公式无法求值（已跳过）: {expr!r} → {e}")
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return f


class Database:
    """数据库管理器（单例）"""

    # 迁移：为既有库补列（SQLite 无法给已存在的表补约束，故只补列/索引）
    _REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
        "financial_metrics": {"source": "VARCHAR(20) DEFAULT ''"},
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
    _REQUIRED_INDEXES: dict[str, str] = {
        "ix_metrics_metric_code":
            "CREATE INDEX IF NOT EXISTS ix_metrics_metric_code ON financial_metrics(metric_code)",
        "ix_metrics_source":
            "CREATE INDEX IF NOT EXISTS ix_metrics_source ON financial_metrics(source)",
    }

    def __init__(self):
        self.settings = get_settings()
        self.engine = create_engine(
            self.settings.DATABASE_URL,
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in self.settings.DATABASE_URL else {},
        )
        self._SessionLocal = sessionmaker(bind=self.engine, autoflush=False)
        self._metric_meta_cache: dict[str, dict] | None = None
        self._init_tables()

    def _init_tables(self):
        """建表（幂等）+ 迁移既有库"""
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> dict:
        """幂等迁移：补列 → 补索引 → 清理遗留空表"""
        result: dict[str, list[str]] = {
            "added_columns": [], "created_indexes": [],
            "dropped_tables": [], "rebuilt_tables": [], "data_fixes": [],
        }
        with self.engine.begin() as conn:
            for table, columns in self._REQUIRED_COLUMNS.items():
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
            for name, ddl in self._REQUIRED_INDEXES.items():
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

    def get_session(self) -> Session:
        """获取一个新的数据库会话"""
        return self._SessionLocal()

    # ---- 数据概览（财报域）----

    def get_stats(self) -> dict:
        """聚合统计：公司数 / 报告期数 / 指标条数 / 指标字典数 / 行业分布 / 来源分布"""
        with self.get_session() as session:
            companies = session.query(CompanyMaster).count()
            reports = session.query(FinancialPeriod).count()
            metrics = session.query(FinancialMetric).count()
            dicts = session.query(MetricDict).count()
            industries = (
                session.query(CompanyMaster.industry_l1,
                              func.count(CompanyMaster.company_id))
                .group_by(CompanyMaster.industry_l1).all()
            )
            sources = (
                session.query(FinancialMetric.source, func.count(FinancialMetric.period_id))
                .group_by(FinancialMetric.source).all()
            )
            return {
                "company_count": companies,
                "report_count": reports,
                "metric_count": metrics,
                "metric_dict_count": dicts,
                "industry_distribution": {k: v for k, v in industries},
                "metric_source_distribution": {(k or "unknown"): v for k, v in sources},
            }

    # ============ 财报 CRUD（方案 v1.1 §4）============

    def upsert_company(self, code: str, name: str, **kwargs) -> int:
        """按归一化 code 幂等写入公司主表，返回 company_id"""
        code = normalize_code(code)
        with self.get_session() as session:
            comp = session.query(CompanyMaster).filter_by(code=code).first()
            if comp is None:
                comp = CompanyMaster(code=code, name=name)
                session.add(comp)
                session.flush()
            comp.name = name or comp.name
            for k, v in kwargs.items():
                if hasattr(comp, k) and v not in (None, ""):
                    if k == "industry_template":
                        v = normalize_template(v)
                    setattr(comp, k, v)
            session.commit()
            return comp.company_id

    def get_company(self, code: str | None = None, company_id: int | None = None,
                    name: str | None = None) -> dict | None:
        """按 code/id/名称查公司（返回 dict，含 industry_template）"""
        with self.get_session() as session:
            q = session.query(CompanyMaster)
            if code:
                raw = str(code).strip()
                q = q.filter((CompanyMaster.code == normalize_code(raw))
                             | (CompanyMaster.name == raw))
            elif company_id:
                q = q.filter(CompanyMaster.company_id == company_id)
            elif name:
                q = q.filter(CompanyMaster.name == name)
            c = q.first()
            if not c:
                return None
            return {col.name: getattr(c, col.name) for col in c.__table__.columns}

    def list_company_codes(self) -> list[str]:
        """全部公司代码（升序）"""
        with self.get_session() as session:
            return [r[0] for r in session.query(CompanyMaster.code)
                    .order_by(CompanyMaster.code).all()]

    def upsert_financial_period(self, company_id: int, period: str,
                                period_type: str = "FY",
                                is_single_quarter: int = 0,
                                report_scope: str = "consolidated",
                                **kwargs) -> int:
        """按 (company_id, period, report_scope) 幂等写入报告期，返回 period_id"""
        with self.get_session() as session:
            r = session.query(FinancialPeriod).filter_by(
                company_id=company_id, period=period, report_scope=report_scope
            ).first()
            if r is None:
                r = FinancialPeriod(
                    company_id=company_id, period=period,
                    period_type=period_type, is_single_quarter=is_single_quarter,
                    report_scope=report_scope,
                )
                session.add(r)
                session.flush()
            r.period_type = period_type
            r.is_single_quarter = is_single_quarter
            for k, v in kwargs.items():
                if hasattr(r, k) and v not in (None, ""):
                    setattr(r, k, v)
            session.commit()
            return r.period_id

    def upsert_metrics(self, period_id: int, metrics: dict[str, float],
                       unit: str | None = None, is_estimated: int = 0,
                       sources: dict[str, str] | str | None = None) -> int:
        """批量写入指标长表（幂等 upsert），返回写入条数

        Args:
            unit: **已废弃**。单位一律由 metric_dict.unit_type 推导（方案 §4.2），
                  保留该参数仅为兼容既有调用方；传值会被忽略并告警。
            is_estimated: 1=非源数据（公式派生填充）或回算校验未通过
            sources: 取值来源，dict{metric_code: source} 或统一字符串
        """
        if not metrics:
            return 0
        if unit is not None:
            logger.warning(
                "upsert_metrics(unit=...) 已废弃：单位由 metric_dict.unit_type 推导，"
                "该参数已被忽略"
            )
        meta = self._metric_meta()
        src_map: dict[str, str] = sources if isinstance(sources, dict) else {}
        src_default = sources if isinstance(sources, str) else ""
        n = 0
        with self.get_session() as session:
            for code, value in metrics.items():
                if value is None:
                    continue
                row = session.query(FinancialMetric).filter_by(
                    period_id=period_id, metric_code=code
                ).first()
                if row is None:
                    row = FinancialMetric(period_id=period_id, metric_code=code)
                    session.add(row)
                row.value = float(value)
                row.unit = self._unit_for(code, meta)
                row.is_estimated = int(is_estimated)
                row.source = src_map.get(code) or src_default or (row.source or "")
                n += 1
            session.commit()
        return n

    def get_metrics(self, period_id: int) -> dict[str, dict]:
        """取一个报告期的全部指标: {metric_code: {value, unit, is_estimated, source}}"""
        with self.get_session() as session:
            rows = session.query(FinancialMetric).filter_by(period_id=period_id).all()
            return {m.metric_code: {
                "value": m.value, "unit": m.unit,
                "is_estimated": m.is_estimated, "source": m.source or "",
            } for m in rows}

    def derive_financial_core(self, period_id: int) -> int:
        """长表 → 宽表镜像（幂等）

        口径仲裁（方案 §4.3.6）：
        1. 按 metric_dict.formula 计算**缺失**指标并写入长表
           （source=derived, is_estimated=1）；已有源值的指标**不覆盖**，
           回算只做校验（见 app/data/quality.py）。
        2. 宽表逐列镜像长表当前值（长表没有的一律置 NULL），不再自行计算。
        """
        metrics = self.get_metrics(period_id)
        if not metrics:
            return 0
        derived = self._compute_derived(metrics)
        if derived:
            self.upsert_metrics(period_id, derived, is_estimated=1,
                                sources=SOURCE_DERIVED)
            metrics = self.get_metrics(period_id)
        with self.get_session() as session:
            r = session.get(FinancialPeriod, period_id)
            if not r:
                return 0
            core = session.get(FinancialCore, period_id)
            if core is None:
                core = FinancialCore(period_id=period_id)
                session.add(core)
            core.company_id = r.company_id
            core.period = r.period
            for col in FinancialCore.__table__.columns.keys():
                if col in ("period_id", "company_id", "period"):
                    continue
                entry = metrics.get(col)
                value = entry["value"] if entry and entry.get("value") is not None else None
                setattr(core, col, value)
            session.commit()
        return len(metrics)

    def rebuild_financial_core(self, company_code: str | None = None) -> dict:
        """全量（或指定公司）重算宽表——宽表是派生物，任何时候都能重建"""
        with self.get_session() as session:
            q = session.query(FinancialPeriod.period_id)
            if company_code:
                comp = session.query(CompanyMaster).filter_by(
                    code=normalize_code(company_code)).first()
                if not comp:
                    logger.warning(f"公司不存在: {company_code}")
                    return {"reports": 0, "metrics": 0}
                q = q.filter(FinancialPeriod.company_id == comp.company_id)
            period_ids = [r[0] for r in q.order_by(FinancialPeriod.period_id).all()]
        total = 0
        for i, rid in enumerate(period_ids, 1):
            total += self.derive_financial_core(rid)
            if i % 200 == 0:
                logger.info(f"宽表重算进度 {i}/{len(period_ids)}")
        logger.info(f"宽表重算完成：{len(period_ids)} 报告期 / {total} 指标")
        return {"reports": len(period_ids), "metrics": total}

    def prune_out_of_window(self, years: int = 10, dry_run: bool = False) -> dict:
        """清理期次窗口外的历史数据（方案 §3 第 7 条；2026-09-22 决定统一为近十年）

        - 删除对象：`period` 年份早于 (当前年 − years) 的报告期，及其指标行与宽表行
        - 窗口外数据在 `storage/raw/<code>.json` 里仍有留痕，可用 `replay()` 回放重建
        - 幂等：重复执行只会匹配到"还没被清掉的越界期"，无越界期时返回 0
        """
        cutoff = datetime.now().year - years
        with self.get_session() as session:
            ids = [r[0] for r in session.execute(sa_text(
                "SELECT period_id FROM financial_periods "
                "WHERE CAST(substr(period, 1, 4) AS INTEGER) < :c"
            ), {"c": cutoff}).all()]
            if not ids:
                logger.info(f"期次窗口清理：无越界数据（窗口 ≥ {cutoff} 年）")
                return {"cutoff_year": cutoff, "reports": 0, "metrics": 0, "core": 0}
            if dry_run:
                n_m = session.execute(sa_text(
                    "SELECT COUNT(*) FROM financial_metrics WHERE period_id IN ("
                    "  SELECT period_id FROM financial_periods "
                    "  WHERE CAST(substr(period, 1, 4) AS INTEGER) < :c)"
                ), {"c": cutoff}).scalar()
                return {"cutoff_year": cutoff, "reports": len(ids),
                        "metrics": int(n_m or 0), "core": None, "dry_run": True}
            n_m = n_c = 0
            for i in range(0, len(ids), 500):          # 分块，避开 SQLite 变量上限
                chunk = ids[i:i + 500]
                ph = ",".join(f":p{k}" for k in range(len(chunk)))
                params = {f"p{k}": v for k, v in enumerate(chunk)}
                n_m += session.execute(sa_text(
                    f"DELETE FROM financial_metrics WHERE period_id IN ({ph})"), params).rowcount or 0
                n_c += session.execute(sa_text(
                    f"DELETE FROM financial_core WHERE period_id IN ({ph})"), params).rowcount or 0
                session.execute(sa_text(
                    f"DELETE FROM financial_periods WHERE period_id IN ({ph})"), params)
            session.commit()
        logger.warning(
            f"期次窗口清理（< {cutoff} 年）：删除报告期 {len(ids)} / "
            f"指标 {n_m} / 宽表 {n_c}；窗口外原始数据仍可在 storage/raw/ 回放"
        )
        return {"cutoff_year": cutoff, "reports": len(ids), "metrics": n_m, "core": n_c}

    # ---- 数据层内部读取（供 textify / quality 使用）----

    def list_core_rows(self, company_code: str) -> list[dict]:
        """某公司全部报告期的宽表行（含口径字段）"""
        code = normalize_code(company_code)
        cols = [c.name for c in FinancialCore.__table__.columns]
        with self.get_session() as session:
            comp = session.query(CompanyMaster).filter_by(code=code).first()
            if not comp:
                return []
            rows = (
                session.query(FinancialCore, FinancialPeriod.period_type,
                              FinancialPeriod.is_single_quarter,
                              FinancialPeriod.report_scope)
                .join(FinancialPeriod, FinancialPeriod.period_id == FinancialCore.period_id)
                .filter(FinancialCore.company_id == comp.company_id)
                .all()
            )
            out = []
            for core, ptype, single, scope in rows:
                d = {c: getattr(core, c) for c in cols}
                d["period_type"] = ptype or "FY"
                d["is_single_quarter"] = single or 0
                d["report_scope"] = scope or "consolidated"
                out.append(d)
            return out

    def list_metric_dict(self) -> dict[str, dict]:
        """指标字典 {metric_code: {...}}（语义来源：中文名/单位类型/公式/适用范围）"""
        with self.get_session() as session:
            return {
                m.metric_code: {
                    "metric_code": m.metric_code,
                    "name_cn": m.name_cn,
                    "unit_type": m.unit_type,
                    "formula": m.formula or "",
                    "industry_scope": m.industry_scope or "general",
                    "deprecated": int(m.deprecated or 0),
                }
                for m in session.query(MetricDict).all()
            }

    def iter_metric_rows(self, company_code: str | None = None) -> list[dict]:
        """指标长表行（供质检/回放使用），含来源与估值标记

        返回 [{period_id, company_id, period, period_type, metric_code,
                value, unit, is_estimated, source}]
        """
        with self.get_session() as session:
            q = session.query(
                FinancialMetric.period_id, FinancialPeriod.company_id,
                FinancialPeriod.period, FinancialPeriod.period_type,
                FinancialMetric.metric_code, FinancialMetric.value,
                FinancialMetric.unit, FinancialMetric.is_estimated,
                FinancialMetric.source,
            ).join(FinancialPeriod, FinancialPeriod.period_id == FinancialMetric.period_id)
            if company_code:
                comp = session.query(CompanyMaster).filter_by(
                    code=normalize_code(company_code)).first()
                if not comp:
                    return []
                q = q.filter(FinancialPeriod.company_id == comp.company_id)
            return [{
                "period_id": r[0], "company_id": r[1], "period": r[2],
                "period_type": r[3] or "FY", "metric_code": r[4], "value": r[5],
                "unit": r[6] or "", "is_estimated": int(r[7] or 0), "source": r[8] or "",
            } for r in q.all()]

    def mark_metrics_estimated(self, keys: list[tuple[int, str]]) -> int:
        """把指定 (period_id, metric_code) 标记 is_estimated=1（回算校验未通过，方案 §4.3.5）"""
        if not keys:
            return 0
        n = 0
        with self.get_session() as session:
            for period_id, metric_code in keys:
                row = session.query(FinancialMetric).filter_by(
                    period_id=period_id, metric_code=metric_code).first()
                if row is not None and not row.is_estimated:
                    row.is_estimated = 1
                    n += 1
            session.commit()
        return n

    def repair_metric_units(self) -> dict:
        """按 metric_dict.unit_type 回填/修正全库 unit（幂等）

        历史遗留行的 unit 被统一写成 CNY（含比率、次数、天数），此处离线修正，
        无需重新拉取数据（unit 由指标语义决定，与数据源无关）。
        """
        meta = self.refresh_metric_meta()
        changed = missing = total = 0
        with self.get_session() as session:
            rows = session.query(FinancialMetric).all()
            for row in rows:
                total += 1
                if row.metric_code not in meta:
                    missing += 1
                    continue
                want = self._unit_for(row.metric_code, meta)
                if row.unit != want:
                    row.unit = want
                    changed += 1
            session.commit()
        logger.info(
            f"单位回填完成：扫描 {total} 行 / 修正 {changed} 行 / "
            f"指标字典缺失 {missing} 行"
        )
        return {"scanned": total, "changed": changed, "not_in_dict": missing}

    def rescale_metric(self, metric_code: str, factor: float,
                       exclude_sources: tuple[str, ...] = (SOURCE_DERIVED,),
                       dry_run: bool = False) -> dict:
        """修正某指标历史缩放错误（value × factor），排除派生行

        用途：数据源列语义改正后回填历史值。例如 `ocf_to_net_profit` 曾按"百分数"
        误除以 100，实际新浪该列是"倍"，需 ×100 复原（方案 §4.3 单位归一）。
        修正前请用 L0 回算确认（rescaled 值应与公式回算一致）。
        """
        scanned = changed = 0
        with self.get_session() as session:
            q = session.query(FinancialMetric).filter(FinancialMetric.metric_code == metric_code)
            for row in q.all():
                scanned += 1
                if (row.source or "") in exclude_sources or row.value is None:
                    continue
                changed += 1
                if not dry_run:
                    row.value = float(row.value) * factor
            if not dry_run:
                session.commit()
        logger.info(
            f"指标 {metric_code} 缩放修正：扫描 {scanned} 行 / "
            f"{'预计修改' if dry_run else '已修改'} {changed} 行（×{factor}）"
        )
        return {"scanned": scanned, "changed": changed, "factor": factor, "dry_run": dry_run}

    def list_anomaly_rules(self, enabled_only: bool = True) -> list[dict]:
        """异常规则（方案 §4.2 anomaly_rules）；表为空时返回空列表，由调用方兜底默认阈值"""
        with self.get_session() as session:
            q = session.query(AnomalyRule)
            if enabled_only:
                q = q.filter(AnomalyRule.enabled == 1)
            return [{
                "rule_id": r.rule_id, "name": r.name, "metric_code": r.metric_code or "",
                "compare": r.compare or "qoq", "operator": r.operator or "abs_gt",
                "threshold": r.threshold, "severity_map": r.severity_map or {},
                "industry_scope": r.industry_scope or "all",
            } for r in q.all()]

    # ---- 原始响应登记（方案 §3：原始落盘可回放）----

    def record_raw_payload(self, company_code: str, payload_path: str,
                           checksum: str = "", api_name: str = "akshare",
                           params: dict | None = None,
                           fetched_at: str = "") -> int:
        """登记一份原始响应落盘文件（公司可能尚未建档，company_id 允许为空）"""
        code = normalize_code(company_code)
        with self.get_session() as session:
            comp = session.query(CompanyMaster).filter_by(code=code).first()
            row = RawFinancialPayload(
                company_id=comp.company_id if comp else None,
                company_code=code,
                source="akshare",
                api_name=api_name,
                params=params or {},
                payload_path=str(payload_path),
                checksum=checksum,
                fetched_at=fetched_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            session.add(row)
            session.commit()
            return row.payload_id

    # ---- 数据质量日志 ----

    def log_data_quality(self, period_id: int | None, metric_code: str,
                         check_name: str, level: str, detail: str) -> None:
        """写入单条数据质量日志（L0 评测）"""
        with self.get_session() as session:
            session.add(DataQualityLog(
                period_id=period_id, metric_code=metric_code,
                check_name=check_name, level=level, detail=detail,
                checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            session.commit()

    def log_quality_entries(self, entries: list[dict]) -> int:
        """批量写入质量日志（单事务，供 L0 使用）"""
        if not entries:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.get_session() as session:
            for e in entries:
                session.add(DataQualityLog(
                    period_id=e.get("period_id"),
                    metric_code=e.get("metric_code", ""),
                    check_name=e.get("check_name", ""),
                    level=e.get("level", "warn"),
                    detail=(e.get("detail", "") or "")[:2000],
                    status=e.get("status", "open"),
                    checked_at=now,
                ))
            session.commit()
        return len(entries)

    def clear_quality_log(self, check_name: str | None = None) -> int:
        """清理质量日志（默认清全部）；L0 重跑前调用，避免日志无限累积"""
        with self.get_session() as session:
            q = session.query(DataQualityLog)
            if check_name:
                q = q.filter(DataQualityLog.check_name == check_name)
            n = q.delete(synchronize_session=False)
            session.commit()
            logger.info(f"清理质量日志 {n} 条（check_name={check_name or '全部'}）")
            return n

    def quality_summary(self) -> dict:
        """质量日志汇总：按检查项 × 级别统计 + 待处理数"""
        with self.get_session() as session:
            rows = (
                session.query(DataQualityLog.check_name, DataQualityLog.level,
                              func.count(DataQualityLog.id))
                .group_by(DataQualityLog.check_name, DataQualityLog.level).all()
            )
            open_issues = (session.query(DataQualityLog)
                           .filter(DataQualityLog.status == "open",
                                   DataQualityLog.level.in_(("error", "warn"))).count())
            return {
                "by_check": [
                    {"check_name": c, "level": lv, "count": n} for c, lv, n in rows
                ],
                "open_issues": open_issues,
            }

    # ---- 内部：指标字典缓存与公式派生 ----

    def _metric_meta(self) -> dict[str, dict]:
        """指标字典缓存（首次访问加载；补种字典后调用 refresh_metric_meta 刷新）"""
        if self._metric_meta_cache is None:
            self._metric_meta_cache = self.list_metric_dict()
        return self._metric_meta_cache

    def refresh_metric_meta(self) -> dict[str, dict]:
        """强制重新加载指标字典缓存"""
        self._metric_meta_cache = None
        return self._metric_meta()

    def _unit_for(self, code: str, meta: dict[str, dict] | None = None) -> str:
        meta = meta if meta is not None else self._metric_meta()
        entry = meta.get(code)
        if not entry:
            logger.warning(
                f"指标 {code} 不在 metric_dict 中，unit 记为 {UNIT_UNKNOWN}（请补种指标字典）"
            )
            return UNIT_UNKNOWN
        return UNIT_BY_TYPE.get(entry.get("unit_type", ""), UNIT_UNKNOWN)

    def _compute_derived(self, metrics: dict[str, dict]) -> dict[str, float]:
        """按 metric_dict.formula 计算缺失指标（源值优先，不覆盖已有值）

        迭代到不动点，以支持依赖链（如 gross_profit → gross_margin）。
        """
        formulas = {c: (m.get("formula") or "").strip()
                    for c, m in self._metric_meta().items()}
        formulas = {c: f for c, f in formulas.items() if f}
        if not formulas:
            return {}
        values = {c: m["value"] for c, m in metrics.items() if m.get("value") is not None}
        derived: dict[str, float] = {}
        for _ in range(len(formulas) + 1):
            progressed = False
            for code, expr in formulas.items():
                if values.get(code) is not None:          # 源值优先
                    continue
                val = safe_eval_formula(expr, values)
                if val is None:
                    continue
                values[code] = val
                derived[code] = val
                progressed = True
            if not progressed:
                break
        return derived


# 单例
_db: Database | None = None


def get_db() -> Database:
    """获取数据库单例"""
    global _db
    if _db is None:
        _db = Database()
    return _db
