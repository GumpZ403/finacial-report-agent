"""
FastAPI 主入口
- 挂载 Agent 对话路由（统一对话入口，意图 + 工具调度）
- 配置 CORS
- 提供健康检查接口
- 托管 Web 界面与报告下载目录
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.core.config import get_settings
from app.api.agent import router as agent_router
# 工具层：显式导入以触发 _register() 自动注册（否则 uvicorn 启动后 LLM 看不到任何工具）
import app.tools.financial_tools  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动初始化 + 关闭清理"""
    settings = get_settings()
    logger.info(f"启动财报解读 Agent v{__import__('app').__version__}")
    logger.info(f"LLM 模型: {settings.LLM_MODEL}")
    logger.info(f"Embedding 模型: {settings.EMBEDDING_MODEL}")
    logger.info("数据层就绪（SQLite + ChromaDB）")

    yield

    logger.info("系统关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    settings = get_settings()

    app = FastAPI(
        title="财报解读 Agent",
        description="财报数据检索、解读与研究辅助系统",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载路由（API 只保留对话入口 + 健康检查）
    app.include_router(agent_router)

    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "financial-report-agent", "version": "1.0.0"}

    # 静态资源（Web 界面）
    _static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    # 报告导出目录（Agent 生成的 .md/.docx 经 /reports/<文件名> 下载）
    _reports_dir = Path(__file__).resolve().parent.parent / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/reports", StaticFiles(directory=str(_reports_dir)), name="reports")

    # 根路径：返回 Web 界面
    @app.get("/")
    async def root():
        return FileResponse(str(_static_dir / "index.html"))

    # API 摘要（供 /docs 与调试）
    @app.get("/api")
    async def api_index():
        return {
            "service": "财报解读 Agent",
            "version": "1.0.0",
            "endpoints": {
                "Agent对话": "/api/agent/chat",
                "流式对话": "/api/agent/chat/stream",
                "记忆": "/api/agent/memory",
                "清空记忆": "/api/agent/clear",
            },
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )
