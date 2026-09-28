"""
全局配置管理
- 从环境变量 / .env 文件加载
- 所有模块通过 get_settings() 获取单例配置
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# 加载 .env（项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


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

    # ---- 数据库 ----
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./storage/financial.db")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./storage/chroma_db")

    # ---- RAG ----
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "20"))
    RERANK_TOP_N: int = int(os.getenv("RERANK_TOP_N", "5"))
    HYBRID_ALPHA: float = float(os.getenv("HYBRID_ALPHA", "0.5"))
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))

    # ---- 标签 ----
    TAG_CONFIDENCE_THRESHOLD: float = float(os.getenv("TAG_CONFIDENCE_THRESHOLD", "0.7"))
    AUTO_EVOLVE_ENABLED: bool = os.getenv("AUTO_EVOLVE_ENABLED", "true").lower() == "true"

    # ---- 简报 ----
    REPORT_MAX_NEWS: int = int(os.getenv("REPORT_MAX_NEWS", "50"))
    REPORT_MIN_CONFIDENCE: float = float(os.getenv("REPORT_MIN_CONFIDENCE", "0.6"))

    # ---- 服务 ----
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    @property
    def storage_dir(self) -> Path:
        p = _PROJECT_ROOT / "storage"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def prompts_dir(self) -> Path:
        return _PROJECT_ROOT / "prompts"


# 单例
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
