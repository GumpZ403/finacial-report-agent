"""
全局配置管理
- 从环境变量 / .env 文件加载
- 所有模块通过 get_settings() 获取单例配置

路径约定（2026-10 重构后）：
    仓库根 D:/A_projects/finacial-report-agent
      ├── backend/          Python 代码（本文件所在）
      ├── frontend/         Vue3 + Vite
      ├── storage/          运行时数据（SQLite / Chroma / raw / cache）
      ├── reports/          Agent 导出的报告
      └── logs/
这里算出的是**仓库根**；所有相对路径配置（DB / Chroma）都按仓库根解析，
与进程 cwd 无关——从仓库根启动 uvicorn 或从 backend/ 启动都不会写错目录。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings

# backend/app/core/config.py -> backend/ -> <repo root>
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_ROOT.parent

# 加载 .env（项目根目录）
load_dotenv(_PROJECT_ROOT / ".env")


def _to_absolute(path_str: str, base: Path) -> str:
    """相对路径按 base 解析为绝对路径；已是绝对路径则原样返回"""
    if not path_str:
        return path_str
    p = Path(path_str).expanduser()
    return str(p) if p.is_absolute() else str((base / p).resolve())


def _absolutize_sqlite_url(url: str, base: Path) -> str:
    """把相对 sqlite URL 绝对化

    sqlite:///./storage/financial.db -> sqlite:///D:/proj/storage/financial.db
    绝对形式（sqlite:////abs 或 sqlite:///D:/...）原样返回。
    """
    prefix = "sqlite:///"
    if not url or not url.startswith(prefix):
        return url
    raw = url[len(prefix):]
    if Path(raw).is_absolute():
        return url
    return f"{prefix}{(base / raw).resolve().as_posix()}"


class Settings(BaseSettings):
    """系统全局配置"""

    # ---- LLM ----
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "sk-demo")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

    # ---- Embedding ----
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "models/bge-base-zh-v1.5")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "768"))
    RERANKER_MODEL: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")

    # ---- 数据库（相对路径按仓库根解析，见 _absolutize_paths）----
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./storage/financial.db")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./storage/chroma_db")

    # ---- RAG ----
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "20"))
    RERANK_TOP_N: int = int(os.getenv("RERANK_TOP_N", "5"))
    HYBRID_ALPHA: float = float(os.getenv("HYBRID_ALPHA", "0.5"))
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))

    # ---- 期次窗口（唯一来源：ingest 入库裁剪与 prune 清理都读它）----
    PERIOD_WINDOW_YEARS: int = int(os.getenv("PERIOD_WINDOW_YEARS", "10"))

    # ---- 服务 ----
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @model_validator(mode="after")
    def _absolutize_paths(self) -> "Settings":
        """相对路径统一按仓库根解析，避免 cwd 不同导致数据写错位置"""
        self.DATABASE_URL = _absolutize_sqlite_url(self.DATABASE_URL, _PROJECT_ROOT)
        self.CHROMA_PERSIST_DIR = _to_absolute(self.CHROMA_PERSIST_DIR, _PROJECT_ROOT)
        return self

    # ---- 目录 ----
    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    @property
    def backend_root(self) -> Path:
        return _BACKEND_ROOT

    @property
    def storage_dir(self) -> Path:
        p = _PROJECT_ROOT / "storage"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reports_dir(self) -> Path:
        p = _PROJECT_ROOT / "reports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def frontend_dist_dir(self) -> Path:
        return _PROJECT_ROOT / "frontend" / "dist"

    @property
    def conversations_db_path(self) -> Path:
        """会话记忆库（独立于财报数据）

        为什么不和 financial.db 合库：财报数据是权威数据，要备份/迁移/可重建；
        对话历史是一次性运行状态，丢了不心疼。两者写频率差异也大（每轮几行 vs 批量导入），
        SQLite 写是串行的，分库可避免互相等锁。
        """
        return self.storage_dir / "conversations.db"


# 单例
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
