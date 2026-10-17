"""东方财富三表采集（akshare 客户端）

- `DataSourceAdapter` 协议 + `AkshareEMAdapter` 实现
- 逐表记录成功/失败（FetchResult.status）→ 三表部分失败必须可见（写入 fetch_status）
- 限频（±20% 抖动）+ 指数退避重试；永久性错误立即放弃
- 原始响应 gzip 快照落盘（storage/raw/<code>/<时间戳>-<checksum8>.json.gz，只增不改）+ 离线回放

映射与归一化在 mapping.py；想换客户端库（tushare 等）只改本文件，不该动映射。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger

from app.domains.financial.fiscal_calendar import years_ago
from app.domains.financial.ingest.mapping import (
    EM_BALANCE_MAP, EM_CASHFLOW_MAP, EM_PROFIT_MAP,
    _em_symbol, _is_na, _jsonable, _normalize_end, _period_from_end, em_rows,
)
from app.domains.financial.repository import SOURCE_EM, get_db, normalize_code
from app.platform.settings import get_settings

# 期次窗口唯一来源：Settings.PERIOD_WINDOW_YEARS（ingest 裁剪与 prune 清理共用）
# 三表的固定 key：status 字典与 fetch_status 字符串都用它，顺序稳定便于比对
STATEMENT_KEYS = ("balance", "profit", "cashflow")


@dataclass
class FetchResult:
    """一次采集的结果：报告期 + 逐表成功状态

    为什么要这个类型：三表是三个独立请求。任一失败时若只写"能拿到的部分"，
    库里就会出现"某期缺 30 个科目"，且与"公司本来没有这些科目"无法区分——
    报告会因此把"网络失败"当成"公司没有这项资产"。
    partial=True 时调用方必须显式标注（financial_periods.fetch_status）。
    """

    code: str
    reports: list[dict]
    status: dict[str, str]
    fetched_at: str

    @property
    def failed(self) -> list[str]:
        return [k for k in STATEMENT_KEYS if self.status.get(k) != "ok"]

    @property
    def partial(self) -> bool:
        return bool(self.failed)

    @property
    def fetch_status(self) -> str:
        """落库值：ok / partial:balance / partial:balance,cashflow"""
        return "ok" if not self.partial else "partial:" + ",".join(self.failed)


@runtime_checkable
class DataSourceAdapter(Protocol):
    """数据源适配器协议（可替换 akshare/tushare/手工导入）"""

    def list_companies(self) -> list[dict]:
        """返回 [{code, name}]，code 为统一 6 位代码（不含后缀）"""
        ...

    def fetch_indicators(self, code: str) -> "FetchResult":
        """返回按报告期的指标列表（含逐表采集状态，见 FetchResult）：

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

@runtime_checkable
class DataSourceAdapter(Protocol):
    """数据源适配器协议（可替换 akshare/tushare/手工导入）"""

    def list_companies(self) -> list[dict]:
        """返回 [{code, name}]，code 为统一 6 位代码（不含后缀）"""
        ...

    def fetch_indicators(self, code: str) -> "FetchResult":
        """返回按报告期的指标列表（含逐表采集状态，见 FetchResult）：

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

_PERMANENT_ERRORS = (ImportError, AttributeError, TypeError, KeyError, NotImplementedError)


def _retryable(exc: BaseException) -> bool:
    """是否值得重试（网络抖动、限频、超时等一律重试）"""
    return not isinstance(exc, _PERMANENT_ERRORS)


def _write_raw(path: Path, text: str) -> None:
    """原始快照一律 gzip：JSON 缩进后的压缩比约 8~12 倍"""
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _read_raw(path: Path) -> dict:
    """读快照；.json.gz 与历史的 .json 都支持"""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def _compact_ts(ts: str) -> str:
    """'2026-10-17 16:44:30' → '20261017-164430'（文件名用，字典序即时间序）"""
    digits = "".join(ch for ch in str(ts or "") if ch.isdigit())
    if len(digits) >= 14:
        return f"{digits[:8]}-{digits[8:14]}"
    return datetime.now().strftime("%Y%m%d-%H%M%S")

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

class AkshareEMAdapter:
    """akshare 东方财富三表实现（单源：资产负债表 + 利润表 + 现金流量表）"""

    def __init__(self, delay: float = 0.4, raw_dir: Path | None = None,
                 max_attempts: int = 3, backoff_base: float = 1.0):
        self.delay = delay                     # 请求间隔（秒），限频保护（实际带 ±20% 抖动）
        self.max_attempts = max(1, int(max_attempts))    # 单表最多尝试次数
        self.backoff_base = max(0.0, float(backoff_base))  # 退避基数：1s → 2s → 4s
        # 走 settings：此前用 __file__ 相对层级推算，重构后漂移到
        # backend/app/domains/storage/raw（仓库根的 storage/raw 永远收不到数据）
        default_raw = get_settings().storage_dir / "raw"
        self.raw_dir = Path(raw_dir) if raw_dir else default_raw
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._last_raw: dict | None = None     # 最近一次 fetch 的原始响应（供 save_raw）

    # ---- 数据源协议实现 ----

    def list_companies(self) -> list[dict]:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        return [{"code": normalize_code(r["code"]), "name": str(r["name"]).strip()}
                for _, r in df.iterrows()]

    def fetch_indicators(self, code: str) -> FetchResult:
        """东财三表拉取 → 归一化 → FetchResult（并缓存原始响应）

        逐表记录成功/失败：任一表失败即 partial，由调用方写入 fetch_status。
        东财接口不支持按年份过滤（原 start_year 参数是死参数，已删），
        窗口裁剪仍由 build_reports() 本地完成。
        """
        import akshare as ak
        code = normalize_code(code)
        symbol = _em_symbol(code)
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        frames: dict[str, object] = {}
        status: dict[str, str] = {k: "failed" for k in STATEMENT_KEYS}
        for name, key, fn in [
            ("资产负债表", "balance", ak.stock_balance_sheet_by_report_em),
            ("利润表", "profit", ak.stock_profit_sheet_by_report_em),
            ("现金流量表", "cashflow", ak.stock_cash_flow_sheet_by_report_em),
        ]:
            df = self._fetch_statement(code, name, fn, symbol)
            if df is None:
                continue
            frames[key] = df
            status[key] = "ok"

        # 原始响应（v3）：写入前不做任何变换，可离线回放。
        # fetch_status 一并落盘 → 回放时能复现"这份快照本来就不全"
        self._last_raw = {
            "version": 3,
            "code": code,
            "fetched_at": fetched_at,
            "params": {"symbol": symbol, "fetch_status": dict(status)},
            "em_balance": _frame_payload(frames.get("balance")),
            "em_profit": _frame_payload(frames.get("profit")),
            "em_cashflow": _frame_payload(frames.get("cashflow")),
        }

        reports, conflicts = self.build_reports(
            frames.get("balance"), frames.get("profit"), frames.get("cashflow"))
        if conflicts:
            logger.warning(f"[{code}] 发现 {len(conflicts)} 处偏差（单源方案不应出现，请检查映射表）")
        result = FetchResult(code=code, reports=reports, status=status, fetched_at=fetched_at)
        if result.partial:
            logger.error(f"[{code}] 采集不完整（{result.fetch_status}）：缺失的表不会写入空科目")
        return result

    # ---- 采集（限频 + 重试退避）----

    def _sleep_before_request(self) -> None:
        """请求前限频；带 ±20% 抖动，避免固定节奏被风控识别"""
        if self.delay > 0:
            time.sleep(self.delay * random.uniform(0.8, 1.2))

    def _fetch_statement(self, code: str, name: str, fn, symbol: str):
        """单表拉取：最多 max_attempts 次，指数退避；永久性错误立即放弃

        为什么不用 tenacity 装饰器：重试次数/退避要在实例上可配置，
        测试里 backoff_base=0 就能完全不睡觉地验证尝试次数。
        """
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._sleep_before_request()
            try:
                df = fn(symbol=symbol)
            except Exception as e:
                last = e
                if not _retryable(e):
                    logger.warning(
                        f"[{code}] {name} 不可重试错误（{type(e).__name__}），放弃: {str(e)[:120]}")
                    return None
                if attempt >= self.max_attempts:
                    break
                wait = self.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    f"[{code}] {name} 第 {attempt}/{self.max_attempts} 次失败"
                    f"（{type(e).__name__}），{wait:.1f}s 后重试")
                if wait > 0:
                    time.sleep(wait)
                continue
            if df is None or len(df) == 0:
                logger.warning(f"[{code}] {name} 返回空表（按失败处理）")
                return None
            logger.info(f"[{code}] {name} 拉取成功: {df.shape}"
                        + (f"（第 {attempt} 次尝试）" if attempt > 1 else ""))
            return df
        logger.warning(
            f"[{code}] {name} 连续 {self.max_attempts} 次失败: "
            f"{type(last).__name__}: {str(last)[:120]}")
        return None

    # ---- 归一化 ----

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
            for end, metrics in em_rows(payload, col_map).items():
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

        # 窗口按**日期**判定（period_end ≥ 今天往前推 N 年），不按日历年：
        # 按年份比大小的话，每年 1 月窗口会退化成"9 年出头"且随月份浮动
        years = get_settings().PERIOD_WINDOW_YEARS if window_years is None else window_years
        if years:
            cutoff = years_ago(years).isoformat()
            kept = [r for r in reports if (r.get("period_end") or "") >= cutoff]
            if len(kept) != len(reports):
                logger.info(
                    f"期次窗口（近 {years} 年，period_end ≥ {cutoff}）："
                    f"丢弃 {len(reports) - len(kept)} 期，保留 {len(kept)} 期"
                )
            reports = kept
        return reports, []

    # ---- 原始落盘（v3：原始响应，可回放）----

    def save_raw(self, code: str, payload: list[dict] | None = None) -> Path:
        """原始响应落盘 storage/raw/<code>/<时间戳>-<checksum8>.json.gz（只增不改，gzip）

        此前写的是固定路径 raw/<code>.json，每次拉取**覆盖**——财报重述后
        "上个月看到的营收"永久丢失，与"只增不改"的承诺相反。
        现在文件名带内容 checksum：同一份内容重复拉取复用已有快照（不产生新文件），
        内容变了必然是新文件 → 历史版本天然保留。

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
        path, need_write = self._snapshot_path(code, checksum, raw.get("fetched_at", ""))
        if need_write:
            _write_raw(path, text)
            logger.info(
                f"[{code}] raw 落盘: {path.parent.name}/{path.name}"
                f"（{path.stat().st_size} B，未压缩 {len(text)} B）")
        else:
            logger.info(f"[{code}] raw 内容与既有快照一致，复用: {path.name}")
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

    def _snapshot_path(self, code: str, checksum: str,
                       fetched_at: str) -> tuple[Path, bool]:
        """快照路径；已有同 checksum 的快照则复用（返回 False 表示无需写盘）"""
        d = self.raw_dir / code
        d.mkdir(parents=True, exist_ok=True)
        existed = sorted(d.glob(f"*-{checksum[:8]}.json*"))
        if existed:
            return existed[-1], False
        return d / f"{_compact_ts(fetched_at)}-{checksum[:8]}.json.gz", True

    def load_raw(self, code: str) -> dict:
        """读取落盘原始响应：取最新快照，兼容 v1 时代的 raw/<code>.json"""
        code = normalize_code(code)
        snapshot_dir = self.raw_dir / code
        if snapshot_dir.is_dir():
            files = sorted(snapshot_dir.glob("*.json*"))
            if files:
                return _read_raw(files[-1])
        path = self.raw_dir / f"{code}.json"
        if not path.exists():
            return {}
        data = _read_raw(path)
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
        # 快照自带采集完整性：缺表的快照回放后仍然是 partial（不臆造数据）
        raw_status = (raw.get("params") or {}).get("fetch_status") or {}
        failed = [k for k in STATEMENT_KEYS if raw_status.get(k) != "ok"] if raw_status else []
        fetch_status = "ok" if not failed else "partial:" + ",".join(failed)
        if fetch_status != "ok":
            logger.warning(f"[{code}] 回放的是不完整快照：{fetch_status}")
        n_rep = n_met = 0
        for r in reports:
            rid = db.upsert_financial_period(
                comp["company_id"], r["period"], period_type=r["period_type"],
                is_single_quarter=r["is_single_quarter"], period_end=r["period_end"],
                source="akshare", fetched_at=fetched_at, fetch_status=fetch_status,
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
    #   python -m app.domains.financial.ingest.eastmoney replay 300750   # 按当前映射离线回放
    #   python -m app.domains.financial.ingest.eastmoney raw 300750      # 查看落盘原始响应的结构版本
    import sys

    argv = sys.argv[1:]
    adapter = get_adapter()
    if not argv:
        print("用法：python -m app.domains.financial.ingest.eastmoney replay <code> [<code> ...] | raw <code>")
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
