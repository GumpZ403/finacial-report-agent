"""financial 领域：财报数据（A 股，东财三表单一数据源）

- models.py      SQLAlchemy 表模型（9 张业务表 + 质量日志）
- repository.py  SQLite 仓储（唯一数据入口；宽表镜像 / 公式派生 / 质量日志 / 统计）
- scope.py       行业模板适用范围（financial 模板下 N/A 指标显式声明）
- quality.py     L0 数据质量校验（回算一致性 / 同比可复现 / 单位一致 / 长宽表一致 / 完整性）
- textify.py     结构化数据 → 可读文本片段（供 embedding / RAG）
- formatting.py  指标值按 unit_type 渲染（亿元 / % / 倍 / 天 / 元/股）
- ingest/        外部数据源适配（akshare 东财三表 + 原始响应落盘 + 离线回放）
- tools/         注册给 LLM 的工具（query_company / query_financials / query_data_stats）

口径仲裁（方案 §4.3.6）：长表是唯一真相，宽表是镜像；源值优先，回算只校验不覆盖。

本文件只做轻量再导出（不 import textify —— 那会把 ChromaDB 拉进 import 期）。
"""
from app.domains.financial.models import Base
from app.domains.financial.repository import Database, get_db, normalize_code
from app.domains.financial.scope import is_applicable, normalize_template

__all__ = [
    "Base", "Database", "get_db", "normalize_code",
    "is_applicable", "normalize_template",
]
