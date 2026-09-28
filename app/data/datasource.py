
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger

from app.data.repository import get_db, normalize_code, SOURCE_EM

# 统一期次窗口：只保留最近 N 年（东财三表返回全历史，由 build_reports() 本地裁剪）
PERIOD_WINDOW_YEARS = 10
DEFAULT_START_YEAR = str(datetime.now().year - PERIOD_WINDOW_YEARS)


@runtime_checkable
class DataSourceAdapter(Protocol):
    """数据源适配器协议（可替换 akshare/tushare/手工导入）"""

    def list_companies(self) -> list[dict]:
        """返回 [{code, name}]，code 为统一 6 位代码（不含后缀）"""
        ...

    def fetch_indicators(self, code: str) -> list[dict]:
        """返回按报告期的指标列表：

        [{
            "period": "2024Q3", "period_end": "2024-09-30",
            "period_type": "Q3", "is_single_quarter": 0,
            "metrics": {"revenue": 1.23e11, "gross_margin": 0.2531, ...},
            "sources": {"revenue": "eastmoney", ...},
            "conflicts": [],
        }]

        所有值已归一化（金额=元、比率=小数）；指标 code 与 metric_dict 对齐
        """
        ...


# ---------------------------------------------------------------------------
# 东财三表英文列 → metric_code 映射表
# ---------------------------------------------------------------------------
# 约定：
#   - 键 = 东财返回的英文大写列名（科目列 / _YOY 同比列）
#   - 值 = metric_dict.metric_code
#   - 金额科目列：东财单位即为元，入库原样
#   - `_YOY` 列：东财给的是百分数（如 5.7459 表示 5.7459%），入库 ÷100 存小数
#   - 未列出的科目（货币资金/流动负债/固定资产…）暂不映射，
#     待 ③ formula 引擎统一评估新增指标后补充（cash_ratio 等派生需要）
#
# 实测依据（2026-09-23，600519 茅台三表 103 行≈10 年，见会话探测记录）：
#   - 资产负债表 158 科目；TOTAL_ASSETS/LIABILITIES/EQUITY/INVENTORY/GOODWILL 全命中
#   - 利润表 99 科目；TOTAL_OPERATE_INCOME/OPERATE_COST/NETPROFIT/
#     PARENT_NETPROFIT/DEDUCT_PARENT_NETPROFIT 全命中；OPERATE_COST=94.7亿为真营业成本
#   - 现金流量表 125 科目；NETCASH_OPERATE/INVEST/FINANCE 命中；FREE_CASH_FLOW 缺失（公式派生）

# 资产负债表（科目余额）
EM_BALANCE_MAP = {
    "TOTAL_ASSETS": "total_assets",            # 总资产（元）
    "TOTAL_LIABILITIES": "total_liabilities",  # 总负债（元）
    "TOTAL_EQUITY": "total_equity",            # 净资产=股东权益合计（含少数股东，元）
    "INVENTORY": "inventory",                  # 存货（元）
    "NOTE_ACCOUNTS_RECE": "receivable",        # 应收票据及应收账款（元；字典别名口径）
    "GOODWILL": "goodwill",                    # 商誉（元）
    # 2026-09-26 ③ formula 支撑科目（东财确有其列，供严格派生；值==metric_code）
    "MONETARYFUNDS": "monetary_funds",              # 货币资金（元）→ cash_ratio 分子
    "TOTAL_CURRENT_ASSETS": "total_current_assets",  # 流动资产合计（元）→ current_ratio/quick_ratio
    "TOTAL_CURRENT_LIAB": "total_current_liab",      # 流动负债合计（元）→ current/quick/cash_ratio 分母
    "TOTAL_PARENT_EQUITY": "total_parent_equity",    # 归母净资产（元）→ roe 分母
    "NOTE_ACCOUNTS_PAYABLE": "accounts_payable",     # 应付票据及应付账款（元）→ payable_turnover 分母
    "FIXED_ASSET": "fixed_asset",                    # 固定资产（元）→ fixed_asset_turnover 分母
    # 同比（百分数 → ÷100 存小数）
    "TOTAL_ASSETS_YOY": "total_assets_yoy",    # 总资产同比
    "TOTAL_EQUITY_YOY": "equity_yoy",          # 净资产同比
}

# 利润表（累计口径）
EM_PROFIT_MAP = {
    "TOTAL_OPERATE_INCOME": "revenue",             # 营业总收入（元）
    "OPERATE_COST": "operate_cost",                # 营业成本（元，真口径，非营业总成本）
    "NETPROFIT": "net_profit",                     # 净利润（含少数股东，元）
    "PARENT_NETPROFIT": "net_profit_parent",       # 归母净利润（元）
    "DEDUCT_PARENT_NETPROFIT": "net_profit_deducted",  # 扣非归母净利润（元）
    "TOTAL_OPERATE_COST": "total_operating_cost",  # 营业总成本（元，含税金及附加与期间费用）
    # 2026-09-26 ③ formula 支撑科目（东财确有其列，供严格派生）
    "OPERATE_PROFIT": "operating_profit",          # 营业利润（元）→ operating_margin 分子
    "INCOME_TAX": "income_tax",                    # 所得税费用（元）→ effective_tax_rate 分子
    "TOTAL_PROFIT": "total_profit",                # 利润总额（元）→ effective_tax_rate 分母
    "FINANCE_EXPENSE": "finance_expense",          # 财务费用（元）→ 三费分析（原生科目）
    "SALE_EXPENSE": "sale_expense",                # 销售费用（元）→ 三费分析（原生科目）
    "MANAGE_EXPENSE": "manage_expense",            # 管理费用（元）→ 三费分析（原生科目）
    "BASIC_EPS": "eps",                            # 基本每股收益（元/股，接口直给）
    # 同比（百分数 → ÷100 存小数）
    "TOTAL_OPERATE_INCOME_YOY": "revenue_yoy",     # 营收同比
    "PARENT_NETPROFIT_YOY": "net_profit_yoy",      # 归母净利润同比（字典语义=归属母公司）
    # 银行别名（2026-09-26）：银行利润表无 TOTAL_OPERATE_INCOME，
    # 营业收入列名为 OPERATE_INCOME、营业支出为 OPERATE_EXPENSE；
    # 因 _em_rows 首值优先，工商企业不受影响（TOTAL_* 优先）
    "OPERATE_INCOME": "revenue",
    "OPERATE_INCOME_YOY": "revenue_yoy",
    "OPERATE_EXPENSE": "total_operating_cost",
}

# 现金流量表（累计口径）
EM_CASHFLOW_MAP = {
    "NETCASH_OPERATE": "operating_cash_flow",   # 经营活动现金流净额（元）
    "NETCASH_INVEST": "investing_cash_flow",    # 投资活动现金流净额（元）
    "NETCASH_FINANCE": "financing_cash_flow",   # 筹资活动现金流净额（元）
    "CONSTRUCT_LONG_ASSET": "capex",            # 资本开支=购建固定资产/无形资产等支付的现金（元）
    # 同比：三表科目 _YOY 无对应 metric_code（operating_cash_flow_yoy 等未入字典），暂不映射
}


def _em_symbol(code: str) -> str:
    """6 位裸代码 → 东财接口所需的交易所前缀代码（6=沪 SH / 0、3=深 SZ / 4、8=北 BJ）"""
    code = normalize_code(code)
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    return f"BJ{code}"


def _period_from_end(period_end: str) -> tuple[str, str]:
    """'2024-03-31' 或 '20240331' → ('2024Q1', 'Q1')；'2024-12-31' → ('2024FY', 'FY')"""
    end = str(period_end).strip()
    if len(end) == 8 and end.isdigit():  # '20260630'
        end = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    parts = end.split("-")
    if len(parts) != 3:
        raise ValueError(f"无法解析报告期: {period_end!r}")
    y, m, _d = parts
    mm = int(m)
    if mm == 12:
        return f"{y}FY", "FY"
    if mm == 6:
        return f"{y}H1", "H1"
    if mm == 3:
        return f"{y}Q1", "Q1"
    if mm == 9:
        return f"{y}Q3", "Q3"
    return f"{y}Q{mm // 3 + 1}", f"Q{mm // 3 + 1}"


def _normalize_end(value) -> str | None:
    """报告期列/值 → 'YYYY-MM-DD'；无法解析返回 None"""
    s = str(value).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _is_na(v) -> bool:
    if v is None:
        return True
    try:
        return v != v                          # NaN
    except Exception:
        return False


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _frame_payload(df) -> dict:
    """DataFrame → 可 JSON 落盘的 {columns, rows}（原始响应，不做任何变换）"""
    if df is None:
        return {"columns": [], "rows": []}
    try:
        columns = [str(c) for c in df.columns]
        rows = [
            {str(c): (None if _is_na(v) else _jsonable(v)) for c, v in rec.items()}
            for rec in df.to_dict(orient="records")
        ]
        return {"columns": columns, "rows": rows}
    except Exception as e:
        logger.warning(f"原始 DataFrame 序列化失败: {e}")
        return {"columns": [], "rows": []}


def _to_frame(payload) -> list[dict]:
    """{columns, rows} / 行列表 / DataFrame → 行字典列表（回放无需真 DataFrame）"""
    if payload is None:
        return []
    if isinstance(payload, dict):
        return payload.get("rows") or []
    if isinstance(payload, list):
        return payload
    try:
        return [dict(r) for r in payload.to_dict(orient="records")]
    except Exception:
        return []


class AkshareEMAdapter:
    """akshare 东方财富三表实现（单源：资产负债表 + 利润表 + 现金流量表）"""

    def __init__(self, delay: float = 0.4, raw_dir: Path | None = None):
        self.delay = delay                     # 请求间隔（秒），限频保护
        default_raw = Path(__file__).resolve().parent.parent.parent / "storage" / "raw"
        self.raw_dir = Path(raw_dir) if raw_dir else default_raw
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._last_raw: dict | None = None     # 最近一次 fetch 的原始响应（供 save_raw）

    # ---- 数据源协议实现 ----

    def list_companies(self) -> list[dict]:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        return [{"code": normalize_code(r["code"]), "name": str(r["name"]).strip()}
                for _, r in df.iterrows()]

    def fetch_indicators(self, code: str, start_year: str | None = None) -> list[dict]:
        """东财三表拉取 → 归一化 → 报告期列表（并缓存原始响应）

        start_year 参数保留仅为兼容旧调用方（东财接口不支持，忽略）。
        """
        import akshare as ak
        code = normalize_code(code)
        symbol = _em_symbol(code)

        balance = profit = cashflow = None
        for name, fn in [
            ("资产负债表", ak.stock_balance_sheet_by_report_em),
            ("利润表", ak.stock_profit_sheet_by_report_em),
            ("现金流量表", ak.stock_cash_flow_sheet_by_report_em),
        ]:
            time.sleep(self.delay)
            try:
                df = fn(symbol=symbol)
                if name == "资产负债表":
                    balance = df
                elif name == "利润表":
                    profit = df
                else:
                    cashflow = df
                logger.info(f"[{code}] {name} 拉取成功: {df.shape}")
            except Exception as e:
                logger.warning(f"[{code}] {name} 拉取失败: {type(e).__name__}: {str(e)[:160]}")

        # 原始响应（v3）：写入前不做任何变换，可离线回放
        self._last_raw = {
            "version": 3,
            "code": code,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "params": {"symbol": symbol},
            "em_balance": _frame_payload(balance),
            "em_profit": _frame_payload(profit),
            "em_cashflow": _frame_payload(cashflow),
        }

        reports, conflicts = self.build_reports(balance, profit, cashflow)
        if conflicts:
            logger.warning(f"[{code}] 发现 {len(conflicts)} 处偏差（单源方案不应出现，请检查映射表）")
        return reports

    # ---- 归一化 ----

    def _em_rows(self, payload, col_map: dict[str, str]) -> dict[str, dict[str, float]]:
        """单张东财表 → {period_end: {metric_code: value}}

        - 行 = 报告期（REPORT_DATE），列 = 科目 / _YOY
        - 金额列原样（东财单位为元）；_YOY 列 ÷100 存小数
        - 只映射 col_map 声明的列，其余忽略
        """
        rows: dict[str, dict[str, float]] = {}
        for rec in _to_frame(payload):
            end = _normalize_end(rec.get("REPORT_DATE", ""))
            if end is None:
                continue
            for col, code in col_map.items():
                if col not in rec:
                    continue
                value = rec.get(col)
                if _is_na(value):
                    continue
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    continue
                if col.endswith("_YOY"):       # 东财同比列为百分数 → 小数
                    v = v / 100.0
                # 首值优先：工商 TOTAL_OPERATE_INCOME 优先于银行 OPERATE_INCOME 别名，
                # 后者仅作银行 fallback（银行利润表无 TOTAL_OPERATE_INCOME 列）
                rows.setdefault(end, {}).setdefault(code, v)
        return rows

    def build_reports(self, balance=None, profit=None, cashflow=None,
                      window_years: int | None = None) -> tuple[list[dict], list[dict]]:
        """三表原始数据 → (报告期列表, 冲突列表)；不含网络调用，可离线测试/回放

        单源方案：冲突列表恒为空（口径归一由映射表 + formula 承担）。
        """
        merged: dict[str, dict[str, float]] = {}
        for payload, col_map in [
            (balance, EM_BALANCE_MAP),
            (profit, EM_PROFIT_MAP),
            (cashflow, EM_CASHFLOW_MAP),
        ]:
            for end, metrics in self._em_rows(payload, col_map).items():
                merged.setdefault(end, {}).update(metrics)

        reports = []
        for end in sorted(merged):
            metrics = merged[end]
            period, ptype = _period_from_end(end)
            reports.append({
                "period": period,
                "period_end": end,
                "period_type": ptype,
                "is_single_quarter": 0,        # 东财三表均为累计口径
                "metrics": metrics,
                "sources": {c: SOURCE_EM for c in metrics},
                "conflicts": [],
            })

        years = PERIOD_WINDOW_YEARS if window_years is None else window_years
        if years:
            cutoff = datetime.now().year - years
            kept = [r for r in reports if int(str(r["period"])[:4]) >= cutoff]
            if len(kept) != len(reports):
                logger.info(
                    f"期次窗口（近 {years} 年，≥ {cutoff}）：丢弃 {len(reports) - len(kept)} 期，"
                    f"保留 {len(kept)} 期"
                )
            reports = kept
        return reports, []

    # ---- 原始落盘（v3：原始响应，可回放）----

    def save_raw(self, code: str, payload: list[dict] | None = None) -> Path:
        """原始响应落盘 storage/raw/<code>.json（只增不改）+ 登记 raw_financial_payload

        payload 保留用于兼容既有调用方；若本次 fetch 已缓存原始响应，则优先落盘**原始响应**（v3）。
        """
        code = normalize_code(code)
        if self._last_raw and self._last_raw.get("code") == code:
            raw = self._last_raw
        else:
            raw = {
                "version": 1,
                "code": code,
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reports": payload or [],
            }
        text = json.dumps(raw, ensure_ascii=False, indent=1)
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path = self.raw_dir / f"{code}.json"
        path.write_text(text, encoding="utf-8")
        try:
            get_db().record_raw_payload(
                code, str(path), checksum=checksum,
                api_name=("stock_balance_sheet_by_report_em+"
                          "stock_profit_sheet_by_report_em+"
                          "stock_cash_flow_sheet_by_report_em"),
                params=raw.get("params") or {}, fetched_at=raw.get("fetched_at", ""),
            )
        except Exception as e:
            logger.warning(f"[{code}] 原始响应登记失败（文件已落盘）: {e}")
        return path

    def load_raw(self, code: str) -> dict:
        """读取落盘原始响应（v1 历史文件自动降级为 {'version': 1, 'reports': [...]})"""
        path = self.raw_dir / f"{normalize_code(code)}.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {"version": 1, "code": normalize_code(code), "reports": data}
        return data

    def replay(self, code: str, raw: dict | None = None) -> dict:
        """离线回放：按**当前映射规则**重新归一化原始响应并入库（方案 §3 第 3 条）

        v1/v2 文件（历史遗留）无法按东财映射重算，明确报错而非给出错误结果。
        """
        code = normalize_code(code)
        raw = raw if raw is not None else self.load_raw(code)
        if not raw:
            raise FileNotFoundError(f"未找到 raw 落盘: {self.raw_dir / (code + '.json')}")
        version = int(raw.get("version", 1))
        if version < 3:
            raise ValueError(
                f"raw v{version} 为新浪旧方案（或仅存归一化结果），无法按东财映射回放；"
                f"请重新拉取以生成 v3 原始响应"
            )
        reports, conflicts = self.build_reports(
            raw.get("em_balance"), raw.get("em_profit"), raw.get("em_cashflow"))
        db = get_db()
        comp = db.get_company(code=code)
        if not comp:
            raise ValueError(f"公司未建档: {code}（回放不负责建档，避免猜名称）")
        fetched_at = raw.get("fetched_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        n_rep = n_met = 0
        for r in reports:
            rid = db.upsert_financial_period(
                comp["company_id"], r["period"], period_type=r["period_type"],
                is_single_quarter=r["is_single_quarter"], period_end=r["period_end"],
                source="akshare", fetched_at=fetched_at,
            )
            n_met += db.upsert_metrics(rid, r["metrics"], sources=r.get("sources") or {})
            db.derive_financial_core(rid)
            n_rep += 1
        logger.info(
            f"[{code}] 回放完成：{n_rep} 报告期 / {n_met} 指标"
        )
        return {"code": code, "reports": n_rep, "metrics": n_met, "conflicts": len(conflicts)}


def get_adapter() -> AkshareEMAdapter:
    return AkshareEMAdapter()


if __name__ == "__main__":
    # 用法：
    #   python -m app.data.datasource replay 300750   # 按当前映射离线回放
    #   python -m app.data.datasource raw 300750      # 查看落盘原始响应的结构版本
    import sys

    argv = sys.argv[1:]
    adapter = get_adapter()
    if not argv:
        print("用法：python -m app.data.datasource replay <code> [<code> ...] | raw <code>")
        raise SystemExit(0)
    cmd, codes = argv[0], argv[1:]
    if cmd == "replay":
        for c in codes:
            try:
                print(c, adapter.replay(c))
            except Exception as e:
                print(f"{c} 回放失败: {type(e).__name__}: {e}")
    elif cmd == "raw":
        for c in codes:
            raw = adapter.load_raw(c)
            print(f"{c}: version={raw.get('version')} "
                  f"em_balance={len((raw.get('em_balance') or {}).get('rows', []))} 行 "
                  f"em_profit={len((raw.get('em_profit') or {}).get('rows', []))} 行 "
                  f"em_cashflow={len((raw.get('em_cashflow') or {}).get('rows', []))} 行")
    else:
        print(f"未知命令: {cmd}")
