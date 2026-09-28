"""数据接入与存储层（财报版）

- datasource:   外部数据源拉取与归一化（东财三表 + 原始响应 v3 落盘 + 离线回放）
- schema:       SQLAlchemy 表模型（方案 §4.2）
- repository:   SQLite 存取（Database 单例 + 财报 CRUD + 宽表镜像 + 公式派生 + 质量日志）
- metric_scope: 行业指标模板适用范围（financial 模板下 N/A 指标的显式声明）
- textify:      结构化数据 → 可读文本片段（overview / anomaly / trend，供 embedding / RAG）
- quality:      L0 数据质量校验（回算一致性 / 同比可复现 / 单位一致 / 长表宽表一致 / 完整性）
- vector_db:    ChromaDB 向量库封装（financial_chunks）

口径仲裁（方案 §4.3.6）：长表是唯一真相，宽表是镜像；源值优先，回算只校验不覆盖。
"""
from app.data.schema import Base
from app.data.repository import Database, get_db
from app.data.datasource import DataSourceAdapter, AkshareEMAdapter, get_adapter
from app.data.metric_scope import (
    is_applicable, not_applicable_metrics, normalize_template,
)
from app.data.quality import run_l0
from app.data.textify import (
    textify_period, textify_company, textify_all, textify_and_index,
)
from app.data.vector_db import VectorStore, get_vector_store

__all__ = [
    "Base", "Database", "get_db",
    "DataSourceAdapter", "AkshareEMAdapter", "get_adapter",
    "is_applicable", "not_applicable_metrics", "normalize_template",
    "run_l0",
    "textify_period", "textify_company", "textify_all", "textify_and_index",
    "VectorStore", "get_vector_store",
]
